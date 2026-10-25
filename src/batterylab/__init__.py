"""batterylab: load battery cycler data into one tidy, canonical format."""

from . import loaders, synthetic  # noqa: F401  (registers sources)
from .core import CellData, available_sources, load, tidy
from .schema import TIMESERIES_COLUMNS, SchemaError
from .tables import cycle_table, step_table

__all__ = [
    "CellData",
    "SchemaError",
    "TIMESERIES_COLUMNS",
    "available_sources",
    "cycle_table",
    "load",
    "step_table",
    "tidy",
]
__version__ = "0.1.0"
