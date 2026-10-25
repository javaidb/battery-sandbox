"""Electrochemistry view: what happens inside a cell during a test.

Configure up to three tests in the sidebar (chemistry, protocol, conditions,
ageing); each is simulated with PyBaMM and cached. Every tab compares them:
single-quantity plots overlay one colour per test, and charts that already
use colour for several mechanisms get a toggle to pick the test.
"""

import dataclasses
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from batterylab.echem import CHEMISTRIES, FIELDS, SERIES_GROUPS, TESTS
from common import CRITICAL, MUTED, band_fill, categorical, metric_colors, ocp_curves, run_to_celldata, simulate, style
from glossary import LOSS_KEYS, card, cards
from views import measured, panels

MAX_TESTS = 3  # every pair of colours stays distinguishable up to three
N_FRAMES = 60

TEST_HELP = {
    "CC discharge": "Constant-current discharge from full to the lower cut-off, then 1 h rest. The rest shows how the cell relaxes.",
    "CC-CV charge": "Constant current to the upper cut-off, constant voltage until C/50, then rest. Raise the C-rate or lower the temperature and watch the anode potential.",
    "HPPC": "Hybrid pulse power characterisation: at every 10 % SoC, a 10 s discharge pulse and a 10 s charge pulse. The instant voltage jumps and the slow relaxation are what an ECM has to capture.",
    "GITT": "Galvanostatic intermittent titration: short C/5 pulses, then long rests. Each rest relaxes to (nearly) the OCV, and the shape of the relaxation reflects solid-state diffusion.",
    "Drive cycle": "A synthetic EV-like profile with accelerations, regenerative braking and stops (charge-positive current).",
    "Relaxation after pulse": "From 50 % SoC: a short discharge pulse, then a 2 h rest. Watch the concentration gradients in the electrolyte and particles decay.",
}


# --- sidebar ----------------------------------------------------------------


DEFAULT_TEST = {
    "chemistry": next(iter(CHEMISTRIES)), "test": "CC discharge", "c_rate": 1.0, "drive": "urban", "drive_minutes": 20,
    "temperature_c": 25.0, "model": "DFN", "thermal": True, "degradation": {"LLI": 0.0, "LAM_PE": 0.0, "LAM_NE": 0.0},
}
C_RATES = [0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0]
MODELS = ["DFN", "SPMe", "SPM"]
# Colour identifies a test in most views; line style identifies it in the V/I/T panel.
GLYPH = ["━━", "╍╍", "┅┅"]


def _edit_test(cfg: dict) -> None:
    """Sidebar widgets for one test, written back into ``cfg``.

    Widget keys carry the test's id, so each test keeps its own settings when
    you switch which one you are editing.
    """
    sb, uid = st.sidebar, cfg["_uid"]
    cfg["chemistry"] = sb.selectbox("Chemistry", list(CHEMISTRIES), index=list(CHEMISTRIES).index(cfg["chemistry"]),
                                    key=f"{uid}_chem", help="PyBaMM parameter sets from the literature")
    sb.caption(CHEMISTRIES[cfg["chemistry"]][1])
    prev_test = cfg["test"]
    cfg["test"] = sb.selectbox("Test", TESTS, index=TESTS.index(cfg["test"]), key=f"{uid}_test")
    if cfg["test"] in ("CC discharge", "CC-CV charge", "Relaxation after pulse"):
        cfg["c_rate"] = sb.select_slider("C-rate", C_RATES, value=cfg["c_rate"], key=f"{uid}_crate")
    if cfg["test"] == "Drive cycle":
        cfg["drive"] = sb.selectbox("Driving style", ["urban", "highway", "aggressive"],
                                    index=["urban", "highway", "aggressive"].index(cfg["drive"]), key=f"{uid}_drive")
        cfg["drive_minutes"] = sb.slider("Duration (min)", 5, 60, cfg["drive_minutes"], 5, key=f"{uid}_dmin")
    cfg["temperature_c"] = float(sb.slider("Ambient temperature (°C)", -10, 45, int(cfg["temperature_c"]), 5, key=f"{uid}_temp"))
    model_key = f"{uid}_model"
    if cfg["test"] != prev_test:  # DFN drive cycles take 10-20 s, so switching to one picks SPMe
        cfg["model"] = st.session_state[model_key] = "SPMe" if cfg["test"] == "Drive cycle" else "DFN"
    with sb.expander("Model and ageing"):
        cfg["model"] = st.selectbox(
            "Model", MODELS, key=model_key,
            **({} if model_key in st.session_state else {"index": MODELS.index(cfg["model"])}),
            help="DFN resolves every particle across the electrode; SPMe/SPM use one representative particle per electrode and are much faster.",
        )
        cfg["thermal"] = st.checkbox("Lumped thermal model", cfg["thermal"], key=f"{uid}_thermal")
        st.caption("Prescribed degradation, as fractions of the fresh cell")
        deg = cfg["degradation"]
        deg["LLI"] = st.slider("LLI: lithium inventory lost", 0.0, 0.3, deg["LLI"], 0.01, key=f"{uid}_lli")
        deg["LAM_PE"] = st.slider("LAM_PE: cathode material lost", 0.0, 0.3, deg["LAM_PE"], 0.01, key=f"{uid}_lampe")
        deg["LAM_NE"] = st.slider("LAM_NE: anode material lost", 0.0, 0.3, deg["LAM_NE"], 0.01, key=f"{uid}_lamne")


