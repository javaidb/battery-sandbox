"""Oxford Battery Degradation Dataset 1 (Birkl, 2017).

One .mat file holds eight Kokam 740 mAh pouch cells (``Cell1`` ... ``Cell8``).
Only characterisation runs are recorded, every 100 cycles (``cyc0000``,
``cyc0100``, ...), each with four segments: ``C1ch``/``C1dc`` (1C) and
``OCVch``/``OCVdc`` (pseudo-OCV, C/18). Each segment has ``t``, ``v``,
``q`` (mAh) and ``T``.

``cycle_index`` is the ageing cycle number from the field name (0, 100, ...),
and each segment becomes one step (``step_index`` 0-3 in the order above).

The file stores charge, not current, so current is the time derivative of
``q``; its sign comes from the segment name (``ch`` charge, ``dc`` discharge).

The dataset README says ``t`` is in seconds, but in the published file it is
a MATLAB datenum (days): a 1C charge spans ~0.04. ``time_unit="auto"``
detects this from the span of the segments. Within each characterisation
the 1C discharge comes first in time, so steps appear in time order
1, 0, 3, 2 rather than by ``step_index``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import loadmat

from ..core import CellData, register

SEGMENTS = ("C1ch", "C1dc", "OCVch", "OCVdc")


def _repair_overlaps(segs: list) -> tuple[list, list[str]]:
    """Fix the published file's overlapping segments; report what changed.

    The file has known defects (e.g. Cell1 ``cyc7100`` is an exact copy of
    ``cyc7000``, and Cell1 ``cyc3200`` pseudo-OCV segments have ``cyc3300``'s
    appended). A segment whose time vector equals an earlier one is dropped;
    every other segment is cut where the next segment in time starts.
    """
    segs = sorted(segs, key=lambda s: (s[3][0], s[0]))
    kept, issues = [], []
    for seg in segs:
        cycle, _, sname, t = seg[:4]
        dup = next((k for k in kept if len(k[3]) == len(t) and np.array_equal(k[3], t)), None)
        if dup is not None:
            issues.append(f"cyc{cycle:04d} {sname}: duplicate of cyc{dup[0]:04d} {dup[2]}, dropped")
            continue
        kept.append(seg)
    starts = np.array([s[3][0] for s in kept])
    out = []
    for seg in kept:
        cycle, step, sname, t, *rest = seg
        later = starts[starts > t[0]]
        if later.size and t[-1] >= later.min():
            keep = t < later.min()
            issues.append(f"cyc{cycle:04d} {sname}: {int((~keep).sum())} samples overlap the next segment, cut")
            t, rest = t[keep], [a[keep] for a in rest]
        out.append((cycle, step, sname, t, *rest))
    return out, issues


@register("oxford")
def load_oxford(path: Path, cell: str = "Cell1", time_unit: str = "auto") -> CellData:
    mat = loadmat(path, squeeze_me=True, struct_as_record=False)
    if cell not in mat:
        cells = [k for k in mat if not k.startswith("__")]
        raise KeyError(f"{cell!r} not in {path.name}; available: {cells}")
    data = mat[cell]
    cyc_names = sorted(f for f in data._fieldnames if f.startswith("cyc"))

    segs = []
    for cname in cyc_names:
        cyc = getattr(data, cname)
        for step, sname in enumerate(SEGMENTS):
            if sname not in cyc._fieldnames:
                continue
            s = getattr(cyc, sname)
            segs.append((int(cname[3:]), step, sname, *(np.ravel(getattr(s, f)).astype(float) for f in "tvqT")))
    segs, issues = _repair_overlaps(segs)

    if time_unit == "auto":
        median_span = np.median([t[-1] - t[0] for _, _, _, t, *_ in segs])
        # A 1C segment lasts ~1 h: a median span < 10 means the unit is days.
        time_unit = "days" if median_span < 10 else "seconds"
    scale = 86400.0 if time_unit == "days" else 1.0

    t0 = min(t[0] for _, _, _, t, *_ in segs)
    chunks = []
    for cycle, step, sname, t, v, q, temp in segs:
        ts = (t - t0) * scale
        q_ah = q / 1000.0
        current = np.abs(np.gradient(q_ah, ts / 3600.0)) if len(ts) > 1 else np.zeros_like(ts)
        if sname.endswith("dc"):
            current = -current
        chunks.append(
            pd.DataFrame(
                {
                    "test_time_s": ts,
                    "cycle_index": cycle,
                    "step_index": step,
                    "current_a": current,
                    "voltage_v": v,
                    "temperature_c": temp,
                    "source_q_ah": q_ah,
                }
            )
        )
    ts_all = pd.concat(chunks, ignore_index=True).sort_values("test_time_s", kind="stable")
    meta = {
        "source": "oxford",
        "path": str(path),
        "cell_id": cell,
        "chemistry": "LCO/NCO blend, graphite",
        "nominal_capacity_ah": 0.74,
        "time_unit_detected": time_unit,
        "data_issues": issues,
    }
    return CellData(ts_all, meta)
