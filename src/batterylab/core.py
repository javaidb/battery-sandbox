"""CellData container, loader registry and the top-level ``load``/``tidy`` API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import tables
from .schema import conform


@dataclass
class CellData:
    """One cell's test record in canonical form.

    ``timeseries`` follows :mod:`batterylab.schema`. ``metadata`` holds
    free-form facts about the cell (chemistry, nominal capacity, source,
    ground truth for synthetic data, ...). ``extras`` holds any other tables
    a source provides, such as EIS spectra.
    """

    timeseries: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, pd.DataFrame] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.timeseries = conform(self.timeseries)

    def steps(self, **kwargs: Any) -> pd.DataFrame:
        return tables.step_table(self.timeseries, **kwargs)

    def cycles(self, **kwargs: Any) -> pd.DataFrame:
        return tables.cycle_table(self.timeseries, **kwargs)


Loader = Callable[..., CellData]
_LOADERS: dict[str, Loader] = {}


def register(name: str) -> Callable[[Loader], Loader]:
    """Decorator that registers a loader under ``name``."""

    def deco(fn: Loader) -> Loader:
        _LOADERS[name] = fn
        return fn

    return deco


def available_sources() -> list[str]:
    return sorted(_LOADERS)


def load(source: str, path: str | Path | None = None, **kwargs: Any) -> CellData:
    """Load one cell from ``path`` using the loader registered as ``source``.

    ``path`` is unused by sources that generate data (``"pybamm"``).
    """
    try:
        loader = _LOADERS[source]
    except KeyError:
        raise ValueError(f"unknown source {source!r}; choose from {available_sources()}") from None
    if path is None:
        return loader(**kwargs)
    return loader(Path(path), **kwargs)


def tidy(
    source: str,
    path: str | Path | None = None,
    *,
    rest_current_a: float | None = None,
    max_gap_s: float = tables.DEFAULT_MAX_GAP_S,
    **kwargs: Any,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load any supported dataset and return ``(cycles, steps)`` tables."""
    cell = load(source, path, **kwargs)
    opts = {"rest_current_a": rest_current_a, "max_gap_s": max_gap_s}
    return cell.cycles(**opts), cell.steps(**opts)
