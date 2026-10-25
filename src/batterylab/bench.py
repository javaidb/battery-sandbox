"""Test bench for BMS algorithms: a PyBaMM "plant", sensors, and scoring.

The plant is a physics model of a real cell, run under a current profile.
It provides the truth an estimator never sees: true SoC, true OCV, and the
true (possibly aged) capacity. ``measure`` turns the plant output into what
a BMS would actually read, with bias and noise. ``run_estimator`` steps
your estimator through those readings, and ``soc_metrics`` scores it.

Your estimators and ECMs live in ``workbench/``; nothing here implements
one. What an estimator is told up front (``PlantRun.context``) is
deliberately what a real BMS would know: the datasheet capacity and an OCV
curve measured on a *fresh* cell. It is not told the plant's ageing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .echem import CHEMISTRIES, _sim_parameters, drive_profile

PROFILES = ("urban", "highway", "aggressive", "pulse train", "constant 1C")


@dataclass(frozen=True)
class Scenario:
    chemistry: str = "NMC811 | graphite-SiOx"
    temperature_c: float = 25.0
    LLI: float = 0.0
    LAM_PE: float = 0.0
    LAM_NE: float = 0.0
    profile: str = "urban"
    duration_min: float = 60.0
    soc0: float = 0.9
    seed: int = 0
    model: str = "SPMe"


@dataclass(frozen=True)
class Sensors:
    current_bias_a: float = 0.0
    current_noise_a: float = 0.0
    voltage_noise_v: float = 0.0
    temperature_noise_c: float = 0.0
    soc0_error: float = 0.0  # added to the estimator's initial SoC guess
    seed: int = 1


@dataclass
class PlantRun:
    scenario: Scenario
    truth: pd.DataFrame  # time_s, current_a, voltage_v, temperature_c, soc, ocv_v
    context: dict[str, Any] = field(default_factory=dict)


def _profile(s: Scenario, nominal_ah: float) -> tuple[np.ndarray, np.ndarray]:
    dur = s.duration_min * 60
    if s.profile in ("urban", "highway", "aggressive"):
        return drive_profile(s.profile, dur, nominal_ah, seed=s.seed)
    t = np.arange(0.0, dur, 1.0)
    if s.profile == "constant 1C":
        return t, -nominal_ah * np.ones_like(t)
    if s.profile == "pulse train":
        i = np.zeros_like(t)
        for k0 in range(0, len(t), 600):  # every 10 min: 10 s 1C discharge, 10 s 0.75C charge, 0.5C drain
            i[k0 : k0 + 10] = -nominal_ah
            i[k0 + 50 : k0 + 60] = 0.75 * nominal_ah
            i[k0 + 120 : k0 + 480] = -0.5 * nominal_ah
        return t, i
    raise ValueError(f"unknown profile {s.profile!r}")


def _soc_window(pv) -> tuple[float, float, float, float]:
    import pybamm

    x0, y0 = pybamm.lithium_ion.get_initial_stoichiometries(0, pv)
    x1, y1 = pybamm.lithium_ion.get_initial_stoichiometries(1, pv)
    return float(x0), float(x1), float(y0), float(y1)


def _ocv_curve(pv, n: int = 101) -> pd.DataFrame:
    """Equilibrium OCV vs SoC for the cell described by ``pv`` (at 25 °C)."""
    import pybamm

    x0, x1, y0, y1 = _soc_window(pv)
    soc = np.linspace(0, 1, n)
    vals = {}
    for e, sto in (("Negative", x0 + soc * (x1 - x0)), ("Positive", y0 + soc * (y1 - y0))):
        f, vec = pv[f"{e} electrode OCP [V]"], pybamm.Vector(sto)
        try:
            vals[e] = np.ravel(pv.evaluate(f(vec)))
        except Exception:
            vals[e] = np.ravel(pv.evaluate(f(vec, pybamm.Scalar(298.15))))
    return pd.DataFrame({"soc": soc, "ocv_v": vals["Positive"] - vals["Negative"]})


def capacity_ah(pv) -> float:
    """Capacity between the 0 % and 100 % SoC stoichiometry limits."""
    from .synthetic import _capacities

    x0, x1, _, _ = _soc_window(pv)
    return (x1 - x0) * _capacities(pv)["Q_n_ah"]


def run_plant(s: Scenario) -> PlantRun:
    """Simulate the plant under the scenario's current profile."""
    import pybamm

    deg = {"LLI": s.LLI, "LAM_PE": s.LAM_PE, "LAM_NE": s.LAM_NE}
    pv, _ = _sim_parameters(s.chemistry, s.temperature_c, deg)
    fresh, _ = _sim_parameters(s.chemistry, 25.0, None)
    nominal = float(pv["Nominal cell capacity [A.h]"])
    q_true = capacity_ah(pv)

    t, i = _profile(s, nominal)
    pv["Current function [A]"] = pybamm.Interpolant(t, -i, pybamm.t)
    mdl = getattr(pybamm.lithium_ion, s.model)(options={"thermal": "lumped"})
    # Regen or charge pulses near full charge briefly overshoot the upper
    # limit; a real cell rides through that, so only the lower limit ends a run.
    mdl.events = [e for e in mdl.events if not e.name.startswith("Maximum voltage")]
    sol =pybamm.Simulation(mdl, parameter_values=pv).solve(t_eval=[t[0], t[-1]], t_interp=t, initial_soc=s.soc0)

    tt = sol["Time [s]"].entries
    q_dis = sol["Discharge capacity [A.h]"].entries
    truth = pd.DataFrame(
        {
            "time_s": tt,
            "current_a": -sol["Current [A]"].entries,
            "voltage_v": sol["Voltage [V]"].entries,
            "temperature_c": sol["X-averaged cell temperature [K]"].entries - 273.15,
            "soc": s.soc0 - q_dis / q_true,
            "ocv_v": sol["Battery open-circuit voltage [V]"].entries,
        }
    )
    context = {
        "nominal_capacity_ah": nominal,  # datasheet value, not the plant's true capacity
        "ocv_curve": _ocv_curve(fresh),  # measured on a fresh cell at 25 °C
        "v_min": float(pv["Lower voltage cut-off [V]"]),
        "v_max": float(pv["Upper voltage cut-off [V]"]),
        "chemistry": s.chemistry,
        "true_capacity_ah": q_true,  # for scoring only; estimators should not read this
        "stopped_early": len(tt) < len(t),
    }
    return PlantRun(s, truth, context)


