"""Algorithms view: a test bench for ECMs and SoC/SoH estimators.

A PyBaMM model plays the real cell (the "plant"). Your estimators only see
what a BMS would see: noisy, biased measurements, plus a datasheet capacity
and a fresh-cell OCV curve. The bench knows the truth and scores them.
"""

import json
from dataclasses import asdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from batterylab import bench
from batterylab.echem import CHEMISTRIES
from common import MUTED, band_fill, categorical, run_plant, scenario_key, style, thin
from views import panels
from workbench import ECMS, ESTIMATORS, discover

import batterylab as bl


def _sidebar() -> tuple[bench.Scenario, bench.Sensors]:
    sb = st.sidebar
    sb.subheader("Plant (the real cell)")
    chem = sb.selectbox("Chemistry", list(CHEMISTRIES), key="alg_chem")
    profile = sb.selectbox("Load profile", bench.PROFILES, key="alg_profile")
    duration = sb.slider("Duration (min)", 10, 120, 60, 10, key="alg_dur")
    soc0 = sb.slider("Initial SoC (true)", 0.2, 1.0, 0.9, 0.05, key="alg_soc0")
    temp = sb.slider("Ambient temperature (°C)", -10, 45, 25, 5, key="alg_temp")
    with sb.expander("Plant ageing (hidden from the estimator)"):
        lli = st.slider("LLI", 0.0, 0.3, 0.0, 0.01, key="alg_lli")
        lam_pe = st.slider("LAM_PE", 0.0, 0.3, 0.0, 0.01, key="alg_lampe")
        lam_ne = st.slider("LAM_NE", 0.0, 0.3, 0.0, 0.01, key="alg_lamne")
    sb.subheader("Sensors and initialisation")
    bias = sb.slider("Current-sensor bias (mA)", -500, 500, 0, 10, key="alg_bias")
    i_noise = sb.slider("Current noise σ (mA)", 0, 500, 0, 10, key="alg_inoise")
    v_noise = sb.slider("Voltage noise σ (mV)", 0, 20, 0, 1, key="alg_vnoise")
    t_noise = sb.slider("Temperature noise σ (°C)", 0.0, 2.0, 0.0, 0.1, key="alg_tnoise")
    soc_err = sb.slider("Initial SoC guess error", -0.3, 0.3, 0.0, 0.05, key="alg_socerr")
    s = bench.Scenario(chemistry=chem, temperature_c=float(temp), LLI=lli, LAM_PE=lam_pe, LAM_NE=lam_ne,
                       profile=profile, duration_min=float(duration), soc0=soc0)
    n = bench.Sensors(current_bias_a=bias / 1000, current_noise_a=i_noise / 1000, voltage_noise_v=v_noise / 1000,
                      temperature_noise_c=t_noise, soc0_error=soc_err)
    return s, n


@st.cache_data(max_entries=64, show_spinner=False)
def _run_est(name: str, version: float, scen_json: str, sens_json: str) -> pd.DataFrame:
    """Cached estimator run. ``version`` changes whenever workbench/ is edited."""
    plant = run_plant(scen_json)
    sens = bench.Sensors(**json.loads(sens_json))
    measured = bench.measure(plant, sens)
    return bench.run_estimator(ESTIMATORS[name], measured, bench.estimator_context(plant, sens))


def _workbench_version() -> float:
    from common import ROOT

    return max((p.stat().st_mtime for p in (ROOT / "workbench").glob("*.py")), default=0.0)


