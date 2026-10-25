"""Synthetic cycler data from PyBaMM, with known (ground-truth) degradation.

The point is validation: simulate a cell whose loss of lithium inventory
(LLI) and loss of active material (LAM) you choose, run your analysis on the
result, and check you recover what you put in.

Degradation definitions used here (all fractions of the fresh value):

* ``LAM_PE`` / ``LAM_NE``: active material volume fraction lost in the
  positive / negative electrode.
* ``LLI``: lithium lost from the particles (PyBaMM's cyclable lithium,
  ``Q_Li``), i.e. total lithium stored in both electrodes' active material.

LAM here removes host material but keeps its lithium (the "delithiated
LAM" case). Combine LAM with LLI to model lithiated LAM. On its own, LAM_PE
can slightly *raise* low-rate capacity in Chen2020: the same lithium spread
over less cathode pushes more of it into the anode at 4.2 V. That is real
electrode-balancing behaviour, not a bug, and a good test of your DVA fit.

Capacities (Ah) before and after are stored in ``metadata["ground_truth"]``
so you can convert to whichever convention your fitting method uses.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

from .core import CellData, register

FARADAY = 96485.33212

DEFAULT_CYCLE = (
    "Discharge at 1C until 2.5 V",
    "Rest for 30 minutes",
    "Charge at 0.5C until 4.2 V",
    "Hold at 4.2 V until C/50",
    "Rest for 30 minutes",
)


def _area(pv) -> float:
    n_par = pv.get("Number of electrodes connected in parallel to make a cell", 1)
    return pv["Electrode height [m]"] * pv["Electrode width [m]"] * n_par


def _capacities(pv) -> dict[str, float]:
    """Electrode and lithium capacities in Ah from a ParameterValues."""
    area = _area(pv)
    out = {}
    li = 0.0
    for e, name in (("n", "negative"), ("p", "positive")):
        vol = pv[f"{name.capitalize()} electrode active material volume fraction"] * pv[
            f"{name.capitalize()} electrode thickness [m]"
        ] * area
        out[f"Q_{e}_ah"] = pv[f"Maximum concentration in {name} electrode [mol.m-3]"] * vol * FARADAY / 3600
        li += pv[f"Initial concentration in {name} electrode [mol.m-3]"] * vol
    out["Q_Li_ah"] = li * FARADAY / 3600
    return out


def apply_degradation(pv, LLI: float = 0.0, LAM_PE: float = 0.0, LAM_NE: float = 0.0):
    """Return a copy of ParameterValues ``pv`` with the given LLI and LAM applied.

    Lithium left after LLI is placed back into the negative electrode at its
    original concentration, with the rest in the positive electrode. The
    split does not matter because :func:`simulate` always re-solves the
    initial stoichiometries for a target SoC from the total lithium.
    """
    for k, v in {"LLI": LLI, "LAM_PE": LAM_PE, "LAM_NE": LAM_NE}.items():
        if not 0.0 <= v < 1.0:
            raise ValueError(f"{k} must be in [0, 1), got {v}")
    pv = pv.copy()
    fresh = _capacities(pv)
    for name, lam in (("Negative", LAM_NE), ("Positive", LAM_PE)):
        key = f"{name} electrode active material volume fraction"
        pv[key] = pv[key] * (1.0 - lam)

    target_li = (1.0 - LLI) * fresh["Q_Li_ah"] * 3600 / FARADAY  # mol
    area = _area(pv)
    vol_n = pv["Negative electrode active material volume fraction"] * pv["Negative electrode thickness [m]"] * area
    vol_p = pv["Positive electrode active material volume fraction"] * pv["Positive electrode thickness [m]"] * area
    cmax_n = pv["Maximum concentration in negative electrode [mol.m-3]"]
    cmax_p = pv["Maximum concentration in positive electrode [mol.m-3]"]
    c_n = min(pv["Initial concentration in negative electrode [mol.m-3]"], target_li / vol_n)
    c_p = (target_li - c_n * vol_n) / vol_p
    if c_p > cmax_p:  # positive electrode full: push the excess back into the negative
        c_p = cmax_p
        c_n = (target_li - c_p * vol_p) / vol_n
    if not (0 <= c_n <= cmax_n):
        raise ValueError("degradation leaves more lithium than both electrodes can hold")
    pv["Initial concentration in negative electrode [mol.m-3]"] = c_n
    pv["Initial concentration in positive electrode [mol.m-3]"] = c_p
    return pv


@register("pybamm")
def simulate(
    n_cycles: int = 3,
    cycle: Sequence[str] = DEFAULT_CYCLE,
    model: str = "SPMe",
    parameter_set: str = "Chen2020",
    degradation: dict[str, float] | None = None,
    initial_soc: float = 1.0,
    temperature_c: float = 25.0,
    period: str = "10 seconds",
    parameter_overrides: dict[str, Any] | None = None,
    noise_v: float = 0.0,
    noise_a: float = 0.0,
    seed: int | None = 0,
) -> CellData:
    """Simulate ``n_cycles`` repeats of ``cycle`` and return canonical CellData.

    ``cycle`` is a list of PyBaMM experiment step strings. Each cycle in the
    experiment becomes a ``cycle_index`` (starting at 1) and each step in it a
    ``step_index``. Optional Gaussian noise is added to voltage and current
    *after* simulation, so the ground truth stays exact.
    """
    import pybamm

    pv = pybamm.ParameterValues(parameter_set)
    if parameter_overrides:
        pv.update(parameter_overrides)
    t_k = temperature_c + 273.15
    pv.update({"Ambient temperature [K]": t_k, "Initial temperature [K]": t_k})
    fresh = _capacities(pv)
    deg = {"LLI": 0.0, "LAM_PE": 0.0, "LAM_NE": 0.0, **(degradation or {})}
    pv = apply_degradation(pv, **deg)
    aged = _capacities(pv)

    mdl = getattr(pybamm.lithium_ion, model)()
    experiment = pybamm.Experiment([tuple(cycle)] * n_cycles, period=period)
    sim = pybamm.Simulation(mdl, parameter_values=pv, experiment=experiment)
    sol = sim.solve(initial_soc=initial_soc)

    chunks = []
    for c_no, cyc in enumerate(sol.cycles, start=1):
        if cyc is None:
            break
        for s_no, step in enumerate(cyc.steps):
            chunks.append(
                pd.DataFrame(
                    {
                        "test_time_s": step["Time [s]"].entries,
                        "cycle_index": c_no,
                        "step_index": s_no,
                        # PyBaMM is positive on discharge; the schema is positive on charge.
                        "current_a": -step["Current [A]"].entries,
                        "voltage_v": step["Voltage [V]"].entries,
                        "temperature_c": step["X-averaged cell temperature [K]"].entries - 273.15,
                    }
                )
            )
    ts = pd.concat(chunks, ignore_index=True)

    rng = np.random.default_rng(seed)
    if noise_v:
        ts["voltage_v"] += rng.normal(0.0, noise_v, len(ts))
    if noise_a:
        ts["current_a"] += rng.normal(0.0, noise_a, len(ts))

    meta = {
        "source": "pybamm",
        "cell_id": f"pybamm_{model}_{parameter_set}",
        "model": model,
        "parameter_set": parameter_set,
        "pybamm_version": pybamm.__version__,
        "nominal_capacity_ah": float(pv["Nominal cell capacity [A.h]"]),
        "experiment": list(cycle),
        "ground_truth": {
            **deg,
            "fresh": fresh,
            "aged": aged,
            "noise_v": noise_v,
            "noise_a": noise_a,
        },
    }
    return CellData(ts, meta)
