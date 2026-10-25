"""Measured-data mode of the Electrochemistry view: browse any downloaded cell."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common import MUTED, categorical, cell_tables, discover_cells, load_cell, ramp, rate_groups, style, thin


def pick_measured():
    """Sidebar picker for a downloaded cell; returns (option, CellData) or None."""
    opts = discover_cells()
    if not opts:
        st.sidebar.warning("Nothing found under data/. See the README for where to get datasets.")
        return None
    labels = [o.label for o in opts]
    prev = st.session_state.get("_cell_label")
    label = st.sidebar.selectbox("Dataset · cell", labels, index=labels.index(prev) if prev in labels else 0)
    st.session_state["_cell_label"] = label
    opt = opts[labels.index(label)]
    try:
        return opt, load_cell(opt)
    except Exception as e:
        st.error(f"Could not load {opt.label}: {e}")
        return None


def render(opt, cell):
    cycles, steps, stepcap = cell_tables(cell, f"{opt.source}|{opt.path}|{opt.kwargs_json}")
    steps = steps.assign(rate=rate_groups(steps))
    ts = cell.timeseries
    meta = cell.metadata

    st.subheader(meta.get("cell_id") or opt.label)
    st.caption(
        " · ".join(
            str(x)
            for x in (opt.source, meta.get("chemistry"), meta.get("nominal_capacity_ah") and f"{meta['nominal_capacity_ah']} Ah nominal")
            if x
        )
    )
    issues = meta.get("data_issues", [])
    if issues:
        with st.expander(f"🛠️ The loader repaired {len(issues)} defect(s) in the source file"):
            st.markdown("\n".join(f"- {i}" for i in issues))

    # --- headline numbers -------------------------------------------------------
    dis = steps[steps["step_type"] == "discharge"]
    full = dis[dis["discharge_ah"] >= 0.5 * dis["discharge_ah"].max()] if len(dis) else dis
    c = st.columns(5)
    c[0].metric("Cycles", f"{cycles['cycle_index'].nunique():,}")
    c[1].metric("Steps", f"{len(steps):,}")
    c[2].metric("Samples", f"{len(ts):,}")
    c[3].metric("Test duration", f"{(ts['test_time_s'].iloc[-1] - ts['test_time_s'].iloc[0]) / 86400:.1f} d")
    if len(full) > 1:
        first, last = full["discharge_ah"].iloc[0], full["discharge_ah"].iloc[-1]
        c[4].metric("Discharge Ah, last vs first full step", f"{last:.3f}", f"{(last / first - 1) * 100:+.1f} %")

    tab_fade, tab_vq, tab_ts, tab_tables, tab_extras, tab_meta = st.tabs(
        ["Fade", "V–Q curves", "Time series", "Tables", "Extras", "Metadata"]
    )

    # --- fade -------------------------------------------------------------------
    with tab_fade:
        f1, f2, f3 = st.columns([1, 1, 1])
        stype = f1.radio("Step type", ["discharge", "charge"], horizontal=True, key="fade_type")
        quantities = {"Ah": f"{stype}_ah", "Wh": f"{stype}_wh", "Duration (h)": "duration_s", "Mean temperature (°C)": "temperature_mean_c"}
        metric = f2.selectbox("Quantity", list(quantities), key="fade_metric")
        hide_partial = f3.checkbox("Hide partial steps (< 20 % of max Ah)", True)
        col = quantities[metric]
        s = steps[steps["step_type"] == stype]
        if hide_partial and len(s):
            s = s[s[f"{stype}_ah"] >= 0.2 * s[f"{stype}_ah"].max()]
        y = s[col] / 3600 if col == "duration_s" else s[col]
        fig = go.Figure()
        groups = [g for g in s["rate"].value_counts().index if g != "other"][:3]
        for color, g in zip(categorical(), groups):
            m = s["rate"] == g
            fig.add_trace(
                go.Scatter(
                    x=s.loc[m, "cycle_index"], y=y[m], name=g, mode="lines+markers",
                    line=dict(width=2, color=color), marker=dict(size=8, color=color),
                    customdata=s.loc[m, ["step_id"]], hovertemplate="cycle %{x}<br>%{y:.4f}<br>step %{customdata[0]}<extra>" + g + "</extra>",
                )
            )
        if (s["rate"] == "other").any():
            m = s["rate"] == "other"
            fig.add_trace(go.Scatter(x=s.loc[m, "cycle_index"], y=y[m], name="other rates", mode="markers", marker=dict(size=8, color=MUTED)))
        st.plotly_chart(style(fig, legend_title="|mean current|").update_layout(xaxis_title="cycle", yaxis_title=metric), width="stretch")
        st.caption("Steps are grouped by their mean current, so a capacity check at a different rate is not mixed into the fade trend.")

    # --- V-Q curves -------------------------------------------------------------
    with tab_vq:
        v1, v2, v3 = st.columns([1, 1, 2])
        vtype = v1.radio("Step type", ["discharge", "charge"], horizontal=True, key="vq_type")
        cand = steps[steps["step_type"] == vtype]
        rates = [g for g in cand["rate"].value_counts().index]
        if not rates:
            st.info(f"No {vtype} steps.")
        else:
            rate = v2.selectbox("Rate group", rates, key="vq_rate")
            cand = cand[cand["rate"] == rate]
            # Longest step of that kind in each cycle.
            best = cand.loc[cand.groupby("cycle_index")[f"{vtype}_ah"].idxmax()]
            cyc_list = best["cycle_index"].tolist()
            default = sorted({cyc_list[int(i)] for i in np.linspace(0, len(cyc_list) - 1, min(6, len(cyc_list)))})
            chosen = v3.multiselect("Cycles (colour strengthens with cycle number)", cyc_list, default=default)
            x_mode = st.radio("x-axis", ["Ah moved in step", "Ah, normalised to first chosen"], horizontal=True)
            fig = go.Figure()
            sel = best[best["cycle_index"].isin(chosen)].sort_values("cycle_index")
            ref = None
            for color, (_, row) in zip(ramp(len(sel)), sel.iterrows()):
                m = (stepcap["step_id"] == row["step_id"]).to_numpy()
                q = stepcap.loc[m, "q_ah"].to_numpy()
                v = ts.loc[m, "voltage_v"].to_numpy()
                if x_mode.startswith("Ah, norm"):
                    ref = ref or q[-1]
                    q = q / ref
                d = thin(pd.DataFrame({"q": q, "v": v}), 3000)
                fig.add_trace(go.Scatter(x=d["q"], y=d["v"], name=f"cycle {row['cycle_index']}", mode="lines", line=dict(width=2, color=color)))
            st.plotly_chart(
                style(fig, 480, "cycle").update_layout(xaxis_title="Ah" if ref is None else "Q / Q(first chosen)", yaxis_title="V"),
                width="stretch",
            )
            st.caption("This view only plots the raw data. Turning these curves into dQ/dV, DVA or degradation modes is module A1, in workbench/.")

    # --- time series ------------------------------------------------------------
    with tab_ts:
        cyc_vals = sorted(ts["cycle_index"].unique())
        if len(cyc_vals) > 1:
            lo, hi = st.select_slider("Cycle range", options=cyc_vals, value=(cyc_vals[0], cyc_vals[min(2, len(cyc_vals) - 1)]))
        else:
            lo = hi = cyc_vals[0]
        w = ts[(ts["cycle_index"] >= lo) & (ts["cycle_index"] <= hi)]
        d = thin(w)
        hours = (d["test_time_s"] - d["test_time_s"].iloc[0]) / 3600
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04, subplot_titles=("Voltage (V)", "Current (A, + = charge)", "Temperature (°C)"))
        for r, colname in enumerate(["voltage_v", "current_a", "temperature_c"], start=1):
            fig.add_trace(go.Scattergl(x=hours, y=d[colname], mode="lines", line=dict(width=1.5, color=categorical()[0]), showlegend=False, name=colname), row=r, col=1)
        fig.update_xaxes(title_text="hours from start of range", row=3, col=1)
        fig.update_layout(hovermode="x unified")
        st.plotly_chart(style(fig, 640), width="stretch")
        if len(d) < len(w):
            st.caption(f"Showing {len(d):,} of {len(w):,} samples (every {len(w) // len(d)}th). Big gaps in time are recording pauses.")

    # --- tables -----------------------------------------------------------------
    with tab_tables:
        which = st.radio("Table", ["Steps", "Cycles", "Time series (first 5,000 rows of range)"], horizontal=True)
        if which == "Steps":
            types = st.multiselect("Step types", ["charge", "discharge", "rest", "dynamic"], default=["charge", "discharge"])
            df = steps[steps["step_type"].isin(types)]
        elif which == "Cycles":
            df = cycles
        else:
            df = w.head(5000)
        st.dataframe(df, width="stretch", height=480)
        st.download_button("Download CSV", df.to_csv(index=False).encode(), file_name=f"{meta.get('cell_id', 'cell')}_{which.split()[0].lower()}.csv")

    # --- extras -----------------------------------------------------------------
    with tab_extras:
        if not cell.extras:
            st.info("This source has no extra tables.")
        for name, df in cell.extras.items():
            st.subheader(name)
            if {"record", "z_real_ohm", "z_imag_ohm"} <= set(df.columns) and len(df):
                recs = sorted(df["record"].unique())
                pick = st.multiselect("Impedance records", recs, default=[recs[0], recs[len(recs) // 2], recs[-1]], key=f"eis_{name}")
                fig = go.Figure()
                for color, r in zip(ramp(len(pick)), pick):
                    e = df[df["record"] == r]
                    fig.add_trace(go.Scatter(x=e["z_real_ohm"], y=-e["z_imag_ohm"], name=f"record {r}", mode="markers", marker=dict(size=8, color=color)))
                fig.update_yaxes(scaleanchor="x", scaleratio=1)
                st.plotly_chart(style(fig, 460, "record").update_layout(xaxis_title="Re Z (Ω)", yaxis_title="−Im Z (Ω)"), width="stretch")
                st.caption("Raw Nyquist data only, as stored (points are not frequency-ordered in NASA's file). Fitting ECMs and running DRT is module A2.")
            st.dataframe(df, width="stretch", height=260)

    # --- metadata ---------------------------------------------------------------
    with tab_meta:
        st.json(meta)
