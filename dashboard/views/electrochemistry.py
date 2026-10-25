"""Electrochemistry view: what happens inside a cell during a test.

Pick a chemistry, a test and conditions in the sidebar; the PyBaMM model
re-runs (results are cached). Pin up to two runs to overlay them against the
current one, then change a condition and watch what moves.
"""

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from batterylab.echem import CHEMISTRIES, FIELDS, SERIES_GROUPS, TESTS
from common import CRITICAL, MUTED, categorical, ocp_curves, run_to_celldata, simulate, style
from views import measured, panels

MAX_PINNED = 2
N_FRAMES = 60

TEST_HELP = {
    "CC discharge": "Constant-current discharge from full to the lower cut-off, then 1 h rest. The rest shows how the cell relaxes.",
    "CC-CV charge": "Constant current to the upper cut-off, constant voltage until C/50, then rest. Raise the C-rate or lower the temperature and watch the anode potential.",
    "HPPC": "Hybrid pulse power characterisation: at every 10 % SoC, a 10 s discharge pulse and a 10 s charge pulse. The instant voltage jumps and the slow relaxation are what an ECM has to capture.",
    "GITT": "Galvanostatic intermittent titration: short C/5 pulses, then long rests. Each rest relaxes to (nearly) the OCV, and the shape of the relaxation reflects solid-state diffusion.",
    "Drive cycle": "A synthetic EV-like profile with accelerations, regenerative braking and stops (charge-positive current).",
    "Relaxation after pulse": "From 50 % SoC: a short discharge pulse, then a 2 h rest. Watch the concentration gradients in the electrolyte and particles decay.",
}


# --- sidebar ----------------------------------------------------------------


def _sidebar_config() -> dict:
    sb = st.sidebar
    chem = sb.selectbox("Chemistry", list(CHEMISTRIES), help="PyBaMM parameter sets from the literature")
    sb.caption(CHEMISTRIES[chem][1])
    test = sb.selectbox("Test", TESTS)
    cfg = {"chemistry": chem, "test": test}
    if test in ("CC discharge", "CC-CV charge", "Relaxation after pulse"):
        cfg["c_rate"] = sb.select_slider("C-rate", [0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0], value=1.0)
    if test == "Drive cycle":
        cfg["drive"] = sb.selectbox("Driving style", ["urban", "highway", "aggressive"])
        cfg["drive_minutes"] = sb.slider("Duration (min)", 5, 60, 20, 5)
    cfg["temperature_c"] = float(sb.slider("Ambient temperature (°C)", -10, 45, 25, 5))
    with sb.expander("Model and ageing"):
        default_model = "SPMe" if test == "Drive cycle" else "DFN"
        models = ["DFN", "SPMe", "SPM"]
        cfg["model"] = st.selectbox(
            "Model", models, index=models.index(default_model),
            help="DFN resolves every particle across the electrode; SPMe/SPM use one representative particle per electrode and are much faster.",
        )
        cfg["thermal"] = st.checkbox("Lumped thermal model", True)
        st.caption("Prescribed degradation, as fractions of the fresh cell")
        cfg["degradation"] = {
            "LLI": st.slider("LLI: lithium inventory lost", 0.0, 0.3, 0.0, 0.01),
            "LAM_PE": st.slider("LAM_PE: cathode material lost", 0.0, 0.3, 0.0, 0.01),
            "LAM_NE": st.slider("LAM_NE: anode material lost", 0.0, 0.3, 0.0, 0.01),
        }
    return cfg


def _pins(current) -> list:
    """Sidebar controls for pinned runs. Returns [current, *pinned]."""
    pinned = st.session_state.setdefault("_pinned", [])
    sb = st.sidebar
    sb.divider()
    sb.subheader("Compare runs")
    if sb.button("📌 Pin this run", disabled=len(pinned) >= MAX_PINNED or any(p.label == current.label for p in pinned), use_container_width=True):
        pinned.append(current)
        st.rerun()
    colors = categorical(3)
    sb.markdown(f"<span style='color:{colors[0]}'>●</span> **current:** {current.label}", unsafe_allow_html=True)
    for k, p in enumerate(list(pinned)):
        c1, c2 = sb.columns([6, 1])
        c1.markdown(f"<span style='color:{colors[k + 1]}'>●</span> {p.label}", unsafe_allow_html=True)
        if c2.button("✕", key=f"unpin{k}", help="Unpin"):
            pinned.pop(k)
            st.rerun()
    if not pinned:
        sb.caption(f"Pin up to {MAX_PINNED} runs, then change a setting to compare.")
    return [current, *pinned]