def _plant_tab(plant, measured):
    tr = plant.truth
    q_true, q_nom = plant.context["true_capacity_ah"], plant.context["nominal_capacity_ah"]
    c = st.columns(4)
    c[0].metric("True capacity", f"{q_true:.3f} Ah", f"{(q_true / q_nom - 1) * 100:+.1f} % vs datasheet", delta_color="off")
    c[1].metric("SoC swing", f"{tr['soc'].iloc[0]:.0%} → {tr['soc'].iloc[-1]:.0%}")
    c[2].metric("Voltage range", f"{tr['voltage_v'].min():.2f}–{tr['voltage_v'].max():.2f} V")
    c[3].metric("Temperature", f"{tr['temperature_c'].min():.1f}–{tr['temperature_c'].max():.1f} °C")
    if plant.context["stopped_early"]:
        st.warning("The plant reached its lower voltage cut-off before the profile ended, so the run is shorter than requested.")

    d, m = thin(tr, 6000), thin(measured, 6000)
    h, hm = d["time_s"] / 60, m["time_s"] / 60
    c1, c2 = categorical(2)
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=("Voltage (V)", "Current (A, + = charge)", "State of charge (true)", "Temperature (°C)"))
    fig.add_trace(go.Scatter(x=hm, y=m["voltage_v"], mode="lines", line=dict(width=1, color=c2), name="measured"), row=1, col=1)
    fig.add_trace(go.Scatter(x=h, y=d["voltage_v"], mode="lines", line=dict(width=2, color=c1), name="true"), row=1, col=1)
    fig.add_trace(go.Scatter(x=h, y=d["ocv_v"], mode="lines", line=dict(width=2, color=MUTED, dash="dot"), name="true OCV"), row=1, col=1)
    fig.add_trace(go.Scatter(x=hm, y=m["current_a"], mode="lines", line=dict(width=1, color=c2), showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=h, y=d["current_a"], mode="lines", line=dict(width=2, color=c1), showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=h, y=d["soc"], mode="lines", line=dict(width=2, color=c1), showlegend=False), row=3, col=1)
    fig.add_trace(go.Scatter(x=hm, y=m["temperature_c"], mode="lines", line=dict(width=1, color=c2), showlegend=False), row=4, col=1)
    fig.add_trace(go.Scatter(x=h, y=d["temperature_c"], mode="lines", line=dict(width=2, color=c1), showlegend=False), row=4, col=1)
    fig.update_xaxes(title_text="minutes", row=4, col=1)
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(style(fig, 760), width="stretch")

    with st.expander("What the estimator is told up front"):
        ctx = bench.estimator_context(plant, bench.Sensors())
        st.write({k: v for k, v in ctx.items() if k != "ocv_curve"})
        oc = ctx["ocv_curve"]
        fig = go.Figure(go.Scatter(x=oc["soc"], y=oc["ocv_v"], mode="lines", line=dict(width=2, color=c1)))
        st.plotly_chart(style(fig, 300).update_layout(title="ocv_curve (fresh cell, 25 °C)", xaxis_title="SoC", yaxis_title="OCV (V)"), width="stretch")


def _metrics_table(rows):
    df = pd.DataFrame(rows).set_index("estimator").round(3)
    df["convergence_s"] = df["convergence_s"].map(lambda v: "never" if pd.isna(v) else f"{v:.0f}")
    return df.rename(columns={"rmse_pct": "RMSE (%)", "max_abs_pct": "max |error| (%)", "final_abs_pct": "final |error| (%)",
                              "convergence_s": "time to stay within 2 % (s)"})