def _sim_config(cfg: dict) -> dict:
    """The part of a test's settings the simulation uses (drops UI-only and irrelevant keys)."""
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    if out["test"] != "Drive cycle":
        out.pop("drive"), out.pop("drive_minutes")
    if out["test"] not in ("CC discharge", "CC-CV charge", "Relaxation after pulse"):
        out.pop("c_rate")
    return out


def _tests_sidebar() -> tuple[list[dict], int]:
    """The list of tests to compare, and which one the widgets below are editing."""
    state = st.session_state
    if "_tests" not in state:
        state["_tests"] = [{**json.loads(json.dumps(DEFAULT_TEST)), "_uid": 0}]
        state["_next_uid"], state["_editing"] = 1, 0
    tests = state["_tests"]
    sb = st.sidebar
    sb.subheader("Tests")
    colors = categorical(MAX_TESTS)
    names = [f"Test {k + 1}" for k in range(len(tests))]
    editing = min(state["_editing"], len(tests) - 1)
    if len(tests) > 1:
        editing = sb.radio("Editing", range(len(tests)), index=editing, format_func=lambda k: names[k], horizontal=True)
    state["_editing"] = editing
    c1, c2 = sb.columns(2)
    if c1.button("➕ Add test", disabled=len(tests) >= MAX_TESTS, use_container_width=True,
                 help=f"Adds a copy of {names[editing]}; change one setting to compare. Up to {MAX_TESTS} tests."):
        copy = json.loads(json.dumps(tests[editing]))
        copy["_uid"] = state["_next_uid"]
        state["_next_uid"] += 1
        tests.append(copy)
        state["_editing"] = len(tests) - 1
        st.rerun()
    if c2.button("✕ Remove", disabled=len(tests) == 1, use_container_width=True, help=f"Removes {names[editing]}"):
        tests.pop(editing)
        state["_editing"] = max(0, editing - 1)
        st.rerun()
    sb.markdown(
        f"<span style='color:{colors[editing]}'>●</span> `{GLYPH[editing]}` **Editing {names[editing]}**",
        unsafe_allow_html=True,
    )
    _edit_test(tests[editing])
    return tests, editing


# --- helpers ----------------------------------------------------------------


def _x_axis(run, mode: str) -> tuple[np.ndarray, str]:
    t = run.series.index.to_numpy()
    if mode == "Time (h)":
        return t / 3600, "time (h)"
    if mode == "Capacity (Ah)":
        q = run.series["Discharge capacity [Ah]"].to_numpy()
        return np.abs(q - q[0]), "|capacity moved| (Ah)"
    return 100 * (t - t[0]) / max(t[-1] - t[0], 1e-9), "test progress (%)"


def _frame_idx(run, n=N_FRAMES) -> np.ndarray:
    return np.unique(np.linspace(0, len(run.t) - 1, n).round().astype(int))


KPI_TIPS = {
    "Capacity moved": "Charge passed during the test",
    "Energy": "Delivered on discharge, absorbed on charge",
    "Voltage range": "Lowest and highest terminal voltage",
    "Peak temp. rise": "Above ambient",
    "Min anode vs Li": "At the anode/separator interface; below 0 V lithium can plate",
}


def _kpi_values(run) -> dict[str, tuple[str, bool]]:
    """Headline numbers for one run: label -> (formatted value, is a warning)."""
    s = run.series
    v, i = s["Voltage [V]"], s["Current [A]"]
    dt = np.diff(s.index.to_numpy(), prepend=s.index[0])
    q = s["Discharge capacity [Ah]"]
    anode = s["Anode potential at separator vs Li [V]"].min()
    return {
        "Capacity moved": (f"{abs(q.iloc[-1] - q.iloc[0]):.3f} Ah", False),
        "Energy": (f"{abs(float(np.sum(v * i * dt) / 3600)):.2f} Wh", False),
        "Voltage range": (f"{v.min():.2f}–{v.max():.2f} V", False),
        "Peak temp. rise": (f"{s['Cell temperature [°C]'].max() - run.config['temperature_c']:.1f} K", False),
        "Min anode vs Li": (f"{anode * 1000:.0f} mV", anode < 0),
    }


