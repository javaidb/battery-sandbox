"""Arbin MITS exports (CSV, XLSX or XLS). Covers the native CALCE files.

``path`` may be one export or a directory of exports for the same cell
(CALCE splits a test across many dated files). Files are ordered by their
first ``Date_Time`` stamp when present, otherwise by name. Cycle numbers
restart in each file, so later files are offset to keep cycles unique, and
test time is rebuilt from ``Date_Time`` (or stacked end-to-end without it).

In XLSX/XLS workbooks every sheet whose name starts with ``Channel`` is read.
Arbin reports discharge current as negative.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..core import CellData, register
from ._util import find_column

_SUFFIXES = {".csv", ".xlsx", ".xls"}


def _read_one(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    sheets = pd.read_excel(path, sheet_name=None)
    data = [df for name, df in sheets.items() if str(name).lower().startswith("channel")]
    if not data:
        raise ValueError(f"{path}: no 'Channel*' sheet found (sheets: {list(sheets)})")
    return pd.concat(data, ignore_index=True)


def _canonical(raw: pd.DataFrame) -> pd.DataFrame:
    temp_col = find_column(
        raw,
        ["Temperature (C)_1", "Aux_Temperature_1(C)", "Temperature(C)", "Temperature (C)"],
        required=False,
    )
    date_col = find_column(raw, ["Date_Time", "DateTime"], required=False)
    out = pd.DataFrame(
        {
            "test_time_s": raw[find_column(raw, ["Test_Time(s)", "Test_Time"])].astype(float),
            "cycle_index": raw[find_column(raw, ["Cycle_Index"])].astype(int),
            "step_index": raw[find_column(raw, ["Step_Index"])].astype(int),
            "current_a": raw[find_column(raw, ["Current(A)", "Current"])].astype(float),
            "voltage_v": raw[find_column(raw, ["Voltage(V)", "Voltage"])].astype(float),
            "temperature_c": raw[temp_col].astype(float) if temp_col else np.nan,
        }
    )
    if date_col:
        out["_abs_start"] = pd.to_datetime(raw[date_col].iloc[0]) - pd.to_timedelta(
            out["test_time_s"].iloc[0], unit="s"
        )
    return out.sort_values("test_time_s", kind="stable").reset_index(drop=True)


@register("arbin")
def load_arbin(path: Path) -> CellData:
    files = (
        sorted(p for p in path.iterdir() if p.suffix.lower() in _SUFFIXES)
        if path.is_dir()
        else [path]
    )
    if not files:
        raise FileNotFoundError(f"no Arbin exports in {path}")
    parts = [_canonical(_read_one(f)) for f in files]
    if all("_abs_start" in p for p in parts):
        parts.sort(key=lambda p: p["_abs_start"].iloc[0])
        origin = parts[0]["_abs_start"].iloc[0]
        for p in parts:
            p["test_time_s"] += (p["_abs_start"].iloc[0] - origin).total_seconds()

    cycle_offset, time_offset, chunks = 0, 0.0, []
    for p in parts:
        p = p.drop(columns="_abs_start", errors="ignore")
        p["cycle_index"] += cycle_offset
        if chunks and p["test_time_s"].iloc[0] < time_offset:
            # No usable wall clock: splice this file on after the previous one.
            p["test_time_s"] += time_offset - p["test_time_s"].iloc[0]
        cycle_offset = int(p["cycle_index"].max())
        time_offset = float(p["test_time_s"].iloc[-1])
        chunks.append(p)

    meta = {"source": "arbin", "path": str(path), "files": [f.name for f in files]}
    return CellData(pd.concat(chunks, ignore_index=True), meta)
