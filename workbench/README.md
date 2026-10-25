# workbench

This is where your module work goes. Each file is ordinary Python you can import from tests,
notebooks or scripts. Decorating a function with `@panel` also puts it on
the dashboard's **Workbench** page.

```python
from batterylab import CellData
from ._registry import panel

@panel("ICA peaks", module="A1", params={"window": (5, 101, 21)})
def ica_peaks(cell: CellData, window: int):
    ...
    return [fig, table]
```

* The function receives the selected cell, plus one keyword argument per
  entry in `params`, filled in from the widgets.
* Widget specs: `(min, max, default)` gives a slider, a list gives a
  dropdown, and `True`/`False` gives a checkbox.
* It returns one item or a list of items. An item can be a plotly or
  matplotlib figure, a DataFrame, a dict (shown as JSON) or a Markdown
  string.
* The dashboard re-imports the package on every run, so your edits show up
  when you hit **Rerun**. Import errors and exceptions are shown with
  their traceback instead of crashing the page.

Keep the analysis in plain functions and make the panel a thin wrapper
around them. That way the same code can be unit-tested and, later, run
against PyBaMM ground truth or ported to C in module B4.

## Estimators and ECMs (Algorithms view)

```python
from ._registry import estimator, ecm

@estimator("EKF (1RC)")
class EKF:
    def reset(self, ctx):  # ctx: soc0_guess, nominal_capacity_ah, ocv_curve, dt_s, v_min, v_max, chemistry
        ...
    def step(self, t, current_a, voltage_v, temperature_c):
        ...
        return {"soc": soc, "soc_std": sigma}  # soc_std is optional, drawn as a ±3σ band

@ecm("2RC, fitted on HPPC")
def two_rc(t, current_a, temperature_c, ctx):  # NumPy arrays in, voltage array out
    ...
```

Current is positive on charge. The estimator never sees the plant's true
capacity or ageing: `ctx["ocv_curve"]` comes from a fresh cell at 25 °C,
exactly the mismatch a real BMS faces. `example_coulomb_counting.py`
shows the interface; delete it when you have your own.