def _soc_tab(plant, scen, sens):
    if not ESTIMATORS:
        st.info("No estimators registered. Copy `workbench/example_coulomb_counting.py` and implement `reset`/`step` for yours.")
        return
    names = st.multiselect("Estimators (up to 3)", list(ESTIMATORS), default=list(ESTIMATORS)[:3], max_selections=3)
    if not names:
        return
    version = _workbench_version()
    tr = plant.truth
    colors = categorical(len(names))
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.6, 0.4],
                        subplot_titles=("SoC", "Error = estimate − truth (percentage points), band = ±3σ"))
    fig.add_trace(go.Scatter(x=tr["time_s"] / 60, y=tr["soc"] * 100, mode="lines", line=dict(width=3, color=MUTED), name="truth"), row=1, col=1)
    rows = []
    for name, color in zip(names, colors):
        try:
            est = _run_est(name, version, scenario_key(scen), json.dumps(asdict(sens), sort_keys=True))
        except Exception as e:
            st.error(f"{name} raised {type(e).__name__}: {e}")
            continue
        err = (est["soc"] - tr["soc"].to_numpy()) * 100
        x = est["time_s"] / 60
        fig.add_trace(go.Scatter(x=x, y=est["soc"] * 100, mode="lines", line=dict(width=2, color=color), name=name), row=1, col=1)
        if "soc_std" in est:
            band = 3 * est["soc_std"] * 100
            fig.add_trace(go.Scatter(x=np.r_[x, x[::-1]], y=np.r_[band, -band[::-1]], fill="toself", fillcolor=band_fill(),
                                     line=dict(width=0), hoverinfo="skip", showlegend=False), row=2, col=1)
        fig.add_trace(go.Scatter(x=x, y=err, mode="lines", line=dict(width=2, color=color), showlegend=False, name=name), row=2, col=1)
        rows.append({"estimator": name, **bench.soc_metrics(tr, est)})
    fig.add_hline(y=0, line=dict(color=MUTED, width=1), row=2, col=1)
    fig.update_xaxes(title_text="minutes", row=2, col=1)
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(style(fig, 640), width="stretch")
    if rows:
        st.dataframe(_metrics_table(rows), width="stretch")
        st.caption("'never' means the error was still outside ±2 % at the end. Results are cached; saving a workbench file re-runs them.")
    extra = [c for c in _run_est(names[0], version, scenario_key(scen), json.dumps(asdict(sens), sort_keys=True)).columns
             if c not in ("soc", "soc_std", "time_s")]
    if extra:
        with st.expander(f"Other states returned by {names[0]}"):
            est = _run_est(names[0], version, scenario_key(scen), json.dumps(asdict(sens), sort_keys=True))
            k = st.selectbox("State", extra)
            st.plotly_chart(style(go.Figure(go.Scatter(x=est["time_s"] / 60, y=est[k], mode="lines", line=dict(width=2, color=colors[0]))), 300)
                            .update_layout(xaxis_title="minutes", yaxis_title=k), width="stretch")


def _robustness_tab(scen, sens):
    st.markdown("Runs every registered estimator through a fixed set of stress cases built on top of the sidebar settings. "
                "This is the robustness table module B2 asks for.")
    cases = st.multiselect("Cases", list(bench.ROBUSTNESS_CASES), default=list(bench.ROBUSTNESS_CASES))
    if not ESTIMATORS:
        st.info("Register an estimator first.")
        return
    version = _workbench_version()
    key = (scenario_key(scen), json.dumps(asdict(sens), sort_keys=True), tuple(cases), tuple(ESTIMATORS), version)
    if st.button("Run robustness grid", type="primary"):
        rows, bar = [], st.progress(0.0)
        jobs = [(c, n) for c in cases for n in ESTIMATORS]
        for k, (case, name) in enumerate(jobs):
            s2, n2 = bench.robustness_case(scen, sens, bench.ROBUSTNESS_CASES[case])
            try:
                est = _run_est(name, version, scenario_key(s2), json.dumps(asdict(n2), sort_keys=True))
                m = bench.soc_metrics(run_plant(scenario_key(s2)).truth, est)
            except Exception as e:
                m = {"rmse_pct": np.nan, "max_abs_pct": np.nan, "final_abs_pct": np.nan, "convergence_s": np.nan, "error": str(e)}
            rows.append({"case": case, "estimator": name, **m})
            bar.progress((k + 1) / len(jobs))
        st.session_state["_robust"] = (key, rows)
    saved = st.session_state.get("_robust")
    if not saved:
        return
    if saved[0] != key:
        st.caption("Settings changed since this table was computed; press the button to refresh it.")
    rows = saved[1]
    df = pd.DataFrame(rows)
    metric = st.radio("Show", ["rmse_pct", "max_abs_pct", "final_abs_pct", "convergence_s"], horizontal=True)
    pivot = df.pivot(index="case", columns="estimator", values=metric)
    pivot = pivot.reindex([c for c in cases if c in pivot.index])
    st.dataframe(pivot.style.format("{:.2f}").background_gradient(cmap="Blues", axis=None), width="stretch")
    st.download_button("Download full table (CSV)", df.to_csv(index=False).encode(), file_name="robustness.csv")


