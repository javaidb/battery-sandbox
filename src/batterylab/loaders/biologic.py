"""BioLogic EC-Lab ``.mpr`` files, read with ``galvani``.

The BioLogic sequence number ``Ns`` becomes ``step_index``. ``cycle number``
is used when the technique writes it, otherwise the whole file is cycle 0.
Current is in mA in the file and positive on oxidation of the working
electrode, which is charging when the cathode is the working electrode (the
usual full-cell wiring). Pass ``invert_current=True`` for the opposite wiring.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..core import CellData, register
from ._util import find_column


@register("biologic")
def load_biologic(path: Path, invert_current: bool = False) -> CellData:
    try:
        from galvani import BioLogic
    except ImportError as e:  # pragma: no cover - depends on optional install
        raise ImportError("install galvani to read BioLogic files: pip install galvani") from e

    raw = pd.DataFrame(BioLogic.MPRfile(str(path)).data)
    cur = raw[find_column(raw, ["I/mA", "<I>/mA", "control/V/mA"])].astype(float) / 1000.0
    cyc_col = find_column(raw, ["cycle number"], required=False)
    step_col = find_column(raw, ["Ns"], required=False)
    temp_col = find_column(raw, ["Temperature/°C", "Temperature/C"], required=False)
    ts = pd.DataFrame(
        {
            "test_time_s": raw[find_column(raw, ["time/s"])].astype(float),
            "cycle_index": raw[cyc_col].astype(int) if cyc_col else 0,
            "step_index": raw[step_col].astype(int) if step_col else -1,
            "current_a": -cur if invert_current else cur,
            "voltage_v": raw[find_column(raw, ["Ewe/V", "<Ewe>/V", "Ecell/V"])].astype(float),
            "temperature_c": raw[temp_col].astype(float) if temp_col else np.nan,
        }
    )
    return CellData(ts, {"source": "biologic", "path": str(path), "cell_id": path.stem})
