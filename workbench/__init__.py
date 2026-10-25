"""Your module work (A1-A5, B1-B5, capstone) lives in this package.

Any module here that registers a function with ``@panel`` appears on the
dashboard's Workbench page, run against whichever cell is selected. See
``example_retention.py`` for the pattern and ``README.md`` for the details.
"""

from ._registry import ECMS, ESTIMATORS, PANELS, Panel, discover, ecm, estimator, panel

__all__ = ["ECMS", "ESTIMATORS", "PANELS", "Panel", "discover", "ecm", "estimator", "panel"]
