"""Canonical time-series schema shared by every loader.

Every loader returns a DataFrame with exactly these columns (extra
source-specific columns are allowed after them). Conventions:

* SI-ish units: seconds, amperes, volts, amp-hours, degrees Celsius.
* Current is **positive on charge, negative on discharge**.
* ``test_time_s`` is monotonically non-decreasing over the whole test.
* ``cycle_index`` and ``step_index`` are integers; ``step_index`` is the
  source's own step number when it has one, otherwise -1 (steps are then
  derived from the current sign, see :mod:`batterylab.tables`).
* ``temperature_c`` may be NaN when the source has no thermocouple.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TIMESERIES_COLUMNS: dict[str, str] = {
    "test_time_s": "float64",
    "cycle_index": "int64",
    "step_index": "int64",
    "current_a": "float64",
    "voltage_v": "float64",
    "temperature_c": "float64",
}

STEP_TYPES = ("charge", "discharge", "rest", "dynamic")


class SchemaError(ValueError):
    """Raised when a time-series table violates the canonical schema."""


def conform(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` with canonical columns first, cast to canonical dtypes.

    Missing ``temperature_c`` is filled with NaN and missing ``step_index``
    with -1; any other missing canonical column is an error.
    """
    df = df.copy()
    if "temperature_c" not in df:
        df["temperature_c"] = np.nan
    if "step_index" not in df:
        df["step_index"] = -1
    missing = [c for c in TIMESERIES_COLUMNS if c not in df]
    if missing:
        raise SchemaError(f"missing canonical columns: {missing}")
    df = df.astype(TIMESERIES_COLUMNS)
    extra = [c for c in df.columns if c not in TIMESERIES_COLUMNS]
    df = df[list(TIMESERIES_COLUMNS) + extra].reset_index(drop=True)
    validate(df)
    return df


def validate(df: pd.DataFrame) -> None:
    """Raise :class:`SchemaError` if ``df`` breaks a schema invariant."""
    for col, dtype in TIMESERIES_COLUMNS.items():
        if col not in df:
            raise SchemaError(f"missing column {col!r}")
        if str(df[col].dtype) != dtype:
            raise SchemaError(f"column {col!r} has dtype {df[col].dtype}, expected {dtype}")
    if len(df) == 0:
        raise SchemaError("time series is empty")
    t = df["test_time_s"].to_numpy()
    if np.isnan(t).any():
        raise SchemaError("test_time_s contains NaN")
    if (np.diff(t) < 0).any():
        raise SchemaError("test_time_s is not monotonically non-decreasing")
    for col in ("current_a", "voltage_v"):
        if np.isnan(df[col].to_numpy()).any():
            raise SchemaError(f"{col} contains NaN")