def _kpis(runs, colors):
    """Headline numbers: a two-column grid for one test, a metric × test table when comparing."""
    vals = [_kpi_values(r) for r in runs]
    warn = lambda w: f" style='color:{CRITICAL}'" if w else ""  # noqa: E731
    css = (".kpis{display:grid;grid-template-columns:1fr 1fr;gap:.5rem .75rem;margin:.25rem 0 .5rem}"
           ".kpi-l{font-size:.75rem;opacity:.65}.kpi-v{font-size:1.05rem;font-weight:600;white-space:nowrap}"
           ".kpit{width:100%;border-collapse:collapse;font-size:.8rem;margin:.25rem 0 .5rem}"
           ".kpit th,.kpit td{padding:.2rem .3rem;text-align:right;white-space:nowrap}"
           ".kpit th:first-child,.kpit td:first-child{text-align:left;opacity:.7;font-weight:400}"
           ".kpit tbody tr{border-top:1px solid rgba(128,128,128,.2)}")
    if len(runs) == 1:
        body = "".join(f"<div title='{KPI_TIPS[k]}'><div class='kpi-l'>{k}</div><div class='kpi-v'{warn(w)}>{v}</div></div>"
                       for k, (v, w) in vals[0].items())
        st.html(f"<style>{css}</style><div class='kpis'>{body}</div>")
        return
    head = "".join(f"<th><span style='color:{c}'>●</span> T{k + 1}</th>" for k, c in enumerate(colors))
    rows = "".join(
        f"<tr title='{KPI_TIPS[key]}'><td>{key}</td>" + "".join(f"<td{warn(v[key][1])}>{v[key][0]}</td>" for v in vals) + "</tr>"
        for key in KPI_TIPS
    )
    st.html(f"<style>{css}</style><table class='kpit'><thead><tr><th></th>{head}</tr></thead><tbody>{rows}</tbody></table>")


def _animation_controls(fig, n_frames: int, bottom_px: int = 90):
    """Play/pause buttons and a progress slider under the figure."""
    pct = [f"{100 * f / (n_frames - 1):.0f} %" for f in range(n_frames)]
    fig.update_layout(
        margin=dict(b=bottom_px),
        updatemenus=[dict(type="buttons", direction="left", x=0, xanchor="left", y=0, yanchor="top", pad=dict(t=50, r=10),
                          showactive=False, buttons=[
            dict(label="▶ Play", method="animate",
                 args=[None, {"frame": {"duration": 120, "redraw": False}, "fromcurrent": True, "transition": {"duration": 0}}]),
            dict(label="⏸ Pause", method="animate", args=[[None], {"frame": {"duration": 0}, "mode": "immediate"}]),
        ])],
        sliders=[dict(active=0, x=0.16, len=0.84, xanchor="left", y=0, yanchor="top", pad=dict(t=40),
                      currentvalue=dict(prefix="test progress: ", font=dict(size=13)),
                      font=dict(color="rgba(0,0,0,0)"), ticklen=0,  # hide the 60 step labels; the current value shows above
                      steps=[dict(method="animate", label=pct[f],
                                  args=[[str(f)], {"frame": {"duration": 0, "redraw": False}, "mode": "immediate"}])
                             for f in range(n_frames)])],
    )
    return fig


# --- sections ---------------------------------------------------------------


RESPONSE = [("Voltage [V]", "voltage", "Voltage (V)"), ("Current [A]", "current", "Current (A, + = charge)"),
            ("Cell temperature [°C]", "temperature", "Temperature (°C)")]
RUN_DASH = ["solid", "dash", "dot"]


def _mini_response(runs, xmode, cursor: bool):
    """Voltage, current and temperature in the side panel.

    Each metric has its own colour; runs are told apart by line style. With
    ``cursor``, a vertical line per run marks the frame an animated view is
    showing; ``_CURSOR_SYNC`` moves it in the browser as the animation plays.
    """
    colors = metric_colors()
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.09,
                        subplot_titles=[title for *_, title in RESPONSE])
    cursor_x = []
    for run, dash in zip(runs, RUN_DASH):
        x, xlabel = _x_axis(run, xmode)
        for r, (col, kind, _) in enumerate(RESPONSE, start=1):
            fig.add_trace(go.Scatter(x=x, y=run.series[col], mode="lines", line=dict(width=2, color=colors[kind], dash=dash),
                                     name=run.meta["name"], legendgroup=run.meta["name"], showlegend=False), row=r, col=1)
        cursor_x.append([float(x[k]) for k in _frame_idx(run)])
    if len(runs) > 1:  # legend entries for line style only, in neutral ink
        for run, dash in zip(runs, RUN_DASH):
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", line=dict(width=2, color=MUTED, dash=dash), name=run.meta["name"]))
    if cursor:
        for xs, dash in zip(cursor_x, RUN_DASH):
            fig.add_shape(type="line", xref="x", yref="paper", x0=xs[0], x1=xs[0], y0=0, y1=1,
                          line=dict(color=MUTED, width=1.5, dash=dash))
        fig.update_layout(meta={"cursor": cursor_x})
    fig.update_xaxes(title_text=xlabel, row=3, col=1)
    fig.update_layout(hovermode="x unified", legend=dict(font_size=10))
    fig.update_annotations(font_size=12)
    with st.container(border=True):
        st.caption("Cell response" + (" · grey line = animation frame" if cursor else ""))
        # Legend under the plots: above them it would sit on the "Voltage (V)" title.
        fig = style(fig, 460).update_layout(legend=dict(orientation="h", x=0, y=-0.16, yanchor="top", font_size=10),
                                            margin=dict(b=40))
        st.plotly_chart(fig, use_container_width=True, key="mini_response")
        with st.expander("What these show"):
            for col, kind, _ in RESPONSE:
                card(col, colors[kind])