def _ecm_tab(plant):
    tr = plant.truth
    st.markdown("Checks an equivalent-circuit model against the plant: same current in, compare voltage out. "
                "Register one with `@ecm(\"name\")` in `workbench/`; fitting its parameters is module B1.")
    c1, c2 = st.columns([2, 1])
    t0, t1 = c1.slider("Zoom (minutes)", 0.0, float(tr["time_s"].iloc[-1] / 60), (0.0, min(10.0, float(tr["time_s"].iloc[-1] / 60))))
    w = tr[(tr["time_s"] >= t0 * 60) & (tr["time_s"] <= t1 * 60)]
    colors = categorical(3)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        subplot_titles=("Voltage (V)", "Residual: model − plant (mV)", "Current (A)"))
    fig.add_trace(go.Scatter(x=w["time_s"] / 60, y=w["voltage_v"], mode="lines", line=dict(width=3, color=MUTED), name="plant"), row=1, col=1)
    fig.add_trace(go.Scatter(x=w["time_s"] / 60, y=w["current_a"], mode="lines", line=dict(width=2, color=MUTED), showlegend=False), row=3, col=1)
    rows = []
    ctx = {**bench.estimator_context(plant, bench.Sensors()), "soc0": float(tr["soc"].iloc[0])}
    for (name, fn), color in zip(list(ECMS.items())[:3], colors):
        try:
            v = np.asarray(fn(tr["time_s"].to_numpy(), tr["current_a"].to_numpy(), tr["temperature_c"].to_numpy(), dict(ctx)), dtype=float)
        except Exception as e:
            st.error(f"{name} raised {type(e).__name__}: {e}")
            continue
        res = (v - tr["voltage_v"].to_numpy()) * 1000
        m = w.index
        fig.add_trace(go.Scatter(x=w["time_s"] / 60, y=v[m], mode="lines", line=dict(width=2, color=color), name=name), row=1, col=1)
        fig.add_trace(go.Scatter(x=w["time_s"] / 60, y=res[m], mode="lines", line=dict(width=2, color=color), showlegend=False), row=2, col=1)
        rows.append({"model": name, "RMSE (mV)": float(np.sqrt(np.mean(res**2))), "max |residual| (mV)": float(np.max(np.abs(res)))})
    fig.update_xaxes(title_text="minutes", row=3, col=1)
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(style(fig, 680), width="stretch")
    if rows:
        st.dataframe(pd.DataFrame(rows).set_index("model").round(2), width="stretch")
    elif not ECMS:
        st.info("No ECMs registered yet. The plot shows the plant alone, which is still useful for looking at pulse responses: try the "
                "'pulse train' profile and zoom in on one pulse.")


def page():
    st.title("Algorithms")
    discover()  # (re)load estimators and ECMs from workbench/
    scen, sens = _sidebar()
    plant = run_plant(scenario_key(scen))
    measured = bench.measure(plant, sens)
    st.caption(f"Plant: {scen.chemistry} · {scen.profile} · {scen.temperature_c:g} °C · {scen.model}. "
               "Your estimators see only the measured signals and a fresh-cell datasheet.")
    tabs = st.tabs(["Plant & sensors", "SoC estimation", "Robustness", "ECM check", "Your panels (workbench)"])
    with tabs[0]:
        _plant_tab(plant, measured)
    with tabs[1]:
        _soc_tab(plant, scen, sens)
    with tabs[2]:
        _robustness_tab(scen, sens)
    with tabs[3]:
        _ecm_tab(plant)
    with tabs[4]:
        tr = plant.truth
        cell = bl.CellData(pd.DataFrame({"test_time_s": tr["time_s"], "cycle_index": 0, "current_a": measured["current_a"],
                                         "voltage_v": measured["voltage_v"], "temperature_c": measured["temperature_c"]}),
                           {"source": "bench", "cell_id": "plant (measured)", "truth": tr, "context": plant.context})
        panels.render(cell, prefixes=("B", "C"))
