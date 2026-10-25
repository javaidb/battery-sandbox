"""NASA PCoE Li-ion battery aging dataset (B0005.mat, B0006.mat, ...).

Each file holds a ``cycle`` struct array of ``charge``, ``discharge`` and
``impedance`` records, each with a ``time`` start stamp (MATLAB datevec) and
a ``data`` struct. Records are placed on one time axis using their start
stamps; a record that would overlap the previous one is pushed to start
right after it.

A cycle here is a charge record plus the discharge records that follow it,
so ``cycle_index`` increases at every charge. Steps are derived from the
current sign, which splits each record into its active part and the
relaxation at its end.

Extras:
* ``eis_summary``: one row per impedance record with fitted ``Re``/``Rct``.
* ``eis``: the ``Rectified_Impedance`` spectra in long form.
* ``reported_capacity``: NASA's own discharge capacity per discharge record,
  useful for cross-checking the integrated capacity.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import loadmat

from ..core import CellData, register


def _datevec(v) -> datetime:
    y, mo, d, h, mi, s = (float(x) for x in np.ravel(v))
    return datetime(int(y), int(mo), int(d), int(h), int(mi)) + timedelta(seconds=s)


def _top_struct(mat: dict):
    keys = [k for k in mat if not k.startswith("__")]
    if len(keys) != 1:
        raise ValueError(f"expected one variable in NASA .mat file, found {keys}")
    return keys[0], mat[keys[0]]


@register("nasa_pcoe")
def load_nasa_pcoe(path: Path) -> CellData:
    name, cell = _top_struct(loadmat(path, squeeze_me=True, struct_as_record=False))
    records = np.atleast_1d(cell.cycle)

    chunks, eis_rows, eis_points, capacity_rows = [], [], [], []
    origin: datetime | None = None
    prev_end = -np.inf
    cycle = 0
    for rec_no, rec in enumerate(records):
        kind = str(rec.type).strip().lower()
        start = _datevec(rec.time)
        origin = origin or start
        start_s = (start - origin).total_seconds()
        d = rec.data
        if kind == "charge":
            cycle += 1
        if kind == "impedance":
            eis_rows.append(
                {
                    "record": rec_no,
                    "cycle_index": cycle,
                    "test_time_s": start_s,
                    "ambient_temperature_c": float(rec.ambient_temperature),
                    "re_ohm": float(np.real(d.Re)),
                    "rct_ohm": float(np.real(d.Rct)),
                }
            )
            z = np.ravel(d.Rectified_Impedance)
            eis_points.append(
                pd.DataFrame(
                    {"record": rec_no, "point": np.arange(len(z)), "z_real_ohm": z.real, "z_imag_ohm": z.imag}
                )
            )
            continue

        t = np.ravel(d.Time).astype(float)
        start_s = max(start_s, prev_end + 1.0) if np.isfinite(prev_end) else start_s
        current = np.abs(np.ravel(d.Current_measured).astype(float))
        if kind == "discharge":
            current = -current
            capacity_rows.append({"record": rec_no, "cycle_index": cycle, "capacity_ah": float(d.Capacity)})
        chunks.append(
            pd.DataFrame(
                {
                    "test_time_s": start_s + t - t[0],
                    "cycle_index": cycle,
                    "current_a": current,
                    "voltage_v": np.ravel(d.Voltage_measured).astype(float),
                    "temperature_c": np.ravel(d.Temperature_measured).astype(float),
                    "record": rec_no,
                }
            )
        )
        prev_end = chunks[-1]["test_time_s"].iloc[-1]

    meta = {
        "source": "nasa_pcoe",
        "path": str(path),
        "cell_id": name,
        "chemistry": None,  # not documented by NASA; 18650 Li-ion
        "nominal_capacity_ah": 2.0,
        "test_start": origin.isoformat() if origin else None,
    }
    extras = {
        "eis_summary": pd.DataFrame(eis_rows),
        "eis": pd.concat(eis_points, ignore_index=True) if eis_points else pd.DataFrame(),
        "reported_capacity": pd.DataFrame(capacity_rows),
    }
    return CellData(pd.concat(chunks, ignore_index=True), meta, extras)