# Moves the mini panel's cursor to follow whichever animated figure is playing
# or being scrubbed. Runs in the browser: Plotly figures cannot drive each other.
_CURSOR_SYNC = """
<script>
(() => {
  if (window.__echemCursor) return;
  window.__echemCursor = true;
  const plots = () => [...document.querySelectorAll('.js-plotly-plot')];
  const move = (name) => {
    const mini = plots().find(p => p.layout && p.layout.meta && p.layout.meta.cursor);
    const f = parseInt(name, 10);
    if (!mini || Number.isNaN(f) || !window.Plotly) return;
    const upd = {};
    mini.layout.meta.cursor.forEach((xs, i) => {
      const x = xs[Math.min(f, xs.length - 1)];
      upd[`shapes[${i}].x0`] = x;
      upd[`shapes[${i}].x1`] = x;
    });
    window.Plotly.relayout(mini, upd);
  };
  setInterval(() => {
    plots().filter(p => p.layout && p.layout.meta && p.layout.meta.anim && !p.__cursorBound && p.on).forEach(gd => {
      gd.__cursorBound = true;
      gd.on('plotly_animatingframe', ev => move(ev.name));
      gd.on('plotly_sliderchange', ev => move(ev.step.args[0][0]));
    });
  }, 400);
})();
</script>
"""


MIN_DRAWN_RADIUS_UM = 1.5  # smaller particles are drawn enlarged, or there would be thousands of dots


def _particle_centres(x0, x1, height, r, fraction):
    """Hexagonal grid of particle centres filling [x0, x1] × [0, height] at roughly ``fraction`` area coverage."""
    spacing = r * np.sqrt(2 * np.pi / (np.sqrt(3) * min(max(fraction, 0.2), 0.85)))
    centres = []
    for j, y in enumerate(np.arange(r, height - r + 1e-9, spacing * np.sqrt(3) / 2)):
        offset = spacing / 2 if j % 2 else 0.0
        centres += [(x, y) for x in np.arange(x0 + r + offset, x1 - r + 1e-9, spacing)]
    return centres


def _cell_cross_section(fig, row, col, geom, chemistry, height=70.0):
    """A to-scale cross-section of one electrode pair, sharing the µm x-axis of the plots below it."""
    anode_name, cathode_name = (p.strip() for p in reversed(chemistry.split("|")[:2])) if "|" in chemistry else ("anode", "cathode")
    la, ls, lc = geom["anode"], geom["separator"], geom["cathode"]
    x_sep, x_cat, x_end = la, la + ls, la + ls + lc
    ref = dict(row=row, col=col)
    ax = _axis_suffix(row, col)
    rect = lambda x0, x1, **kw: fig.add_shape(type="rect", x0=x0, x1=x1, y0=0, y1=height, layer="below", **kw, **ref)  # noqa: E731
    rect(-geom["anode_cc"], 0, fillcolor="#b87333", line_width=0)  # copper current collector
    rect(0, x_end, fillcolor=band_fill(), line_width=0)  # electrolyte fills every pore
    rect(x_sep, x_cat, fillcolor=MUTED, opacity=0.45, line_width=0)  # porous separator
    rect(x_end, x_end + geom["cathode_cc"], fillcolor="#a8adb5", line_width=0)  # aluminium current collector
    for x0, x1, r_true, frac, fill in [
        (0, x_sep, geom["anode_particle_radius"], geom["anode_active_fraction"], "#4b4f58"),
        (x_cat, x_end, geom["cathode_particle_radius"], geom["cathode_active_fraction"], "#8d6fb0"),
    ]:
        r = max(r_true, MIN_DRAWN_RADIUS_UM)
        for cx, cy in _particle_centres(x0, x1, height, r, frac):
            fig.add_shape(type="circle", x0=cx - r, x1=cx + r, y0=cy - r, y1=cy + r, fillcolor=fill,
                          line=dict(width=0.6, color="rgba(255,255,255,0.35)"), **ref)
    for x, text in [(-geom["anode_cc"] / 2, "Cu"), (x_sep / 2, f"Anode<br>{anode_name}"), ((x_sep + x_cat) / 2, "Sep."),
                    ((x_cat + x_end) / 2, f"Cathode<br>{cathode_name}"), (x_end + geom["cathode_cc"] / 2, "Al")]:
        fig.add_annotation(x=x, y=1.0, xref=f"x{ax}", yref=f"y{ax} domain", text=text, showarrow=False,
                           yanchor="bottom", font=dict(size=11))
    # Equal µm per pixel in x and y, so particles are round and to scale.
    fig.update_yaxes(range=[0, height], visible=False, scaleanchor=f"x{ax}", scaleratio=1, constrain="domain", row=row, col=col)
    fig.update_xaxes(range=[-geom["anode_cc"], x_end + geom["cathode_cc"]], showgrid=False, row=row, col=col)


