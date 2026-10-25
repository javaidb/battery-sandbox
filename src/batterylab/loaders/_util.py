"""Helpers shared by loaders."""

from __future__ import annotations

import re
from typing import Iterable

import numpy as np
import pandas as pd


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def find_column(df: pd.DataFrame, candidates: Iterable[str], required: bool = True) -> str | None:
    """Return the first column of ``df`` matching any candidate.

    Matching ignores case, spaces, brackets and punctuation, so
    ``"Test_Time (s)"`` matches ``"test time(s)"``.
    """
    lookup = {_norm(c): c for c in df.columns}
    for cand in candidates:
        hit = lookup.get(_norm(cand))
        if hit is not None:
            return hit
    if required:
        raise KeyError(f"none of {list(candidates)} found in columns {list(df.columns)}")
    return None


def unwrap_time(t: np.ndarray) -> np.ndarray:
    """Make a time vector monotonic by stacking segments where it resets.

    Some exports restart test time at zero partway through (a new file, a
    resumed schedule). Each reset is spliced on at the previous end time.
    """
    t = np.asarray(t, dtype=float).copy()
    resets = np.flatnonzero(np.diff(t) < 0) + 1
    for k in resets:
        t[k:] += t[k - 1] - t[k]
    return t