# --- helpers ----------------------------------------------------------------


def _x_axis(run, mode: str) -> tuple[np.ndarray, str]:
    t = run.series.index.to_numpy()
    if mode == "Time (h)":
        return t / 3600, "time (h)"
    if mode == "Capacity (Ah)":
        q = run.series["Discharge capacity [Ah]"].to_numpy()
        return np.abs(q - q[0]), "|capacity moved| (Ah)"
    return 100 * (t - t[0]) / max(t[-1] - t[0], 1e-9), "test progress (%)"


def _frame_idx(run, n=N_FRAMES) -> np.ndarray:
    return np.unique(np.linspace(0, len(run.t) - 1, n).round().astype(int))


def _kpis(run):
    s = run.series
    v, i = s["Voltage [V]"], s["Current [A]"]
    dt = np.diff(s.index.to_numpy(), prepend=s.index[0])
    e_wh = float(np.sum(v * i * dt) / 3600)
    q = s["Discharge capacity [Ah]"]
    anode = s["Anode potential at separator vs Li [V]"]
    c = st.columns(5)
    c[0].metric("Capacity moved", f"{abs(q.iloc[-1] - q.iloc[0]):.3f} Ah", help=f"Nominal {run.meta['nominal_capacity_ah']:g} Ah")
    c[1].metric("Energy", f"{abs(e_wh):.2f} Wh", help="Positive = delivered on discharge or absorbed on charge")
    c[2].metric("Voltage range", f"{v.min():.2f}–{v.max():.2f} V", help=f"Cut-offs {run.meta['v_min']:g}–{run.meta['v_max']:g} V")
    c[3].metric("Peak temperature rise", f"{s['Cell temperature [°C]'].max() - run.config['temperature_c']:.1f} K")
    c[4].metric(
        "Min anode potential vs Li", f"{anode.min() * 1000:.0f} mV",
        delta="plating possible" if anode.min() < 0 else "no plating risk", delta_color="inverse" if anode.min() < 0 else "off",
        help="Solid–electrolyte potential difference at the anode/separator interface. Below 0 V, lithium can plate instead of intercalating.",
    )


def _animation_controls(fig, n_frames: int, bottom_px: int = 90):
    """Play/pause buttons and a progress slider under the figure."""
    pct = [f"{100 * f / (n_frames - 1):.0f} %" for f in range(n_frames)]
    fig.update_layout(
        margin=dict(b=bottom_px),
        updatemenus=[dict(type="buttons", direction="left", x=0, xanchor="left", y=0, yanchor="top", pad=dict(t=50, r=10),
                          showactive=False, buttons=[
            dict(label="▶ Play", method="animate",
                 args=[None, {"frame": {"duration": 120, "redraw": False}, "fromcurrent": True, "transition": {"duration": 0}}]),
            dict(label="⏸ Pause", method="animate", args=[[None], {"frame": {"duration": 0}, "mode": "immediate"}]),
        ])],
        sliders=[dict(active=0, x=0.16, len=0.84, xanchor="left", y=0, yanchor="top", pad=dict(t=40),
                      currentvalue=dict(prefix="test progress: ", font=dict(size=13)),
                      font=dict(color="rgba(0,0,0,0)"), ticklen=0,  # hide the 60 step labels; the current value shows above
                      steps=[dict(method="animate", label=pct[f],
                                  args=[[str(f)], {"frame": {"duration": 0, "redraw": False}, "mode": "immediate"}])
                             for f in range(n_frames)])],
    )
    return fig


# --- sections ---------------------------------------------------------------


def _response(runs, colors, xmode):
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        subplot_titles=("Terminal voltage (V)", "Current (A, + = charge)", "Cell temperature (°C)"))
    for run, color in zip(runs, colors):
        x, xlabel = _x_axis(run, xmode)
        for r, col in enumerate(["Voltage [V]", "Current [A]", "Cell temperature [°C]"], start=1):
            fig.add_trace(go.Scatter(x=x, y=run.series[col], mode="lines", line=dict(width=2, color=color),
                                     name=run.label, legendgroup=run.label, showlegend=r == 1), row=r, col=1)
    fig.update_xaxes(title_text=xlabel, row=3, col=1)
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(style(fig, 620), use_container_width=True)


def _field_ranges(runs, label):
    lo, hi = np.inf, -np.inf
    for run in runs:
        for arr in run.fields.get(label, {}).values():
            lo, hi = min(lo, np.nanmin(arr)), max(hi, np.nanmax(arr))
    pad = 0.05 * (hi - lo or 1)
    return [lo - pad, hi + pad]