def _axis_suffix(row, col, n_cols=2):
    """Plotly names subplot axes x, x2, x3, ... in row-major order."""
    n = (row - 1) * n_cols + col
    return "" if n == 1 else str(n)


def _field_ranges(runs, label):
    lo, hi = np.inf, -np.inf
    for run in runs:
        for arr in run.fields.get(label, {}).values():
            lo, hi = min(lo, np.nanmin(arr)), max(hi, np.nanmax(arr))
    pad = 0.05 * (hi - lo or 1)
    return [lo - pad, hi + pad]


def _inside(runs, colors):
    """Animated through-thickness profiles, scrubbed by % of test progress."""
    labels = [lab for lab in FIELDS if all(lab in r.fields for r in runs)]
    chosen = st.multiselect("Profiles", labels, default=labels[:4], max_selections=6)
    if not chosen:
        return
    # Row 1 in each column is a to-scale drawing of the cell on the same µm axis as
    # the profiles below it. It is built from shapes only, so it never animates.
    geom = runs[0].meta.get("geometry_um")
    top = 1 if geom else 0
    n_rows = top + (len(chosen) + 1) // 2
    titles = (["", ""] if geom else []) + chosen  # the cross-section labels its own layers
    fig = make_subplots(rows=n_rows, cols=2, subplot_titles=titles, vertical_spacing=0.3 / n_rows, horizontal_spacing=0.08,
                        row_heights=([0.5] if geom else []) + [1.0] * (n_rows - top))
    if geom:
        x_range = [-geom["anode_cc"], geom["anode"] + geom["separator"] + geom["cathode"] + geom["cathode_cc"]]
        for c in (1, 2):
            _cell_cross_section(fig, 1, c, geom, runs[0].config["chemistry"])
    idxs = [_frame_idx(r) for r in runs]

    def frame_traces(f):
        """Traces for frame f (0..N_FRAMES-1), in a fixed order."""
        out = []
        for run, color, idx in zip(runs, colors, idxs):
            k = idx[min(f, len(idx) - 1)]
            for lab in chosen:
                for part, arr in run.fields[lab].items():
                    out.append(go.Scatter(x=run.x_um[part], y=arr[:, k], mode="lines", line=dict(width=2, color=color),
                                          name=run.meta["name"], legendgroup=run.meta["name"], showlegend=False,
                                          hovertemplate=f"{lab}<br>x %{{x:.0f}} µm<br>%{{y:.4g}}<extra>{run.meta['name']}</extra>"))
        return out

    positions = [(top + 1 + j // 2, 1 + j % 2) for run in runs for j, lab in enumerate(chosen) for _ in run.fields[lab]]
    for tr, (r, c) in zip(frame_traces(0), positions):
        fig.add_trace(tr, row=r, col=c)
    fig.frames = [go.Frame(data=frame_traces(f), name=str(f)) for f in range(N_FRAMES)]
    for j, lab in enumerate(chosen):
        r, c = top + 1 + j // 2, 1 + j % 2
        fig.update_yaxes(range=_field_ranges(runs, lab), row=r, col=c)
        fig.update_xaxes(title_text="x through the cell (µm)", row=r, col=c, **({"range": x_range} if geom else {}))
        sep = runs[0].x_um
        fig.add_vrect(x0=sep["n"][-1], x1=sep["p"][0], fillcolor=MUTED, opacity=0.12, line_width=0, row=r, col=c)
    fig.update_layout(meta={"anim": True})

    st.plotly_chart(_animation_controls(style(fig, 340 * (n_rows - top) + 200 * top), N_FRAMES), use_container_width=True)
    st.caption("Drag the slider or press Play; the grey line in the Cell response panel follows along. "
               "The grey band in each plot is the **separator**: a porous polymer film that keeps the electrodes apart "
               "while electrolyte in its pores carries Li⁺. (SEI is a nanometre-thin film on the anode particles, far too thin to see here.) "
               "Overlaid runs are matched by % of test progress.")
    if geom:
        _cross_section_notes(geom)
    cards(chosen)


def _cross_section_notes(geom):
    small = [f"{name} particles (radius {geom[f'{key}_particle_radius'] * 1000:.0f} nm)"
             for key, name in (("anode", "anode"), ("cathode", "cathode")) if geom[f"{key}_particle_radius"] < MIN_DRAWN_RADIUS_UM]
    with st.expander("Reading the cross-section"):
        st.markdown(
            f"""
- **Cu / Al**: copper and aluminium current collectors ({geom['anode_cc']:g} µm and {geom['cathode_cc']:g} µm). They carry electrons to the tabs; no lithium enters them.
- **Anode** ({geom['anode']:g} µm): active particles (dark) packed at {geom['anode_active_fraction']:.0%} by volume. The rest is pores full of electrolyte (blue tint), plus binder and carbon.
- **Separator** ({geom['separator']:g} µm): electronically insulating, ionically conducting.
- **Cathode** ({geom['cathode']:g} µm): active particles (purple) at {geom['cathode_active_fraction']:.0%} by volume.
- **Li⁺ path**: on discharge, lithium leaves the anode particles, crosses the electrolyte through the separator as Li⁺ and enters the cathode particles, while electrons go round the external circuit. Charge reverses it.

Particle radii and layer thicknesses are the model's own parameters, drawn to the same µm scale as the plots. The model treats the particles as spheres; the drawing is a 2-D slice through them.
"""
            + (f"\n{' and '.join(small).capitalize()} are too small to draw individually, so they are shown enlarged to {MIN_DRAWN_RADIUS_UM} µm." if small else "")
        )


def _particles(runs, colors):
    keys = sorted({k for r in runs for k in r.particles})
    pick = st.selectbox("Which particle", keys, format_func=lambda k: ("Anode · " if k.startswith("n") else "Cathode · ") + k.split(":")[1])
    # Keep each test's own colour even when some tests lack this particle (SPM/SPMe have no x-resolved ones).
    pairs = [(r, c) for r, c in zip(runs, colors) if pick in r.particles]
    runs_k = [r for r, _ in pairs]
    missing = [r.meta["name"] for r in runs if pick not in r.particles]
    if missing:
        st.caption(f"Not available for {', '.join(missing)}: only the DFN model resolves particles at different depths.")
    part = pick[0]
    fig = go.Figure()
    idxs = [_frame_idx(r) for r in runs_k]

    def traces(f):
        return [go.Scatter(x=r.r_um[part] / r.r_um[part][-1], y=r.particles[pick][:, idx[min(f, len(idx) - 1)]], mode="lines",
                           line=dict(width=2, color=c), name=r.meta["name"]) for (r, c), idx in zip(pairs, idxs)]

    for tr in traces(0):
        fig.add_trace(tr)
    fig.frames = [go.Frame(data=traces(f), name=str(f)) for f in range(N_FRAMES)]
    lo = min(np.nanmin(r.particles[pick]) for r in runs_k)
    hi = max(np.nanmax(r.particles[pick]) for r in runs_k)
    fig.update_yaxes(range=[lo - 0.02, hi + 0.02], title_text="stoichiometry (lithium fraction)")
    fig.update_xaxes(title_text="radius r / R  (0 = centre, 1 = surface)")
    fig.update_layout(meta={"anim": True})
    st.plotly_chart(_animation_controls(style(fig, 520), N_FRAMES), use_container_width=True)
    st.caption("A flat profile means the particle is in equilibrium; a steep one means solid diffusion is limiting. "
               "The surface value is what sets the electrode's potential.")
    card("particles")


def _test_toggle(runs, key: str):
    """For charts with several series per test: pick which test to show (no control when there is only one)."""
    if len(runs) == 1:
        return runs[0]
    k = st.segmented_control("Show test", range(len(runs)), default=0, key=key, format_func=lambda k: runs[k].meta["name"],
                             help="This chart has one series per mechanism, so it shows one test at a time.")
    return runs[k if k is not None else 0]


def _losses(runs, colors, xmode):
    run = _test_toggle(runs, "losses_test")
    labels = [lab for lab in SERIES_GROUPS["Voltage breakdown"] if lab != "Open-circuit voltage [V]" and lab in run.series]
    x, xlabel = _x_axis(run, xmode)
    fig = go.Figure()
    for lab, color in zip(labels, categorical(6)):
        fig.add_trace(go.Scatter(x=x, y=run.series[lab].abs() * 1000, mode="lines", stackgroup="loss", line=dict(width=0.5, color=color),
                                 name=lab.replace(" [V]", "")))
    total = (run.series["Open-circuit voltage [V]"] - run.series["Voltage [V]"]).abs() * 1000
    fig.add_trace(go.Scatter(x=x, y=total, mode="lines", line=dict(width=2, color=MUTED, dash="dot"), name="|OCV − V| (check)"))
    fig.update_layout(xaxis_title=xlabel, yaxis_title="mV")
    st.plotly_chart(style(fig, 460), use_container_width=True)
    st.caption(f"{run.label}. Each band is the size of one loss mechanism; together they explain the gap between "
               "open-circuit voltage and terminal voltage. The dotted line is that gap, computed directly, as a check.")
    if len(runs) > 1:
        lab = st.selectbox("Compare one mechanism across all tests", labels)
        fig = go.Figure()
        for r, c in zip(runs, colors):
            xr, _ = _x_axis(r, xmode)
            if lab in r.series:
                fig.add_trace(go.Scatter(x=xr, y=r.series[lab].abs() * 1000, mode="lines", line=dict(width=2, color=c), name=r.meta["name"]))
        st.plotly_chart(style(fig, 360).update_layout(xaxis_title=xlabel, yaxis_title=f"|{lab}| (mV)"), use_container_width=True)
    cards(["Open-circuit voltage [V]"] + [LOSS_KEYS.get(lab, lab) for lab in labels], cols=3,
          colors=[None] + categorical(6)[: len(labels)])


def _windows(runs, colors):
    st.caption("Each electrode's open-circuit potential across its full lithium range (grey), with the part this test used "
               "(coloured, from start ○ to end ●). Ageing (LLI/LAM) slides and shrinks the used windows.")
    cols = st.columns(2)
    for col, (part, name, var) in zip(cols, [("n", "Anode", "Anode surface stoichiometry (x-avg)"), ("p", "Cathode", "Cathode surface stoichiometry (x-avg)")]):
        fig = go.Figure()
        key = f"{'negative' if part == 'n' else 'positive'}_ocp_v"
        for chem in dict.fromkeys(r.config["chemistry"] for r in runs):
            ocp = ocp_curves(chem)
            ok = ocp[key].between(-0.2, 5.0)
            fig.add_trace(go.Scatter(x=ocp["stoichiometry"][ok], y=ocp[key][ok], mode="lines", line=dict(width=2, color=MUTED),
                                     name=f"{name} OCP · {chem}"))
        # Later tests first, Test 1 last so it is drawn on top.
        for r, c in reversed(list(zip(runs, colors))):
            ocp = ocp_curves(r.config["chemistry"])
            sto = r.series[var].to_numpy()
            pot = np.interp(sto, ocp["stoichiometry"], ocp[key])
            fig.add_trace(go.Scatter(x=sto, y=pot, mode="lines", line=dict(width=5, color=c), name=r.meta["name"], opacity=0.85))
            fig.add_trace(go.Scatter(x=[sto[0], sto[-1]], y=[pot[0], pot[-1]], mode="markers", showlegend=False,
                                     marker=dict(size=10, color=["rgba(0,0,0,0)", c], line=dict(width=2, color=c))))
        fig.update_layout(xaxis_title="stoichiometry (lithium fraction)", yaxis_title="potential vs Li/Li⁺ (V)")
        col.markdown(f"**{name}**")  # a heading outside the plot, so it never collides with the legend
        col.plotly_chart(style(fig, 420), use_container_width=True)
    cards(["windows", "Anode OCP [V]", "Cathode OCP [V]"], cols=3)


def _heat_safety(runs, colors, xmode):
    c1, c2 = st.columns(2)
    with c1:
        run = _test_toggle(runs, "heat_test")
    x, xlabel = _x_axis(run, xmode)
    heat = [lab for lab in SERIES_GROUPS["Heat"] if lab in run.series and lab != "Total heating [W]"]
    if heat:
        fig = go.Figure()
        for lab, color in zip(heat, categorical(3)):
            fig.add_trace(go.Scatter(x=x, y=run.series[lab], mode="lines", line=dict(width=2, color=color), name=lab.replace(" [W]", "")))
        fig.add_trace(go.Scatter(x=x, y=run.series["Total heating [W]"], mode="lines", line=dict(width=2, color=MUTED, dash="dot"), name="total"))
        c1.markdown(f"**Heat sources · {run.meta['name']}**")
        c1.plotly_chart(style(fig, 420).update_layout(xaxis_title=xlabel, yaxis_title="W"), use_container_width=True)
    else:
        c1.info(f"{run.meta['name']} has the thermal model off; turn it on under Model and ageing to see heat sources.")
    fig = go.Figure()
    for r, c in zip(runs, colors):
        xr, _ = _x_axis(r, xmode)
        fig.add_trace(go.Scatter(x=xr, y=r.series["Anode potential at separator vs Li [V]"] * 1000, mode="lines", line=dict(width=2, color=c), name=r.meta["name"]))
    fig.add_hline(y=0, line=dict(color=CRITICAL, width=1.5, dash="dash"), annotation_text="0 V: plating threshold", annotation_position="bottom right")
    if len(runs) > 1:
        c2.markdown("<div style='height:2.6rem'></div>", unsafe_allow_html=True)  # level with the test toggle on the left
    c2.markdown("**Anode potential at the separator · all tests**")
    c2.plotly_chart(style(fig, 420).update_layout(xaxis_title=xlabel, yaxis_title="mV vs Li/Li⁺"), use_container_width=True)
    st.caption("Reversible (entropic) heat can be negative: the cell cools while that reaction runs. "
               "The anode is closest to plating at the separator, where the electrolyte current is highest.")
    cards(heat + (["Total heating [W]"] if heat else []) + ["Anode potential at separator vs Li [V]"], cols=3,
          colors=categorical(3)[: len(heat)] + ([MUTED] if heat else []))


def _all_metrics(runs, colors, xmode):
    c1, c2 = st.columns(2)
    group = c1.selectbox("Group", list(SERIES_GROUPS) + ["Safety"])
    options = ["Anode potential at separator vs Li [V]"] if group == "Safety" else [k for k in SERIES_GROUPS[group] if k in runs[0].series]
    lab = c2.selectbox("Metric", options)
    fig = go.Figure()
    for r, c in zip(runs, colors):
        if lab in r.series:
            xr, xlabel = _x_axis(r, xmode)
            fig.add_trace(go.Scatter(x=xr, y=r.series[lab], mode="lines", line=dict(width=2, color=c), name=r.meta["name"]))
    st.plotly_chart(style(fig, 440).update_layout(xaxis_title=xlabel, yaxis_title=lab), use_container_width=True)
    card(lab)
    with st.expander("Data table (current run)"):
        st.dataframe(runs[0].series, use_container_width=True, height=300)
        st.download_button("Download CSV", runs[0].series.to_csv().encode(), file_name="run.csv")


# --- page -------------------------------------------------------------------


def page():
    mode = st.sidebar.radio("Data", ["Simulate (PyBaMM)", "Measured datasets"], horizontal=True,
                            index=["Simulate (PyBaMM)", "Measured datasets"].index(st.session_state.get("_echem_mode", "Simulate (PyBaMM)")))
    st.session_state["_echem_mode"] = mode
    if mode == "Measured datasets":
        st.title("Electrochemistry")
        picked = measured.pick_measured()
        if picked:
            opt, cell = picked
            tab_data, tab_yours = st.tabs(["Data", "Your analysis (workbench)"])
            with tab_data:
                measured.render(opt, cell)
            with tab_yours:
                panels.render(cell, prefixes=("A", "C", "example", "misc"))
        return

    tests, editing = _tests_sidebar()
    runs = []
    for k, cfg in enumerate(tests):
        try:
            sim = simulate(json.dumps(_sim_config(cfg), sort_keys=True))
        except Exception as e:
            st.error(f"Test {k + 1} failed to simulate: {e}")
            st.stop()
        # A shallow copy named "Test k", so legends say which test is which (the cached run is untouched).
        runs.append(dataclasses.replace(sim, meta={**sim.meta, "name": f"Test {k + 1}"}))
    colors = categorical(len(runs))
    run = runs[editing]

    # Layout: the plots get the whole left column, starting right at the top.
    # Everything else lives in a persistent panel on the right that stays in
    # view (sticky) and scrolls on its own if it is taller than the window.
    # Below 640 px Streamlit stacks columns; the panel then goes first and stops sticking.
    side_col = "[data-testid='stColumn']:has(.st-key-echem_side)"
    st.html(
        "<style>[data-testid='stMainBlockContainer']{padding-top:3rem}"
        f"@media (min-width:641px){{{side_col}{{position:sticky;top:3.5rem;align-self:flex-start;"
        "max-height:calc(100vh - 4.5rem);overflow-y:auto;overflow-x:hidden}}"
        f"@media (max-width:640px){{{side_col}{{order:-1}}}}</style>"
    )
    # Voltage, current and temperature live in the side panel, so there is no
    # separate tab for them. The lambdas read xmode when called, after the panel sets it.
    views = {
        "Inside the cell": lambda: _inside(runs, colors),
        "Inside the particles": lambda: _particles(runs, colors),
        "Voltage losses": lambda: _losses(runs, colors, xmode),
        "Electrode windows": lambda: _windows(runs, colors),
        "Heat & plating": lambda: _heat_safety(runs, colors, xmode),
        "Any metric": lambda: _all_metrics(runs, colors, xmode),
        "Your analysis (workbench)": lambda: panels.render(run_to_celldata(run), prefixes=("A", "C", "example", "misc")),
    }
    animated = {"Inside the cell", "Inside the particles"}
    if st.session_state.get("echem_tab") not in views:
        st.session_state["echem_tab"] = next(iter(views))
    active = st.session_state["echem_tab"]

    main, side = st.columns([3, 1.15], gap="medium")
    with side, st.container(key="echem_side"):
        st.markdown("#### Electrochemistry")
        for r, c, glyph in zip(runs, colors, GLYPH):
            st.markdown(f"<span style='color:{c}'>●</span> `{glyph}` {r.label}", unsafe_allow_html=True)
        for test in dict.fromkeys(r.config["test"] for r in runs):
            st.caption(f"**{test}.** {TEST_HELP[test]}")
        borrowed = [r.meta["name"] for r in runs if r.meta.get("borrowed_parameters")]
        if borrowed:
            st.caption(f"{', '.join(borrowed)}: this parameter set has no thermal/current-collector data, so those values are borrowed from Chen2020.")
        _kpis(runs, colors)
        xmode = st.radio("x-axis for time plots", ["Test progress (%)", "Time (h)", "Capacity (Ah)"], horizontal=True)
        _mini_response(runs, xmode, cursor=active in animated)
    if active in animated:
        st.html(_CURSOR_SYNC, unsafe_allow_javascript=True)

    with main:
        # The tabs report which one is open, so only that one is drawn.
        tabs = st.tabs(list(views), key="echem_tab", on_change="rerun")
        for name, tab in zip(views, tabs):
            if name == active:
                with tab:
                    views[name]()
