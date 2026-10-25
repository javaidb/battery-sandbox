"""Per-step and per-cycle summary tables computed from a canonical time series.

Capacity and energy are always obtained by trapezoidal integration of the
current (and power) over time, never from a source's cumulative capacity
columns. Cyclers disagree on when those counters reset, so integrating is
the only way to get numbers that mean the same thing across datasets.

Integration never spans a step (for step tables) or cycle (for cycle tables)
boundary, nor a gap in the record longer than ``max_gap_s``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import validate

DEFAULT_MAX_GAP_S = 600.0
# A step counts as "dynamic" when at least this fraction of its samples is
# charging and at least this fraction is discharging (e.g. a drive cycle).
_DYNAMIC_FRACTION = 0.05


def rest_threshold(current_a: np.ndarray) -> float:
    """Default |current| below which a sample counts as rest."""
    peak = float(np.nanmax(np.abs(current_a))) if len(current_a) else 0.0
    return max(1e-3, 0.005 * peak)


def sign_class(current_a: np.ndarray, threshold_a: float) -> np.ndarray:
    """+1 for charge, -1 for discharge, 0 for rest, per sample."""
    out = np.zeros(len(current_a), dtype=np.int8)
    out[current_a > threshold_a] = 1
    out[current_a < -threshold_a] = -1
    return out


def assign_step_ids(ts: pd.DataFrame, rest_current_a: float | None = None) -> np.ndarray:
    """Give every sample a step id: a run of constant (cycle, step) key.

    The step key is the source's ``step_index`` where it has one (>= 0), and
    the current sign class otherwise. Step ids start at 0 and increase by one
    at every key change, so a source step number reused in a later cycle still
    gets a fresh id.
    """
    thr = rest_threshold(ts["current_a"].to_numpy()) if rest_current_a is None else rest_current_a
    src_step = ts["step_index"].to_numpy()
    key = np.where(src_step >= 0, src_step, sign_class(ts["current_a"].to_numpy(), thr) - 10)
    cycle = ts["cycle_index"].to_numpy()
    changed = np.empty(len(ts), dtype=bool)
    changed[0] = False
    changed[1:] = (key[1:] != key[:-1]) | (cycle[1:] != cycle[:-1])
    return np.cumsum(changed)


def _increments(ts: pd.DataFrame, group: np.ndarray, max_gap_s: float) -> pd.DataFrame:
    """Per-sample charge/energy increments for the interval ending at each sample."""
    t = ts["test_time_s"].to_numpy()
    i = ts["current_a"].to_numpy()
    p = i * ts["voltage_v"].to_numpy()
    dt = np.diff(t, prepend=t[0])
    valid = np.zeros(len(t), dtype=bool)
    valid[1:] = (group[1:] == group[:-1]) & (dt[1:] <= max_gap_s)
    dq = np.zeros(len(t))
    de = np.zeros(len(t))
    dq[1:] = 0.5 * (i[1:] + i[:-1]) * dt[1:] / 3600.0
    de[1:] = 0.5 * (p[1:] + p[:-1]) * dt[1:] / 3600.0
    dq[~valid] = 0.0
    de[~valid] = 0.0
    return pd.DataFrame(
        {
            "charge_ah": np.clip(dq, 0, None),
            "discharge_ah": np.clip(-dq, 0, None),
            "charge_wh": np.clip(de, 0, None),
            "discharge_wh": np.clip(-de, 0, None),
        }
    )


def _classify(frac_pos: np.ndarray, frac_neg: np.ndarray) -> np.ndarray:
    dyn = (frac_pos >= _DYNAMIC_FRACTION) & (frac_neg >= _DYNAMIC_FRACTION)
    chg = ~dyn & (frac_pos >= _DYNAMIC_FRACTION) & (frac_pos >= frac_neg)
    dis = ~dyn & ~chg & (frac_neg >= _DYNAMIC_FRACTION)
    return np.select([dyn, chg, dis], ["dynamic", "charge", "discharge"], default="rest")


def step_capacity(
    ts: pd.DataFrame,
    rest_current_a: float | None = None,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> pd.DataFrame:
    """Per-sample ``step_id`` and Ah moved since the start of that step.

    ``q_ah`` is the running integral of |current|, so it rises from 0 in
    every charge and discharge step. Plot ``voltage_v`` against it to get a
    step's V-Q curve.
    """
    validate(ts)
    thr = rest_threshold(ts["current_a"].to_numpy()) if rest_current_a is None else rest_current_a
    step_id = assign_step_ids(ts, thr)
    inc = _increments(ts, step_id, max_gap_s)
    moved = (inc["charge_ah"] + inc["discharge_ah"]).to_numpy()
    q = pd.Series(moved).groupby(step_id).cumsum().to_numpy()
    return pd.DataFrame({"step_id": step_id, "q_ah": q}, index=ts.index)


def step_table(
    ts: pd.DataFrame,
    rest_current_a: float | None = None,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> pd.DataFrame:
    """One row per step: timing, current/voltage/temperature stats, Ah and Wh."""
    validate(ts)
    thr = rest_threshold(ts["current_a"].to_numpy()) if rest_current_a is None else rest_current_a
    step_id = assign_step_ids(ts, thr)
    sgn = sign_class(ts["current_a"].to_numpy(), thr)
    work = pd.concat(
        [ts[list(ts.columns[:6])], _increments(ts, step_id, max_gap_s)], axis=1
    ).assign(step_id=step_id, _pos=sgn == 1, _neg=sgn == -1)

    g = work.groupby("step_id", sort=True)
    out = pd.DataFrame(
        {
            "cycle_index": g["cycle_index"].first(),
            "step_index": g["step_index"].first(),
            "start_time_s": g["test_time_s"].first(),
            "end_time_s": g["test_time_s"].last(),
            "n_points": g.size(),
            "current_mean_a": g["current_a"].mean(),
            "current_min_a": g["current_a"].min(),
            "current_max_a": g["current_a"].max(),
            "voltage_start_v": g["voltage_v"].first(),
            "voltage_end_v": g["voltage_v"].last(),
            "voltage_min_v": g["voltage_v"].min(),
            "voltage_max_v": g["voltage_v"].max(),
            "charge_ah": g["charge_ah"].sum(),
            "discharge_ah": g["discharge_ah"].sum(),
            "charge_wh": g["charge_wh"].sum(),
            "discharge_wh": g["discharge_wh"].sum(),
            "temperature_mean_c": g["temperature_c"].mean(),
            "temperature_max_c": g["temperature_c"].max(),
        }
    )
    out.insert(2, "step_type", _classify(g["_pos"].mean().to_numpy(), g["_neg"].mean().to_numpy()))
    out.insert(5, "duration_s", out["end_time_s"] - out["start_time_s"])
    return out.reset_index()


def cycle_table(
    ts: pd.DataFrame,
    rest_current_a: float | None = None,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> pd.DataFrame:
    """One row per cycle: Ah/Wh throughput, efficiencies, voltage and temperature range.

    Coulombic efficiency is discharge Ah / charge Ah *within the cycle*, so its
    meaning depends on whether the source starts a cycle with charge or
    discharge. Check the loader's docstring before reading too much into it.
    """
    validate(ts)
    cycle = ts["cycle_index"].to_numpy()
    work = pd.concat([ts[list(ts.columns[:6])], _increments(ts, cycle, max_gap_s)], axis=1)
    steps = step_table(ts, rest_current_a, max_gap_s)

    g = work.groupby("cycle_index", sort=True)
    out = pd.DataFrame(
        {
            "start_time_s": g["test_time_s"].first(),
            "end_time_s": g["test_time_s"].last(),
            "n_steps": steps.groupby("cycle_index").size(),
            "charge_ah": g["charge_ah"].sum(),
            "discharge_ah": g["discharge_ah"].sum(),
            "charge_wh": g["charge_wh"].sum(),
            "discharge_wh": g["discharge_wh"].sum(),
            "voltage_min_v": g["voltage_v"].min(),
            "voltage_max_v": g["voltage_v"].max(),
            "temperature_mean_c": g["temperature_c"].mean(),
            "temperature_max_c": g["temperature_c"].max(),
        }
    )
    out.insert(2, "duration_s", out["end_time_s"] - out["start_time_s"])
    with np.errstate(divide="ignore", invalid="ignore"):
        out["coulombic_efficiency"] = np.where(
            out["charge_ah"] > 0, out["discharge_ah"] / out["charge_ah"], np.nan
        )
        out["energy_efficiency"] = np.where(
            out["charge_wh"] > 0, out["discharge_wh"] / out["charge_wh"], np.nan
        )
    return out.reset_index()
