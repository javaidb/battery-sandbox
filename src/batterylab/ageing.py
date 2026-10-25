"""Physics-based cycle ageing: SEI growth, lithium plating, particle cracking.

``simulate_ageing`` cycles a cell with PyBaMM's degradation submodels
switched on and returns how much capacity each mechanism took, cycle by
cycle, plus the internal states that drive it (SEI thickness, plated
lithium, crack length) over time.

Only the OKane2022 parameter set (LG M50, NMC811 | graphite-SiOx) is
parameterised for all of these mechanisms together, so that is the cell
used here. Nothing in this module analyses the result; it exposes the
model's own bookkeeping so you can see each mechanism at work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

CELL = "LG M50 21700 · NMC811 | graphite-SiOx (O'Kane 2022)"

# Per-cycle quantities from PyBaMM's summary variables: label -> variable.
LOSSES_AH = {
    "SEI": "Loss of capacity to negative SEI [A.h]",
    "SEI on cracks": "Loss of capacity to negative SEI on cracks [A.h]",
    "Lithium plating": "Loss of capacity to negative lithium plating [A.h]",
}
SUMMARY = {
    "LLI [%]": "Loss of lithium inventory [%]",
    "LAM anode [%]": "Loss of active material in negative electrode [%]",
    "LAM cathode [%]": "Loss of active material in positive electrode [%]",
    "Throughput [Ah]": "Throughput capacity [A.h]",
    **{f"Lost to {k} [Ah]": v for k, v in LOSSES_AH.items()},
}
# States over time: label -> (variable, scale to display units).
STATES = {
    "SEI thickness [nm]": ("X-averaged negative SEI thickness [m]", 1e9),
    "SEI on cracks thickness [nm]": ("X-averaged negative SEI on cracks thickness [m]", 1e9),
    "Plated lithium thickness [nm]": ("X-averaged negative lithium plating thickness [m]", 1e9),
    "Anode crack length [µm]": ("X-averaged negative particle crack length [m]", 1e6),
    "Cathode crack length [µm]": ("X-averaged positive particle crack length [m]", 1e6),
    "Cell temperature [°C]": ("X-averaged cell temperature [K]", 1.0),
    "Voltage [V]": ("Voltage [V]", 1.0),
}


# Side-reaction rates scaled by the "accelerate" factor. Real cells fade over
# hundreds of cycles; scaling these lets trends show in tens. It is an
# artificial speed-up of each mechanism, not a calibrated ageing test.
ACCELERATED = (
    "SEI solvent diffusivity [m2.s-1]",
    "SEI kinetic rate constant [m.s-1]",
    "Lithium plating kinetic rate constant [m.s-1]",
    "Negative electrode cracking rate",
    "Positive electrode cracking rate",
    "Negative electrode LAM constant proportional term [s-1]",
    "Positive electrode LAM constant proportional term [s-1]",
)


@dataclass
class AgeingRun:
    config: dict[str, Any]
    cycles: pd.DataFrame  # one row per cycle
    states: pd.DataFrame  # time series, index time_h
    curves: dict[int, pd.DataFrame] = field(default_factory=dict)  # cycle -> discharge curve (Ah moved, V)
    meta: dict[str, Any] = field(default_factory=dict)


def degradation_options(sei: bool = True, plating: bool = True, cracking: bool = True) -> dict[str, Any]:
    """PyBaMM model options for the chosen mechanisms."""
    opts: dict[str, Any] = {"thermal": "lumped"}
    if sei:
        opts.update({"SEI": "solvent-diffusion limited", "SEI porosity change": "true"})
    if plating:
        opts.update({"lithium plating": "partially reversible", "lithium plating porosity change": "true"})
    if cracking:
        opts.update({"particle mechanics": ("swelling and cracking", "swelling only"),
                     "loss of active material": "stress-driven"})
        if sei:
            opts["SEI on cracks"] = "true"
    return opts


def simulate_ageing(
    cycles: int = 50,
    charge_c: float = 1.0,
    discharge_c: float = 1.0,
    temperature_c: float = 25.0,
    sei: bool = True,
    plating: bool = True,
    cracking: bool = True,
    model: str = "SPM",
    accelerate: float = 1.0,
    curves_at: int = 6,
) -> AgeingRun:
    """Cycle the cell: CC-CV charge, rest, CC discharge, rest, ``cycles`` times.

    ``accelerate`` multiplies every side-reaction rate (see :data:`ACCELERATED`).
    ``curves_at`` evenly spaced cycles keep their full discharge curve.
    """
    import pybamm

    from .echem import scale_parameters

    config = dict(cycles=cycles, charge_c=charge_c, discharge_c=discharge_c, temperature_c=temperature_c,
                  sei=sei, plating=plating, cracking=cracking, model=model, accelerate=accelerate)
    pv = scale_parameters(pybamm.ParameterValues("OKane2022"), {k: accelerate for k in ACCELERATED})
    t_k = temperature_c + 273.15
    pv.update({"Ambient temperature [K]": t_k, "Initial temperature [K]": t_k})
    vmin, vmax = pv["Lower voltage cut-off [V]"], pv["Upper voltage cut-off [V]"]
    mdl = getattr(pybamm.lithium_ion, model)(options=degradation_options(sei, plating, cracking))
    cycle = (
        f"Charge at {charge_c}C until {vmax} V",
        f"Hold at {vmax} V until C/20",
        "Rest for 10 minutes",
        f"Discharge at {discharge_c}C until {vmin} V",
        "Rest for 10 minutes",
    )
    exp = pybamm.Experiment([cycle] * cycles, period="120 seconds")
    sol = pybamm.Simulation(mdl, parameter_values=pv, experiment=exp).solve(initial_soc=0)

    done = [c for c in sol.cycles if c is not None]
    sv = sol.summary_variables
    rows = []
    for i, cyc in enumerate(done):
        dis = cyc.steps[3] if len(cyc.steps) > 3 else cyc.steps[-1]
        q = dis["Discharge capacity [A.h]"].entries
        row = {"cycle": i + 1, "discharge_capacity_ah": float(q[-1] - q[0])}
        for label, var in SUMMARY.items():
            try:
                row[label] = float(np.asarray(sv[var])[i])
            except (KeyError, IndexError):
                row[label] = np.nan  # mechanism switched off
        rows.append(row)
    cycles_df = pd.DataFrame(rows)

    t_h = sol["Time [h]"].entries
    states = {}
    for label, (var, scale) in STATES.items():
        try:
            vals = np.asarray(sol[var].entries, dtype=float).reshape(len(t_h)) * scale
        except Exception:
            continue
        states[label] = vals - 273.15 if label.startswith("Cell temperature") else vals
    states_df = pd.DataFrame(states, index=pd.Index(t_h, name="time_h"))
    states_df = states_df.iloc[:: max(1, len(states_df) // 4000)]  # plenty for plotting

    curves = {}
    picks = sorted(set(np.linspace(0, len(done) - 1, min(curves_at, len(done))).round().astype(int)))
    for i in picks:
        dis = done[i].steps[3] if len(done[i].steps) > 3 else done[i].steps[-1]
        q = dis["Discharge capacity [A.h]"].entries
        curves[i + 1] = pd.DataFrame({"q_ah": q - q[0], "voltage_v": dis["Voltage [V]"].entries})

    meta = {"cell": CELL, "nominal_capacity_ah": float(pv["Nominal cell capacity [A.h]"]),
            "cycles_completed": len(done), "v_min": vmin, "v_max": vmax}
    return AgeingRun(config, cycles_df, states_df, curves, meta)