def measure(plant: PlantRun, sensors: Sensors) -> pd.DataFrame:
    """What the BMS reads: truth plus sensor bias and noise."""
    rng = np.random.default_rng(sensors.seed)
    tr = plant.truth
    n = len(tr)
    return pd.DataFrame(
        {
            "time_s": tr["time_s"],
            "current_a": tr["current_a"] + sensors.current_bias_a + rng.normal(0, sensors.current_noise_a, n),
            "voltage_v": tr["voltage_v"] + rng.normal(0, sensors.voltage_noise_v, n),
            "temperature_c": tr["temperature_c"] + rng.normal(0, sensors.temperature_noise_c, n),
        }
    )


def estimator_context(plant: PlantRun, sensors: Sensors) -> dict[str, Any]:
    """The context handed to an estimator's ``reset``: no ground truth in it."""
    ctx = {k: v for k, v in plant.context.items() if k not in ("true_capacity_ah", "stopped_early")}
    soc0 = float(plant.truth["soc"].iloc[0])
    ctx["soc0_guess"] = float(np.clip(soc0 + sensors.soc0_error, 0, 1))
    ctx["dt_s"] = float(np.median(np.diff(plant.truth["time_s"])))
    return ctx


def run_estimator(factory: Callable[[], Any], measured: pd.DataFrame, ctx: dict[str, Any]) -> pd.DataFrame:
    """Step a fresh estimator through every measurement.

    ``factory()`` returns an object with ``reset(ctx)`` and
    ``step(t, current_a, voltage_v, temperature_c)``. ``step`` returns a SoC
    float or a dict with at least ``"soc"`` (and optionally ``"soc_std"``
    and any other states you want plotted).
    """
    est = factory()
    est.reset(dict(ctx))
    rows = []
    for t, i, v, temp in measured[["time_s", "current_a", "voltage_v", "temperature_c"]].itertuples(index=False):
        out = est.step(float(t), float(i), float(v), float(temp))
        rows.append(out if isinstance(out, dict) else {"soc": out})
    return pd.DataFrame(rows, index=measured.index).assign(time_s=measured["time_s"].to_numpy())


def soc_metrics(truth: pd.DataFrame, est: pd.DataFrame, band: float = 0.02) -> dict[str, float]:
    """RMSE, max |error|, final |error| and time to stay within ``band`` (s)."""
    err = (est["soc"].to_numpy() - truth["soc"].to_numpy())
    t = truth["time_s"].to_numpy()
    outside = np.flatnonzero(np.abs(err) > band)
    if len(outside) == 0:
        conv = 0.0
    elif outside[-1] == len(err) - 1:
        conv = float("nan")  # never settled inside the band
    else:
        conv = float(t[outside[-1] + 1] - t[0])
    return {
        "rmse_pct": float(np.sqrt(np.mean(err**2)) * 100),
        "max_abs_pct": float(np.max(np.abs(err)) * 100),
        "final_abs_pct": float(abs(err[-1]) * 100),
        "convergence_s": conv,
    }


ROBUSTNESS_CASES: dict[str, dict[str, Any]] = {
    "baseline": {},
    "current bias +1 % of 1C": {"sensors": {"current_bias_frac": 0.01}},
    "initial SoC +20 %": {"sensors": {"soc0_error": 0.2}},
    "voltage noise 10 mV": {"sensors": {"voltage_noise_v": 0.01}},
    "aged (LLI 10 %, LAM_PE 5 %)": {"scenario": {"LLI": 0.10, "LAM_PE": 0.05}},
    "cold (0 °C)": {"scenario": {"temperature_c": 0.0}},
}


def robustness_case(base_s: Scenario, base_n: Sensors, case: dict[str, Any]) -> tuple[Scenario, Sensors]:
    """Apply one robustness case on top of a base scenario and sensor set."""
    s = Scenario(**{**asdict(base_s), **case.get("scenario", {})})
    sens = dict(case.get("sensors", {}))
    if "current_bias_frac" in sens:
        nominal = float(_sim_parameters(s.chemistry, 25.0, None)[0]["Nominal cell capacity [A.h]"])
        sens["current_bias_a"] = base_n.current_bias_a + sens.pop("current_bias_frac") * nominal
    return s, Sensors(**{**asdict(base_n), **sens})


__all__ = [
    "CHEMISTRIES", "PROFILES", "ROBUSTNESS_CASES", "PlantRun", "Scenario", "Sensors", "capacity_ah",
    "estimator_context", "measure", "robustness_case", "run_estimator", "run_plant", "soc_metrics",
]
