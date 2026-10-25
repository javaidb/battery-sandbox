"""Panel registry: how workbench code shows up in the dashboard."""

from __future__ import annotations

import importlib
import pkgutil
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

ParamSpec = tuple | list | bool


@dataclass
class Panel:
    key: str
    title: str
    module: str
    fn: Callable[..., Any]
    params: dict[str, ParamSpec] = field(default_factory=dict)
    description: str = ""


PANELS: dict[str, Panel] = {}
ESTIMATORS: dict[str, Callable[[], Any]] = {}
ECMS: dict[str, Callable[..., Any]] = {}


def panel(title: str, module: str = "misc", params: dict[str, ParamSpec] | None = None):
    """Register ``fn(cell, **params)`` as a dashboard panel.

    ``module`` groups panels in the dashboard ("A1", "B2", ...).

    ``params`` maps keyword names to widget specs:

    * ``(min, max, default)``: a slider (ints give an int slider)
    * ``[option, ...]``: a dropdown, defaulting to the first option
    * ``True`` / ``False``: a checkbox with that default

    The function returns one item or a list of items. Each item can be a
    plotly or matplotlib figure, a pandas DataFrame, a dict (shown as JSON)
    or a string (rendered as Markdown).
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        key = f"{fn.__module__}.{fn.__name__}"
        PANELS[key] = Panel(key, title, module, fn, dict(params or {}), (fn.__doc__ or "").strip())
        return fn

    return deco


def estimator(name: str):
    """Register a SoC estimator class for the Algorithms test bench.

    The class needs a no-argument constructor and two methods:

    * ``reset(ctx)``: called once. ``ctx`` holds what a real BMS knows:
      ``soc0_guess``, ``nominal_capacity_ah``, ``ocv_curve`` (DataFrame of
      ``soc``/``ocv_v`` measured on a fresh cell), ``dt_s``, ``v_min``,
      ``v_max`` and ``chemistry``.
    * ``step(t, current_a, voltage_v, temperature_c)``: called once per
      sample, current positive on charge. Return a SoC float, or a dict
      with ``"soc"`` plus optional ``"soc_std"`` (1-sigma, drawn as a 3-sigma
      band) and any other states you want to inspect.
    """

    def deco(cls):
        ESTIMATORS[name] = cls
        return cls

    return deco


def ecm(name: str):
    """Register an equivalent-circuit model for the Algorithms test bench.

    ``fn(t, current_a, temperature_c, ctx) -> voltage_v`` takes NumPy arrays
    (current positive on charge) and returns the predicted terminal voltage.
    ``ctx`` is the estimator context plus ``soc0`` (the *true* initial
    SoC, since this checks the model, not an estimator).
    """

    def deco(fn):
        ECMS[name] = fn
        return fn

    return deco


def discover() -> tuple[dict[str, Panel], dict[str, str]]:
    """Import every module in ``workbench`` and return (panels, import errors)."""
    import workbench

    errors: dict[str, str] = {}
    for reg in (PANELS, ESTIMATORS, ECMS):
        reg.clear()  # so renamed or deleted entries disappear
    for info in pkgutil.iter_modules(workbench.__path__):
        if info.name.startswith("_"):
            continue
        name = f"workbench.{info.name}"
        try:
            mod = importlib.import_module(name)
            importlib.reload(mod)  # pick up edits without restarting the dashboard
        except Exception:
            errors[name] = traceback.format_exc()
    return PANELS, errors
