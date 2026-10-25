"""Physics-based test simulations with every internal state exposed.

``simulate_test`` runs one PyBaMM experiment (a chemistry, a test protocol
and a set of conditions) and returns an :class:`EchemRun`. The run holds:

* ``series``: scalar time series (terminal values, voltage-loss breakdown,
  electrode stoichiometries, anode potential, heat sources)
* ``fields``: through-thickness profiles over x, one column per time point
* ``particles``: radial concentration profiles inside the active particles

Everything is in charge-positive current and degrees Celsius, matching
:mod:`batterylab.schema`. Nothing here analyses the data; it only exposes
what the model computes, so you can look at it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .synthetic import apply_degradation

# label -> (PyBaMM parameter set, short description)
CHEMISTRIES: dict[str, tuple[str, str]] = {
    "NMC811 | graphite-SiOx": ("Chen2020", "LG M50 21700, 5 Ah (Chen 2020)"),
    "NMC532 | graphite": ("Mohtat2020", "5 Ah pouch (Mohtat 2020)"),
    "LFP | graphite": ("Prada2013", "A123 26650, 2.3 Ah (Prada 2013)"),
    "LCO | graphite": ("Marquis2019", "Kokam pouch, 0.68 Ah (Marquis 2019)"),
    "NCA | graphite": ("NCA_Kim2011", "0.43 Ah pouch (Kim 2011)"),
    "NMC | graphite (Ecker)": ("Ecker2015", "Kokam 7.5 Ah, scaled (Ecker 2015)"),
}

TESTS = ("CC discharge", "CC-CV charge", "HPPC", "GITT", "Drive cycle", "Relaxation after pulse")

# Scalar series: label -> (PyBaMM variable, scale, offset). Grouped for display.
SERIES_GROUPS: dict[str, dict[str, str]] = {
    "Terminal": {
        "Voltage [V]": "Voltage [V]",
        "Current [A]": "Current [A]",
        "Discharge capacity [Ah]": "Discharge capacity [A.h]",
        "Cell temperature [°C]": "X-averaged cell temperature [K]",
    },
    "Voltage breakdown": {
        "Open-circuit voltage [V]": "Battery open-circuit voltage [V]",
        "Particle concentration overpotential [V]": "Battery particle concentration overpotential [V]",
        "Reaction overpotential [V]": "X-averaged battery reaction overpotential [V]",
        "Electrolyte concentration overpotential [V]": "X-averaged battery concentration overpotential [V]",
        "Electrolyte ohmic loss [V]": "X-averaged battery electrolyte ohmic losses [V]",
        "Solid-phase ohmic loss [V]": "X-averaged battery solid phase ohmic losses [V]",
    },
    "Electrodes": {
        "Anode average stoichiometry": "Average negative particle stoichiometry",
        "Cathode average stoichiometry": "Average positive particle stoichiometry",
        "Anode surface stoichiometry (x-avg)": "X-averaged negative particle surface stoichiometry",
        "Cathode surface stoichiometry (x-avg)": "X-averaged positive particle surface stoichiometry",
        "Anode OCP [V]": "Battery negative electrode bulk open-circuit potential [V]",
        "Cathode OCP [V]": "Battery positive electrode bulk open-circuit potential [V]",
    },
    "Heat": {
        "Total heating [W]": "Total heating [W]",
        "Ohmic heating [W]": "Ohmic heating [W]",
        "Irreversible reaction heating [W]": "Irreversible electrochemical heating [W]",
        "Reversible (entropic) heating [W]": "Reversible heating [W]",
    },
}

# Through-thickness fields: label -> (negative-electrode var, separator var, positive var) or a single full-cell var.
FIELDS: dict[str, dict[str, Any]] = {
    "Electrolyte concentration [mol/m³]": {"full": "Electrolyte concentration [mol.m-3]"},
    "Electrolyte potential [V]": {"full": "Electrolyte potential [V]"},
    "Particle surface stoichiometry": {
        "n": "Negative particle surface stoichiometry",
        "p": "Positive particle surface stoichiometry",
    },
    "Interfacial current density [A/m²]": {
        "n": "Negative electrode interfacial current density [A.m-2]",
        "p": "Positive electrode interfacial current density [A.m-2]",
    },
    "Reaction overpotential [V]": {
        "n": "Negative electrode reaction overpotential [V]",
        "p": "Positive electrode reaction overpotential [V]",
    },
    "Solid−electrolyte potential difference [V]": {
        "n": "Negative electrode surface potential difference [V]",
        "p": "Positive electrode surface potential difference [V]",
    },
}


@dataclass
class EchemRun:
    """Everything one simulated test produced. Times are in seconds."""

    config: dict[str, Any]
    t: np.ndarray
    series: pd.DataFrame
    x_um: dict[str, np.ndarray]
    fields: dict[str, np.ndarray]
    r_um: dict[str, np.ndarray]
    particles: dict[str, np.ndarray]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        c = self.config
        bits = [c["chemistry"], c["test"], f"{c['temperature_c']:g} °C"]
        if c.get("c_rate") is not None and c["test"] in ("CC discharge", "CC-CV charge"):
            bits.append(f"{c['c_rate']:g}C")
        deg = {k: v for k, v in c.get("degradation", {}).items() if v}
        if deg:
            bits.append(", ".join(f"{k} {v:.0%}" for k, v in deg.items()))
        knob_names = {v: k for k, v in KNOBS.items()}
        for name, k in (c.get("parameter_scales") or {}).items():
            bits.append(f"{knob_names.get(name, name)} ×{k:g}")
        if self.meta.get("name"):  # e.g. "Test 2", set by whoever is comparing runs
            bits.insert(0, self.meta["name"])
        return " · ".join(bits)


def drive_profile(kind: str, duration_s: float, nominal_ah: float, seed: int = 0, dt: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """A synthetic EV-like current profile (charge-positive amps).

    Not a standard cycle: a seeded random sequence of accelerations, cruises,
    regenerative braking and stops, scaled to the cell's capacity. ``kind`` is
    "urban" (frequent stops, mild), "highway" (sustained load) or
    "aggressive" (high peaks, strong regen).
    """
    rng = np.random.default_rng(seed)
    peak, cruise, regen, stop_p = {
        "urban": (2.0, 0.6, 0.8, 0.35),
        "highway": (2.0, 1.2, 0.4, 0.05),
        "aggressive": (4.0, 1.5, 1.5, 0.2),
    }[kind]
    t = np.arange(0.0, duration_s, dt)
    i = np.zeros_like(t)
    k = 0
    while k < len(t):
        n_acc, n_cru, n_brk = rng.integers(5, 15), rng.integers(10, 60), rng.integers(4, 12)
        seg = np.concatenate(
            [
                -np.linspace(0.3, peak * rng.uniform(0.5, 1), n_acc),
                -cruise * rng.uniform(0.6, 1.2) * np.ones(n_cru) * (1 + 0.1 * rng.standard_normal(n_cru)),
                regen * rng.uniform(0.3, 1) * np.ones(n_brk),
                np.zeros(rng.integers(5, 40) if rng.random() < stop_p else 0),
            ]
        )
        i[k : k + len(seg)] = seg[: len(t) - k]
        k += len(seg)
    return t, i * nominal_ah


def _experiment_steps(test: str, c_rate: float, vmin: float, vmax: float) -> tuple[list, float]:
    """(PyBaMM experiment steps, initial SoC) for a test protocol."""
    if test == "CC discharge":
        return [f"Discharge at {c_rate}C until {vmin} V", "Rest for 1 hour"], 1.0
    if test == "CC-CV charge":
        return [f"Charge at {c_rate}C until {vmax} V", f"Hold at {vmax} V until C/50", "Rest for 30 minutes"], 0.0
    if test == "HPPC":
        # Step down 10 % SoC first, so the charge pulse never starts at 100 %.
        step = [f"Discharge at 0.5C for 12 minutes or until {vmin} V", "Rest for 30 minutes"]
        pulse = [
            "Discharge at 1C for 10 seconds",
            "Rest for 40 seconds",
            "Charge at 0.75C for 10 seconds",
            "Rest for 40 seconds",
        ]
        return (step + pulse) * 9, 1.0
    if test == "GITT":
        return [f"Discharge at 0.2C for 15 minutes or until {vmin} V", "Rest for 45 minutes"] * 20, 1.0
    if test == "Relaxation after pulse":
        return ["Rest for 5 minutes", f"Discharge at {c_rate}C for 2 minutes or until {vmin} V", "Rest for 2 hours"], 0.5
    raise ValueError(f"unknown test {test!r}")


_THERMAL_WORDS = ("current collector", "heat", "thermal conductivity", "density", "Cell cooling", "Cell volume", "Total heat transfer")


#: Material and design properties you can scale ("physics knobs"): label -> PyBaMM parameter.
KNOBS: dict[str, str] = {
    "Anode solid diffusivity": "Negative particle diffusivity [m2.s-1]",
    "Cathode solid diffusivity": "Positive particle diffusivity [m2.s-1]",
    "Anode reaction rate": "Negative electrode exchange-current density [A.m-2]",
    "Cathode reaction rate": "Positive electrode exchange-current density [A.m-2]",
    "Electrolyte conductivity": "Electrolyte conductivity [S.m-1]",
    "Electrolyte diffusivity": "Electrolyte diffusivity [m2.s-1]",
    "Anode particle radius": "Negative particle radius [m]",
    "Cathode particle radius": "Positive particle radius [m]",
    "Anode thickness": "Negative electrode thickness [m]",
    "Cathode thickness": "Positive electrode thickness [m]",
}


def scale_parameters(pv, scales: dict[str, float] | None):
    """Multiply parameters (by PyBaMM name) by the given factors.

    Works for plain values and for functions of state (e.g. a diffusivity
    that depends on stoichiometry and temperature): a function is wrapped so
    its output is scaled everywhere it is evaluated.
    """
    for name, k in (scales or {}).items():
        if k == 1:
            continue
        v = pv[name]
        pv[name] = (lambda *args, _f=v, _k=k: _k * _f(*args)) if callable(v) else v * k
    return pv


def _sim_parameters(chemistry: str, temperature_c: float, degradation: dict | None, scales: dict[str, float] | None = None):
    import pybamm

    pv = scale_parameters(pybamm.ParameterValues(CHEMISTRIES[chemistry][0]), scales)
    # Some sets (e.g. Prada2013) lack the current-collector and thermal
    # properties a lumped thermal model needs; borrow Chen2020's.
    ref = pybamm.ParameterValues("Chen2020")
    missing = {k: v for k, v in ref.items() if k not in pv.keys() and any(w in k for w in _THERMAL_WORDS)}
    pv.update(missing, check_already_exists=False)
    t_k = temperature_c + 273.15
    pv.update({"Ambient temperature [K]": t_k, "Initial temperature [K]": t_k})
    deg = {"LLI": 0.0, "LAM_PE": 0.0, "LAM_NE": 0.0, **(degradation or {})}
    if any(deg.values()):
        pv = apply_degradation(pv, **deg)
    return pv, sorted(missing)


def simulate_test(
    chemistry: str = "NMC811 | graphite-SiOx",
    test: str = "CC discharge",
    c_rate: float = 1.0,
    temperature_c: float = 25.0,
    model: str = "DFN",
    thermal: bool = True,
    degradation: dict[str, float] | None = None,
    drive: str = "urban",
    drive_minutes: float = 30.0,
    initial_soc: float | None = None,
    period_s: float = 10.0,
    parameter_scales: dict[str, float] | None = None,
) -> EchemRun:
    """Simulate one test and return all exposed internal states.

    ``parameter_scales`` multiplies material/design parameters by PyBaMM
    name (see :data:`KNOBS`), e.g. ``{"Negative particle diffusivity [m2.s-1]": 0.1}``.
    """
    import pybamm

    config = dict(
        chemistry=chemistry, test=test, c_rate=c_rate, temperature_c=temperature_c, model=model,
        thermal=thermal, degradation=dict(degradation or {}), drive=drive, drive_minutes=drive_minutes,
        initial_soc=initial_soc, period_s=period_s, parameter_scales=dict(parameter_scales or {}),
    )
    pv, borrowed = _sim_parameters(chemistry, temperature_c, degradation, parameter_scales)
    vmin, vmax = pv["Lower voltage cut-off [V]"], pv["Upper voltage cut-off [V]"]
    nominal = float(pv["Nominal cell capacity [A.h]"])
    options = {"thermal": "lumped"} if thermal else {}
    mdl = getattr(pybamm.lithium_ion, model)(options=options)

    if test == "Drive cycle":
        t, i = drive_profile(drive, drive_minutes * 60, nominal)
        pv["Current function [A]"] = pybamm.Interpolant(t, -i, pybamm.t)  # PyBaMM is discharge-positive
        sim = pybamm.Simulation(mdl, parameter_values=pv)
        t_out = t[:: max(1, int(period_s))]
        sol = sim.solve(t_eval=[t[0], t[-1]], t_interp=t_out, initial_soc=0.9 if initial_soc is None else initial_soc)
    else:
        steps, soc0 = _experiment_steps(test, c_rate, vmin, vmax)
        exp = pybamm.Experiment(steps, period=f"{period_s:g} seconds")
        sim = pybamm.Simulation(mdl, parameter_values=pv, experiment=exp)
        sol = sim.solve(initial_soc=soc0 if initial_soc is None else initial_soc)

    t = sol["Time [s]"].entries
    series = {}
    for group in SERIES_GROUPS.values():
        for label, var in group.items():
            try:
                series[label] = np.asarray(sol[var].entries, dtype=float).reshape(len(t))
            except Exception:
                continue  # variable not in this model (e.g. heating when isothermal)
    series["Current [A]"] = -series["Current [A]"]
    series["Cell temperature [°C]"] = series["Cell temperature [°C]"] - 273.15
    # Anode potential vs Li/Li+ next to the separator: below 0 V lithium plating is thermodynamically possible.
    spd = np.atleast_2d(sol["Negative electrode surface potential difference [V]"].entries)
    series["Anode potential at separator vs Li [V]"] = spd[-1] if spd.shape[0] > 1 else spd[0]
    series_df = pd.DataFrame(series, index=pd.Index(t, name="time_s"))

    x = {k: sol[f"x{'_' + k if k != 'full' else ''} [m]"].entries[:, 0] * 1e6 for k in ("full", "n", "p")}
    fields = {}
    for label, parts in FIELDS.items():
        try:
            fields[label] = {k: np.atleast_2d(sol[v].entries) for k, v in parts.items()}
        except Exception:
            continue

    r = {k: sol[f"r_{k} [m]"].entries.reshape(sol[f"r_{k} [m]"].entries.shape[0], -1)[:, 0] * 1e6 for k in ("n", "p")}
    particles = {}
    for k, name in (("n", "negative"), ("p", "positive")):
        avg = np.atleast_2d(sol[f"X-averaged {name} particle stoichiometry"].entries)
        particles[f"{k}:x-averaged"] = avg
        full = sol[f"{name.capitalize()} particle stoichiometry"].entries
        if full.ndim == 3 and full.shape[1] > 1:  # DFN resolves particles at every x
            sep, cc = (-1, 0) if k == "n" else (0, -1)
            particles[f"{k}:near separator"] = full[:, sep, :]
            particles[f"{k}:near current collector"] = full[:, cc, :]

    meta = {
        "nominal_capacity_ah": nominal,
        "v_min": vmin,
        "v_max": vmax,
        "description": CHEMISTRIES[chemistry][1],
        "termination": str(getattr(sol, "termination", "")),
        "borrowed_parameters": borrowed,
        "geometry_um": cell_geometry(pv),
    }
    return EchemRun(config, t, series_df, x, fields, r, particles, meta)


def cell_geometry(pv) -> dict[str, float]:
    """Layer thicknesses and particle radii (µm) plus active-material fractions, for drawing the cell."""
    um = lambda k: float(pv[k]) * 1e6  # noqa: E731
    return {
        "anode": um("Negative electrode thickness [m]"),
        "separator": um("Separator thickness [m]"),
        "cathode": um("Positive electrode thickness [m]"),
        "anode_cc": um("Negative current collector thickness [m]"),
        "cathode_cc": um("Positive current collector thickness [m]"),
        "anode_particle_radius": um("Negative particle radius [m]"),
        "cathode_particle_radius": um("Positive particle radius [m]"),
        "anode_active_fraction": float(pv["Negative electrode active material volume fraction"]),
        "cathode_active_fraction": float(pv["Positive electrode active material volume fraction"]),
    }


def ocp_curves(chemistry: str, n: int = 200) -> pd.DataFrame:
    """Each electrode's open-circuit potential over its full stoichiometry range, at 25 °C."""
    import pybamm

    pv = pybamm.ParameterValues(CHEMISTRIES[chemistry][0])
    sto = np.linspace(0.005, 0.995, n)
    out = {"stoichiometry": sto}
    for e in ("Negative", "Positive"):
        f = pv[f"{e} electrode OCP [V]"]
        vec = pybamm.Vector(sto)
        try:
            u = pv.evaluate(f(vec))
        except Exception:
            u = pv.evaluate(f(vec, pybamm.Scalar(298.15)))
        out[f"{e.lower()}_ocp_v"] = np.ravel(u)
    return pd.DataFrame(out)
