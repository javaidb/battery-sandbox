"""Example panel: shows the pattern, not part of any module.

Copy this file to start a module, e.g. ``a1_ica.py``, and replace the body
with your own analysis.
"""

import plotly.graph_objects as go

from batterylab import CellData

from ._registry import panel


@panel(
    "Capacity retention (example)",
    module="example",
    params={"step_type": ["discharge", "charge"], "min_fraction": (0.0, 0.5, 0.2)},
)
def retention(cell: CellData, step_type: str, min_fraction: float):
    """Each step's capacity relative to the first full step of that type.

    Steps smaller than ``min_fraction`` of the largest are treated as partial
    and skipped.
    """
    steps = cell.steps()
    col = f"{step_type}_ah"
    s = steps[steps["step_type"] == step_type]
    s = s[s[col] >= min_fraction * s[col].max()]
    if s.empty:
        return f"No full {step_type} steps in this cell."
    retention = s[col] / s[col].iloc[0]

    fig = go.Figure(go.Scatter(x=s["cycle_index"], y=retention, mode="lines+markers", line_width=2))
    fig.update_layout(xaxis_title="cycle", yaxis_title=f"{step_type} capacity / first", height=380)
    summary = {"steps": len(s), "final_retention": round(float(retention.iloc[-1]), 4)}
    return [fig, summary]
