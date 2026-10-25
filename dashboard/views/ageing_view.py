"""Ageing tab: cycle a cell with SEI growth, lithium plating and cracking switched on.

Up to two scenarios (A and B) share the same mechanisms and cycle count but
can differ in temperature and C-rates, so you can see which conditions feed
which mechanism.
"""

import json

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from batterylab.ageing import CELL, LOSSES_AH, STATES
from common import categorical, ramp, simulate_ageing, style
from glossary import card, cards

C_RATES = [0.2, 0.5, 1.0, 1.5, 2.0, 3.0]


def _scenario_inputs(name: str, defaults: dict) -> dict:
    st.markdown(f"**Scenario {name}**")
    c1, c2, c3 = st.columns(3)
    return {
        "temperature_c": float(c1.select_slider("Temperature (°C)", [-10, 0, 10, 25, 35, 45], value=defaults["temperature_c"], key=f"age_{name}_t")),
        "charge_c": c2.select_slider("Charge rate (C)", C_RATES, value=defaults["charge_c"], key=f"age_{name}_cc"),
        "discharge_c": c3.select_slider("Discharge rate (C)", C_RATES, value=defaults["discharge_c"], key=f"age_{name}_dc"),
    }


def render():
    st.caption(f"Cell: {CELL}, the only PyBaMM parameter set calibrated for all of these mechanisms together. "
               "Each cycle is a CC-CV charge, a 10 min rest, a CC discharge and a 10 min rest.")
    with st.container(border=True):
        a = _scenario_inputs("A", {"temperature_c": 25, "charge_c": 1.0, "discharge_c": 1.0})
        use_b = st.checkbox("Compare with scenario B", True, key="age_use_b")
        b = _scenario_inputs("B", {"temperature_c": 0, "charge_c": 2.0, "discharge_c": 1.0}) if use_b else None
        c1, c2, c3 = st.columns(3)
        cycles = c1.select_slider("Cycles", [25, 50, 100, 200, 400], value=100, key="age_cycles")
        accelerate = c2.select_slider("Accelerate ageing ×", [1, 3, 10, 30, 100], value=10, key="age_acc",
                                      help="Multiplies every side-reaction rate so trends show in fewer cycles. See the note below.")
        model = c3.selectbox("Model", ["SPM", "SPMe"], key="age_model", help="SPM: ~10 s per 100 cycles. SPMe: about 2-3× slower.")
        m1, m2, m3 = st.columns(3)
        mech = {
            "sei": m1.checkbox("SEI growth", True, key="age_sei"),
            "plating": m2.checkbox("Lithium plating", True, key="age_plating"),
            "cracking": m3.checkbox("Particle cracking & LAM", True, key="age_crack"),
        }
        shared = {"cycles": cycles, "accelerate": float(accelerate), "model": model, **mech}
        configs = {"A": {**shared, **a}, **({"B": {**shared, **b}} if b else {})}
        key = json.dumps(configs, sort_keys=True)
        estimate = cycles / 100 * (10 if model == "SPM" else 25) * len(configs)
        if st.button(f"Run ageing (≈{estimate:.0f} s)", type="primary"):
            runs = {}
            with st.spinner("Cycling the cell…"):
                for name, cfg in configs.items():
                    runs[name] = simulate_ageing(json.dumps(cfg, sort_keys=True))
            st.session_state["_ageing"] = (key, runs)
    saved = st.session_state.get("_ageing")
    if not saved:
        st.info("Set up the scenarios and press **Run ageing**.")
        cards(["ageing:acceleration"], cols=1)
        return
    if saved[0] != key:
        st.caption("Settings changed since this run; press **Run ageing** to update it.")
    runs = saved[1]
    names = list(runs)
    colors = dict(zip(names, categorical(len(names))))
    describe = {n: f"{n}: {r.config['temperature_c']:g} °C, charge {r.config['charge_c']:g}C, discharge {r.config['discharge_c']:g}C"
                for n, r in runs.items()}
    for n, r in runs.items():
        if r.meta["cycles_completed"] < r.config["cycles"]:
            st.warning(f"Scenario {n} stopped after {r.meta['cycles_completed']} cycles (the cell could no longer complete a cycle).")

    # 1. capacity
    fig = go.Figure()
    for n, r in runs.items():
        c = r.cycles
        fig.add_trace(go.Scatter(x=c["cycle"], y=c["discharge_capacity_ah"], mode="lines", line=dict(width=2, color=colors[n]),
                                 name=describe[n]))
    st.markdown("**Discharge capacity per cycle**")
    st.plotly_chart(style(fig, 360).update_layout(xaxis_title="cycle", yaxis_title="Ah"), use_container_width=True)
    card("ageing:capacity")

    # 2. where the lithium went (one scenario at a time) and LAM (both)
    pick = names[0]
    if len(names) > 1:
        pick = st.segmented_control("Show scenario", names, default=names[0], key="age_pick",
                                    help="The breakdown already uses colour for the mechanisms, so it shows one scenario at a time.") or names[0]
    c1, c2 = st.columns(2)
    r = runs[pick]
    fig = go.Figure()
    for (label, _), color in zip(LOSSES_AH.items(), categorical(6)[3:6]):
        col = f"Lost to {label} [Ah]"
        if col in r.cycles and r.cycles[col].notna().any():
            fig.add_trace(go.Scatter(x=r.cycles["cycle"], y=r.cycles[col] * 1000, mode="lines", stackgroup="lli",
                                     line=dict(width=0.5, color=color), name=label))
    c1.markdown(f"**Lithium lost to side reactions · scenario {pick}**")
    c1.plotly_chart(style(fig, 360).update_layout(xaxis_title="cycle", yaxis_title="mAh"), use_container_width=True)
    fig = go.Figure()
    for n, run in runs.items():
        for col, dash in (("LAM anode [%]", "solid"), ("LAM cathode [%]", "dash")):
            fig.add_trace(go.Scatter(x=run.cycles["cycle"], y=run.cycles[col], mode="lines",
                                     line=dict(width=2, color=colors[n], dash=dash), name=f"{n} · {col.replace(' [%]', '')}"))
    c2.markdown("**Loss of active material · all scenarios** (solid anode, dashed cathode)")
    c2.plotly_chart(style(fig, 360).update_layout(xaxis_title="cycle", yaxis_title="% of electrode"), use_container_width=True)
    cards(["ageing:breakdown", "ageing:lam"])

    # 3. mechanism states over time
    st.markdown("**Inside the cell over its life**")
    c1, c2 = st.columns([2, 1])
    state = c1.selectbox("State", [s for s in STATES if any(s in run.states for run in runs.values())], key="age_state")
    sqrt_t = c2.radio("Time axis", ["time (h)", "√time (√h)"], horizontal=True, key="age_sqrt",
                      help="Diffusion-limited SEI grows like √t, so it plots as a straight line against √time.") != "time (h)"
    fig = go.Figure()
    for n, run in runs.items():
        if state in run.states:
            t = run.states.index.to_numpy()
            fig.add_trace(go.Scatter(x=np.sqrt(t) if sqrt_t else t, y=run.states[state], mode="lines",
                                     line=dict(width=1.5, color=colors[n]), name=describe[n]))
    st.plotly_chart(style(fig, 360).update_layout(xaxis_title="√time (√h)" if sqrt_t else "time (h)", yaxis_title=state),
                    use_container_width=True)
    card(f"ageing:{state}")

    # 4. discharge curves through life
    st.markdown(f"**Discharge curves through life · scenario {pick}**")
    fig = go.Figure()
    curves = runs[pick].curves
    for (cyc, df), color in zip(curves.items(), ramp(len(curves))):
        fig.add_trace(go.Scatter(x=df["q_ah"], y=df["voltage_v"], mode="lines", line=dict(width=2, color=color), name=f"cycle {cyc}"))
    st.plotly_chart(style(fig, 380, "cycle").update_layout(xaxis_title="Ah delivered", yaxis_title="V"), use_container_width=True)
    cards(["ageing:curves", "ageing:acceleration"])
    with st.expander("Per-cycle table"):
        st.dataframe(runs[pick].cycles, use_container_width=True, height=280)
        st.download_button("Download CSV", runs[pick].cycles.to_csv(index=False).encode(), file_name=f"ageing_{pick}.csv")