def _inside(runs, colors):
    """Animated through-thickness profiles, scrubbed by % of test progress."""
    labels = [lab for lab in FIELDS if all(lab in r.fields for r in runs)]
    chosen = st.multiselect("Profiles", labels, default=labels[:4], max_selections=6)
    if not chosen:
        return
    n_rows = 1 + (len(chosen) + 1) // 2
    specs = [[{"colspan": 2}, None]] + [[{}, {}] for _ in range(n_rows - 1)]
    titles = ["Terminal voltage (V) · marker = current frame"] + chosen
    fig = make_subplots(rows=n_rows, cols=2, specs=specs, subplot_titles=titles, vertical_spacing=0.36 / n_rows, horizontal_spacing=0.08)
    idxs = [_frame_idx(r) for r in runs]

    def frame_traces(f):
        """Traces for frame f (0..N_FRAMES-1), in a fixed order."""
        out = []
        for run, color, idx in zip(runs, colors, idxs):
            k = idx[min(f, len(idx) - 1)]
            x, _ = _x_axis(run, "Test progress (%)")
            out.append(go.Scatter(x=[x[k]], y=[run.series["Voltage [V]"].iloc[k]], mode="markers",
                                  marker=dict(size=12, color=color, line=dict(width=2, color="white")), showlegend=False))
            for lab in chosen:
                for part, arr in run.fields[lab].items():
                    xs = run.x_um[part]
                    out.append(go.Scatter(x=xs, y=arr[:, k], mode="lines", line=dict(width=2, color=color), showlegend=False,
                                          hovertemplate=f"{lab}<br>x %{{x:.0f}} µm<br>%{{y:.4g}}<extra>{run.label}</extra>"))
        return out

    # Static voltage traces first (never animated), then the animated traces.
    for run, color in zip(runs, colors):
        x, _ = _x_axis(run, "Test progress (%)")
        fig.add_trace(go.Scatter(x=x, y=run.series["Voltage [V]"], mode="lines", line=dict(width=2, color=color), name=run.label), row=1, col=1)
    n_static = len(fig.data)
    positions = []
    for run in runs:
        positions.append((1, 1))
        for j, lab in enumerate(chosen):
            for _ in run.fields[lab]:
                positions.append((2 + j // 2, 1 + j % 2))
    for tr, (r, c) in zip(frame_traces(0), positions):
        fig.add_trace(tr, row=r, col=c)
    anim_ids = list(range(n_static, len(fig.data)))

    fig.frames = [go.Frame(data=frame_traces(f), traces=anim_ids, name=str(f)) for f in range(N_FRAMES)]
    for j, lab in enumerate(chosen):
        r, c = 2 + j // 2, 1 + j % 2
        fig.update_yaxes(range=_field_ranges(runs, lab), row=r, col=c)
        fig.update_xaxes(title_text="x through the cell (µm)", row=r, col=c)
        sep = runs[0].x_um
        fig.add_vrect(x0=sep["n"][-1], x1=sep["p"][0], fillcolor=MUTED, opacity=0.12, line_width=0, row=r, col=c)
    fig.update_xaxes(title_text="test progress (%)", row=1, col=1)

    st.plotly_chart(_animation_controls(style(fig, 340 * n_rows), N_FRAMES), use_container_width=True)
    st.caption("Drag the slider or press Play. Anode on the left, separator shaded, cathode on the right. "
               "Overlaid runs are matched by % of test progress, so the same slider position is the same fraction of each test.")


def _particles(runs, colors):
    keys = sorted({k for r in runs for k in r.particles})
    pick = st.selectbox("Which particle", keys, format_func=lambda k: ("Anode · " if k.startswith("n") else "Cathode · ") + k.split(":")[1])
    runs_k = [r for r in runs if pick in r.particles]
    part = pick[0]
    fig = go.Figure()
    idxs = [_frame_idx(r) for r in runs_k]

    def traces(f):
        return [go.Scatter(x=r.r_um[part] / r.r_um[part][-1], y=r.particles[pick][:, idx[min(f, len(idx) - 1)]], mode="lines",
                           line=dict(width=2, color=c), name=r.label) for r, c, idx in zip(runs_k, colors, idxs)]

    for tr in traces(0):
        fig.add_trace(tr)
    fig.frames = [go.Frame(data=traces(f), name=str(f)) for f in range(N_FRAMES)]
    lo = min(np.nanmin(r.particles[pick]) for r in runs_k)
    hi = max(np.nanmax(r.particles[pick]) for r in runs_k)
    fig.update_yaxes(range=[lo - 0.02, hi + 0.02], title_text="stoichiometry (lithium fraction)")
    fig.update_xaxes(title_text="radius r / R  (0 = centre, 1 = surface)")
    st.plotly_chart(_animation_controls(style(fig, 520), N_FRAMES), use_container_width=True)
    st.caption("A flat profile means the particle is in equilibrium; a steep one means solid diffusion is limiting. "
               "The surface value is what sets the electrode's potential.")


def _losses(runs, colors, xmode):
    run = runs[0]
    labels = [lab for lab in SERIES_GROUPS["Voltage breakdown"] if lab != "Open-circuit voltage [V]" and lab in run.series]
    x, xlabel = _x_axis(run, xmode)
    fig = go.Figure()
    for lab, color in zip(labels, categorical(6)):
        fig.add_trace(go.Scatter(x=x, y=run.series[lab].abs() * 1000, mode="lines", stackgroup="loss", line=dict(width=0.5, color=color),
                                 name=lab.replace(" [V]", "")))
    total = (run.series["Open-circuit voltage [V]"] - run.series["Voltage [V]"]).abs() * 1000
    fig.add_trace(go.Scatter(x=x, y=total, mode="lines", line=dict(width=2, color=MUTED, dash="dot"), name="|OCV − V| (check)"))
    fig.update_layout(xaxis_title=xlabel, yaxis_title="mV")
    st.plotly_chart(style(fig, 460), use_container_width=True)
    st.caption(f"Current run only ({run.label}). Each band is the size of one loss mechanism; together they explain the gap between "
               "open-circuit voltage and terminal voltage. The dotted line is that gap, computed directly, as a check.")
    if len(runs) > 1:
        lab = st.selectbox("Compare one mechanism across runs", labels)
        fig = go.Figure()
        for r, c in zip(runs, colors):
            xr, _ = _x_axis(r, xmode)
            if lab in r.series:
                fig.add_trace(go.Scatter(x=xr, y=r.series[lab].abs() * 1000, mode="lines", line=dict(width=2, color=c), name=r.label))
        st.plotly_chart(style(fig, 360).update_layout(xaxis_title=xlabel, yaxis_title=f"|{lab}| (mV)"), use_container_width=True)


def _windows(runs, colors):
    st.caption("Each electrode's open-circuit potential across its full lithium range (grey), with the part this test used "
               "(coloured, from start ○ to end ●). Ageing (LLI/LAM) slides and shrinks the used windows.")
    cols = st.columns(2)
    for col, (part, name, var) in zip(cols, [("n", "Anode", "Anode surface stoichiometry (x-avg)"), ("p", "Cathode", "Cathode surface stoichiometry (x-avg)")]):
        fig = go.Figure()
        key = f"{'negative' if part == 'n' else 'positive'}_ocp_v"
        for chem in dict.fromkeys(r.config["chemistry"] for r in runs):
            ocp = ocp_curves(chem)
            ok = ocp[key].between(-0.2, 5.0)
            fig.add_trace(go.Scatter(x=ocp["stoichiometry"][ok], y=ocp[key][ok], mode="lines", line=dict(width=2, color=MUTED),
                                     name=f"{name} OCP · {chem}"))
        # Pinned runs first, the current run last so it is drawn on top.
        for r, c in reversed(list(zip(runs, colors))):
            ocp = ocp_curves(r.config["chemistry"])
            sto = r.series[var].to_numpy()
            pot = np.interp(sto, ocp["stoichiometry"], ocp[key])
            fig.add_trace(go.Scatter(x=sto, y=pot, mode="lines", line=dict(width=5, color=c), name=r.label, opacity=0.85))
            fig.add_trace(go.Scatter(x=[sto[0], sto[-1]], y=[pot[0], pot[-1]], mode="markers", showlegend=False,
                                     marker=dict(size=10, color=["rgba(0,0,0,0)", c], line=dict(width=2, color=c))))
        fig.update_layout(title=name, xaxis_title="stoichiometry (lithium fraction)", yaxis_title="potential vs Li/Li⁺ (V)")
        col.plotly_chart(style(fig, 420), use_container_width=True)


def _heat_safety(runs, colors, xmode):
    run = runs[0]
    x, xlabel = _x_axis(run, xmode)
    c1, c2 = st.columns(2)
    heat = [lab for lab in SERIES_GROUPS["Heat"] if lab in run.series and lab != "Total heating [W]"]
    if heat:
        fig = go.Figure()
        for lab, color in zip(heat, categorical(3)):
            fig.add_trace(go.Scatter(x=x, y=run.series[lab], mode="lines", line=dict(width=2, color=color), name=lab.replace(" [W]", "")))
        fig.add_trace(go.Scatter(x=x, y=run.series["Total heating [W]"], mode="lines", line=dict(width=2, color=MUTED, dash="dot"), name="total"))
        c1.plotly_chart(style(fig, 420).update_layout(title="Heat sources (current run)", xaxis_title=xlabel, yaxis_title="W"), use_container_width=True)
    else:
        c1.info("Turn on the lumped thermal model to see heat sources.")
    fig = go.Figure()
    for r, c in zip(runs, colors):
        xr, _ = _x_axis(r, xmode)
        fig.add_trace(go.Scatter(x=xr, y=r.series["Anode potential at separator vs Li [V]"] * 1000, mode="lines", line=dict(width=2, color=c), name=r.label))
    fig.add_hline(y=0, line=dict(color=CRITICAL, width=1.5, dash="dash"), annotation_text="0 V: plating threshold", annotation_position="bottom right")
    c2.plotly_chart(style(fig, 420).update_layout(title="Anode potential at the separator", xaxis_title=xlabel, yaxis_title="mV vs Li/Li⁺"), use_container_width=True)
    st.caption("Reversible (entropic) heat can be negative: the cell cools while that reaction runs. "
               "The anode is closest to plating at the separator, where the electrolyte current is highest.")


def _all_metrics(runs, colors, xmode):
    c1, c2 = st.columns(2)
    group = c1.selectbox("Group", list(SERIES_GROUPS) + ["Safety"])
    options = ["Anode potential at separator vs Li [V]"] if group == "Safety" else [k for k in SERIES_GROUPS[group] if k in runs[0].series]
    lab = c2.selectbox("Metric", options)
    fig = go.Figure()
    for r, c in zip(runs, colors):
        if lab in r.series:
            xr, xlabel = _x_axis(r, xmode)
            fig.add_trace(go.Scatter(x=xr, y=r.series[lab], mode="lines", line=dict(width=2, color=c), name=r.label))
    st.plotly_chart(style(fig, 440).update_layout(xaxis_title=xlabel, yaxis_title=lab), use_container_width=True)
    with st.expander("Data table (current run)"):
        st.dataframe(runs[0].series, use_container_width=True, height=300)
        st.download_button("Download CSV", runs[0].series.to_csv().encode(), file_name="run.csv")


# --- page -------------------------------------------------------------------


def page():
    st.title("Electrochemistry")
    mode = st.sidebar.radio("Data", ["Simulate (PyBaMM)", "Measured datasets"], horizontal=True,
                            index=["Simulate (PyBaMM)", "Measured datasets"].index(st.session_state.get("_echem_mode", "Simulate (PyBaMM)")))
    st.session_state["_echem_mode"] = mode
    if mode == "Measured datasets":
        picked = measured.pick_measured()
        if picked:
            opt, cell = picked
            tab_data, tab_yours = st.tabs(["Data", "Your analysis (workbench)"])
            with tab_data:
                measured.render(opt, cell)
            with tab_yours:
                panels.render(cell, prefixes=("A", "C", "example", "misc"))
        return

    cfg = _sidebar_config()
    try:
        run = simulate(json.dumps(cfg, sort_keys=True))
    except Exception as e:
        st.error(f"The simulation failed for this combination: {e}")
        st.stop()
    runs = _pins(run)
    colors = categorical(len(runs))

    st.markdown(f"**{run.label}**  \n{TEST_HELP[cfg['test']]}")
    if run.meta.get("borrowed_parameters"):
        st.caption(f"This parameter set has no thermal/current-collector data; {len(run.meta['borrowed_parameters'])} values are borrowed from Chen2020.")
    _kpis(run)
    xmode = st.radio("x-axis for time plots", ["Test progress (%)", "Time (h)", "Capacity (Ah)"], horizontal=True)

    tabs = st.tabs(["Cell response", "Inside the cell", "Inside the particles", "Voltage losses", "Electrode windows",
                    "Heat & plating", "Any metric", "Your analysis (workbench)"])
    with tabs[0]:
        _response(runs, colors, xmode)
    with tabs[1]:
        _inside(runs, colors)
    with tabs[2]:
        _particles(runs, colors)
    with tabs[3]:
        _losses(runs, colors, xmode)
    with tabs[4]:
        _windows(runs, colors)
    with tabs[5]:
        _heat_safety(runs, colors, xmode)
    with tabs[6]:
        _all_metrics(runs, colors, xmode)
    with tabs[7]:
        panels.render(run_to_celldata(run), prefixes=("A", "C", "example", "misc"))
