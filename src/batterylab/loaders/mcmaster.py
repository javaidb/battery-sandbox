"""McMaster LG HG2 dataset (Kollmeyer et al., Mendeley Data, 2020).

Tests on a 3 Ah LG 18650HG2 at -20 to 40 degC: HPPC pulses, C/20 OCV and drive
cycles (UDDS, HWFET, LA92, US06, mixes). Two file flavours are supported:

* ``.mat``: a ``meas`` struct with ``Time`` (s), ``Voltage``, ``Current``
  and ``Battery_Temp_degC``. It has no step numbers, so steps come from the
  current sign; pass ``steps="single"`` to keep a drive cycle as one step
  (it is then classed ``dynamic``).
* ``.csv``: the Digatron export with a metadata preamble, a ``Time Stamp``
  header row, a units row, and ``Prog Time``/``Step``/``Voltage``/
  ``Current``/``Temperature`` columns. Its own ``Step`` numbers are used.

Discharge current is negative in both. Each file is a single test, loaded
as ``cycle_index`` 0.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import loadmat

from ..core import CellData, register
from ._util import find_column, unwrap_time


def _from_mat(path: Path, steps: str) -> pd.DataFrame:
    meas = loadmat(path, squeeze_me=True, struct_as_record=False)["meas"]
    temp = getattr(meas, "Battery_Temp_degC", None)
    return pd.DataFrame(
        {
            "test_time_s": unwrap_time(np.ravel(meas.Time).astype(float)),
            "cycle_index": 0,
            "step_index": 0 if steps == "single" else -1,
            "current_a": np.ravel(meas.Current).astype(float),
            "voltage_v": np.ravel(meas.Voltage).astype(float),
            "temperature_c": np.ravel(temp).astype(float) if temp is not None else np.nan,
        }
    )


def _parse_prog_time(s: pd.Series) -> np.ndarray:
    """``Prog Time`` is ``h:mm:ss.sss`` where hours can exceed 24."""
    parts = s.astype(str).str.strip().str.split(":", expand=True).astype(float)
    return (parts[0] * 3600 + parts[1] * 60 + parts[2]).to_numpy()


def _from_csv(path: Path, steps: str) -> pd.DataFrame:
    with open(path, encoding="utf-8", errors="replace") as fh:
        header_row = next(i for i, line in enumerate(fh) if line.lstrip('"').startswith("Time Stamp"))
    raw = pd.read_csv(path, skiprows=header_row, low_memory=False)
    raw = raw.iloc[1:]  # units row
    temp_col = find_column(raw, ["Temperature"], required=False)
    step_col = find_column(raw, ["Step"])
    return pd.DataFrame(
        {
            "test_time_s": unwrap_time(_parse_prog_time(raw[find_column(raw, ["Prog Time"])])),
            "cycle_index": 0,
            "step_index": 0 if steps == "single" else raw[step_col].astype(int).to_numpy(),
            "current_a": raw[find_column(raw, ["Current"])].astype(float).to_numpy(),
            "voltage_v": raw[find_column(raw, ["Voltage"])].astype(float).to_numpy(),
            "temperature_c": raw[temp_col].astype(float).to_numpy() if temp_col else np.nan,
        }
    )


@register("mcmaster_hg2")
def load_mcmaster_hg2(path: Path, steps: str = "auto") -> CellData:
    if steps not in ("auto", "single"):
        raise ValueError("steps must be 'auto' or 'single'")
    reader = _from_mat if path.suffix.lower() == ".mat" else _from_csv
    meta = {
        "source": "mcmaster_hg2",
        "path": str(path),
        "cell_id": path.stem,
        "chemistry": "NMC/graphite-SiOx",
        "nominal_capacity_ah": 3.0,
    }
    return CellData(reader(path, steps), meta)
