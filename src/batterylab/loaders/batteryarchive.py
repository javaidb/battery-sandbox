"""batteryarchive.org standardized time-series CSV.

batteryarchive.org republishes many public datasets in one format: Sandia
(SNL), Severson/Attia MIT-Stanford-TRI (MATR), CALCE, Oxford (OX), HNEI and
others. Download the ``*_timeseries.csv`` file for a cell.

Columns used: ``Test_Time (s)``, ``Cycle_Index``, ``Current (A)``,
``Voltage (V)`` and ``Cell_Temperature (C)`` (falling back to
``Environment_Temperature (C)``). Discharge current is already negative.
The format carries no step numbers, so steps are derived from current sign.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..core import CellData, register
from ._util import find_column, unwrap_time

# Cell IDs end in test conditions, e.g. SNL_18650_NCA_25C_0-100_0.5/1C_a or
# CALCE_CX2-16_prism_LCO_25C_0-100_0.5/0.5C_a. In file names the "/" between
# charge and discharge C-rate becomes "-".
_CONDITIONS = re.compile(
    r"_(?P<temp>-?[\d.]+)C_(?P<soc_min>[\d.]+)-(?P<soc_max>[\d.]+)_"
    r"(?P<c_chg>[\d.]+)[-/](?P<c_dis>[\d.]+)C_(?P<rep>[A-Za-z0-9]+)$"
)
_CHEMISTRIES = ("LFP", "NCA", "NMC", "LCO", "LMO", "LTO")
_FORM_FACTORS = ("18650", "21700", "26650", "pouch", "prism", "prismatic")


def parse_name(name: str) -> dict:
    """Pull test conditions out of a batteryarchive cell ID or file name."""
    cell_id = re.sub(r"_(timeseries|cycle_data)$", "", name)
    meta: dict = {"cell_id": cell_id, "group": cell_id.split("_", 1)[0]}
    m = _CONDITIONS.search(cell_id)
    if not m:
        return meta
    d = m.groupdict()
    tokens = cell_id[: m.start()].split("_")[1:]
    chem = [t for t in tokens if t.upper() in _CHEMISTRIES]
    form = [t for t in tokens if t.lower() in _FORM_FACTORS]
    meta.update(
        form_factor=form[0] if form else None,
        chemistry="-".join(chem) or None,
        ambient_temperature_c=float(d["temp"]),
        soc_window=(float(d["soc_min"]), float(d["soc_max"])),
        c_rate_charge=float(d["c_chg"]),
        c_rate_discharge=float(d["c_dis"]),
        replicate=d["rep"],
    )
    return meta


@register("batteryarchive")
def load_batteryarchive(path: Path) -> CellData:
    raw = pd.read_csv(path)
    t_col = find_column(raw, ["Test_Time (s)", "test_time"])
    temp_col = find_column(
        raw, ["Cell_Temperature (C)", "Environment_Temperature (C)"], required=False
    )
    ts = pd.DataFrame(
        {
            "test_time_s": unwrap_time(raw[t_col].to_numpy()),
            "cycle_index": raw[find_column(raw, ["Cycle_Index"])].to_numpy(),
            "current_a": raw[find_column(raw, ["Current (A)"])].to_numpy(),
            "voltage_v": raw[find_column(raw, ["Voltage (V)"])].to_numpy(),
            "temperature_c": raw[temp_col].to_numpy() if temp_col else np.nan,
        }
    ).dropna(subset=["current_a", "voltage_v"])
    meta = {"source": "batteryarchive", "path": str(path), **parse_name(Path(path).stem)}
    return CellData(ts, meta)
