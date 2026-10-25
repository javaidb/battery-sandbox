"""Space–time maps: one through-cell quantity over the whole test, as a colour map.

The animated views show one moment at a time. These show every moment at
once: position through the cell on the vertical axis, time along the
horizontal, colour for the value. Reaction fronts, salt depletion and
relaxation all become shapes you can see.
"""

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from batterylab.echem import FIELDS
from common import MUTED, diverging_scale, sequential_scale, style, x_axis
from glossary import card

MAX_COLUMNS = 400  # time columns per map; plenty for the eye, light for the browser


def _stack(run, label):
    """(position µm, values[position, time]) for a field, with a NaN gap across the separator for electrode-only fields."""
    parts = run.fields[label]
    if "full" in parts:
        return run.x_um["full"], parts["full"]
    xn, xp = run.x_um["n"], run.x_um["p"]
    gap = np.full((1, parts["n"].shape[1]), np.nan)
    return np.concatenate([xn, [(xn[-1] + xp[0]) / 2], xp]), np.vstack([parts["n"], gap, parts["p"]])


def render(runs, colors, xmode):
    labels = [lab for lab in FIELDS if all(lab in r.fields for r in runs)]
    lab = st.selectbox("Quantity", labels, key="st_map_field")
    stacks = [_stack(r, lab) for r in runs]
    lo = min(np.nanmin(z) for _, z in stacks)
    hi = max(np.nanmax(z) for _, z in stacks)
    signed = lo < 0 < hi
    if signed:  # symmetric around zero so the neutral colour means "nothing happening"
        m = max(abs(lo), abs(hi))
        lo, hi, scale = -m, m, diverging_scale()
    else:
        scale = sequential_scale()

    fig = make_subplots(rows=len(runs), cols=1, shared_xaxes=xmode == "Test progress (%)", vertical_spacing=0.08,
                        subplot_titles=[r.label for r in runs])
    for i, (run, (pos, z)) in enumerate(zip(runs, stacks), start=1):
        x, xlabel = x_axis(run, xmode)
        step = max(1, len(x) // MAX_COLUMNS)
        fig.add_trace(go.Heatmap(
            x=x[::step], y=pos, z=z[:, ::step], zmin=lo, zmax=hi, colorscale=scale, showscale=i == 1,
            colorbar=dict(title=lab.split(" [")[0], thickness=12, len=min(1.0, 0.9 / len(runs) + 0.3)),
            hovertemplate=f"{xlabel} %{{x:.3g}}<br>x %{{y:.0f}} µm<br>%{{z:.4g}}<extra>{run.meta['name']}</extra>",
        ), row=i, col=1)
        xn_end, xp_start = run.x_um["n"][-1], run.x_um["p"][0]
        for y in (xn_end, xp_start):  # separator edges
            fig.add_hline(y=y, line=dict(color=MUTED, width=1, dash="dot"), row=i, col=1)
        fig.update_yaxes(title_text="x (µm)", row=i, col=1)
        ax = "" if i == 1 else str(i)
        for y, text in ((xn_end / 2, "anode"), ((xp_start + pos[-1]) / 2, "cathode")):
            fig.add_annotation(x=0.005, xref=f"x{ax} domain", y=y, yref=f"y{ax}", text=text, showarrow=False, xanchor="left",
                               font=dict(size=10, color="#f0efec"), bgcolor="rgba(0,0,0,0.35)")
    fig.update_xaxes(title_text=xlabel, row=len(runs), col=1)
    st.plotly_chart(style(fig, 300 * len(runs) + 80), width="stretch")
    st.caption(
        "Read across for time, up for position (anode at the bottom, the dotted lines are the separator's edges). "
        + ("Colour is signed: grey means zero, blue and red are opposite directions. " if signed else "")
        + "Look for diagonal bands (a reaction front moving through the electrode), colour pooling at one edge "
          "(something being depleted or concentrated there) and fading after the current stops (relaxation). "
          "Every test shares one colour scale, so their maps compare directly."
    )
    card(lab)
