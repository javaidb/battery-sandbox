"""Electrochemistry view: what happens inside a cell during a test.

Pick a chemistry, a test and conditions in the sidebar; the PyBaMM model
re-runs (results are cached). Pin up to two runs to overlay them against the
current one, then change a condition and watch what moves.
"""

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

MAX_PINNED = 2
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


def _sidebar_config() -> dict:
    sb = st.sidebar
    chem = sb.selectbox("Chemistry", list(CHEMISTRIES), help="PyBaMM parameter sets from the literature")
    sb.caption(CHEMISTRIES[chem][1])
    test = sb.selectbox("Test", TESTS)
    cfg = {"chemistry": chem, "test": test}
    if test in ("CC discharge", "CC-CV charge", "Relaxation after pulse"):
        cfg["c_rate"] = sb.select_slider("C-rate", [0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0], value=1.0)
    if test == "Drive cycle":
        cfg["drive"] = sb.selectbox("Driving style", ["urban", "highway", "aggressive"])
        cfg["drive_minutes"] = sb.slider("Duration (min)", 5, 60, 20, 5)
    cfg["temperature_c"] = float(sb.slider("Ambient temperature (°C)", -10, 45, 25, 5))
    with sb.expander("Model and ageing"):
        default_model = "SPMe" if test == "Drive cycle" else "DFN"
        models = ["DFN", "SPMe", "SPM"]
        cfg["model"] = st.selectbox(
            "Model", models, index=models.index(default_model),
            help="DFN resolves every particle across the electrode; SPMe/SPM use one representative particle per electrode and are much faster.",
        )
        cfg["thermal"] = st.checkbox("Lumped thermal model", True)
        st.caption("Prescribed degradation, as fractions of the fresh cell")
        cfg["degradation"] = {
            "LLI": st.slider("LLI: lithium inventory lost", 0.0, 0.3, 0.0, 0.01),
            "LAM_PE": st.slider("LAM_PE: cathode material lost", 0.0, 0.3, 0.0, 0.01),
            "LAM_NE": st.slider("LAM_NE: anode material lost", 0.0, 0.3, 0.0, 0.01),
        }
    return cfg


def _pins(current) -> list:
    """Sidebar controls for pinned runs. Returns [current, *pinned]."""
    pinned = st.session_state.setdefault("_pinned", [])
    sb = st.sidebar
    sb.divider()
    sb.subheader("Compare runs")
    if sb.button("📌 Pin this run", disabled=len(pinned) >= MAX_PINNED or any(p.label == current.label for p in pinned), use_container_width=True):
        pinned.append(current)
        st.rerun()
    colors = categorical(3)
    # Colour identifies a run in most views; line style identifies it in the V/I/T plots.
    glyph = ["━━", "╍╍", "┅┅"]
    sb.markdown(f"<span style='color:{colors[0]}'>●</span> `{glyph[0]}` **current:** {current.label}", unsafe_allow_html=True)
    for k, p in enumerate(list(pinned)):
        c1, c2 = sb.columns([6, 1])
        c1.markdown(f"<span style='color:{colors[k + 1]}'>●</span> `{glyph[k + 1]}` {p.label}", unsafe_allow_html=True)
        if c2.button("✕", key=f"unpin{k}", help="Unpin"):
            pinned.pop(k)
            st.rerun()
    if not pinned:
        sb.caption(f"Pin up to {MAX_PINNED} runs, then change a setting to compare.")
    return [current, *pinned]


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


def _kpis(run):
    s = run.series
    v, i = s["Voltage [V]"], s["Current [A]"]
    dt = np.diff(s.index.to_numpy(), prepend=s.index[0])
    e_wh = float(np.sum(v * i * dt) / 3600)
    q = s["Discharge capacity [Ah]"]
    anode = s["Anode potential at separator vs Li [V]"].min()
    items = [
        ("Capacity moved", f"{abs(q.iloc[-1] - q.iloc[0]):.3f} Ah", f"Nominal {run.meta['nominal_capacity_ah']:g} Ah"),
        ("Energy", f"{abs(e_wh):.2f} Wh", "Delivered on discharge, absorbed on charge"),
        ("Voltage range", f"{v.min():.2f}–{v.max():.2f} V", f"Cut-offs {run.meta['v_min']:g}–{run.meta['v_max']:g} V"),
        ("Peak temp. rise", f"{s['Cell temperature [°C]'].max() - run.config['temperature_c']:.1f} K", "Above ambient"),
        ("Min anode vs Li", f"{anode * 1000:.0f} mV",
         ("Plating possible" if anode < 0 else "No plating risk") + " (anode/separator interface; below 0 V lithium can plate)"),
    ]
    cells = []
    for label, value, tip in items:
        warn = f" style='color:{CRITICAL}'" if label.startswith("Min anode") and anode < 0 else ""
        cells.append(f"<div title='{tip}'><div class='kpi-l'>{label}</div><div class='kpi-v'{warn}>{value}</div></div>")
    # A compact two-column grid: st.metric truncates in a narrow side panel.
    st.html(
        "<style>.kpis{display:grid;grid-template-columns:1fr 1fr;gap:.5rem .75rem;margin:.25rem 0 .5rem}"
        ".kpi-l{font-size:.75rem;opacity:.65}.kpi-v{font-size:1.05rem;font-weight:600;white-space:nowrap}</style>"
        f"<div class='kpis'>{''.join(cells)}</div>"
    )


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
                                     name=run.label, legendgroup=run.label, showlegend=False), row=r, col=1)
        cursor_x.append([float(x[k]) for k in _frame_idx(run)])
    if len(runs) > 1:  # legend entries for line style only, in neutral ink
        for run, dash in zip(runs, RUN_DASH):
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", line=dict(width=2, color=MUTED, dash=dash), name=run.label))
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
        st.plotly_chart(style(fig, 430), use_container_width=True, key="mini_response")
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
                                          name=run.label, legendgroup=run.label, showlegend=False,
                                          hovertemplate=f"{lab}<br>x %{{x:.0f}} µm<br>%{{y:.4g}}<extra>{run.label}</extra>"))
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
    runs_k = [r for r in runs if pick in r.particles]
    part = pick[0]
    fig = go.Figure()
    idxs = [_frame_idx(r) for r in runs_k]

    def traces(f):
        return [go.Scatter(x=r.r_um[part] / r.r_um[part][-1], y=r.particles[pick][:, idx[min(f, len(idx) - 1)]], mode="lines",
                           line=dict(width=2, color=c), name=r.label) for r, c, idx in zip(runs_k, colors, idxs)]

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


def _losses(runs, colors, xmode):
    run = runs[0]
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
    st.caption(f"Current run only ({run.label}). Each band is the size of one loss mechanism; together they explain the gap between "
               "open-circuit voltage and terminal voltage. The dotted line is that gap, computed directly, as a check.")
    cards(["Open-circuit voltage [V]"] + [LOSS_KEYS.get(lab, lab) for lab in labels], cols=3,
          colors=[None] + categorical(6)[: len(labels)])
    if len(runs) > 1:
        lab = st.selectbox("Compare one mechanism across runs", labels)
        fig = go.Figure()
        for r, c in zip(runs, colors):
            xr, _ = _x_axis(r, xmode)
            if lab in r.series:
                fig.add_trace(go.Scatter(x=xr, y=r.series[lab].abs() * 1000, mode="lines", line=dict(width=2, color=c), name=r.label))
        st.plotly_chart(style(fig, 360).update_layout(xaxis_title=xlabel, yaxis_title=f"|{lab}| (mV)"), use_container_width=True)


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
        # Pinned runs first, the current run last so it is drawn on top.
        for r, c in reversed(list(zip(runs, colors))):
            ocp = ocp_curves(r.config["chemistry"])
            sto = r.series[var].to_numpy()
            pot = np.interp(sto, ocp["stoichiometry"], ocp[key])
            fig.add_trace(go.Scatter(x=sto, y=pot, mode="lines", line=dict(width=5, color=c), name=r.label, opacity=0.85))
            fig.add_trace(go.Scatter(x=[sto[0], sto[-1]], y=[pot[0], pot[-1]], mode="markers", showlegend=False,
                                     marker=dict(size=10, color=["rgba(0,0,0,0)", c], line=dict(width=2, color=c))))
        fig.update_layout(title=name, xaxis_title="stoichiometry (lithium fraction)", yaxis_title="potential vs Li/Li⁺ (V)")
        col.plotly_chart(style(fig, 420), use_container_width=True)
    cards(["windows", "Anode OCP [V]", "Cathode OCP [V]"], cols=3)


def _heat_safety(runs, colors, xmode):
    run = runs[0]
    x, xlabel = _x_axis(run, xmode)
    c1, c2 = st.columns(2)
    heat = [lab for lab in SERIES_GROUPS["Heat"] if lab in run.series and lab != "Total heating [W]"]
    if heat:
        fig = go.Figure()
        for lab, color in zip(heat, categorical(3)):
            fig.add_trace(go.Scatter(x=x, y=run.series[lab], mode="lines", line=dict(width=2, color=color), name=lab.replace(" [W]", "")))
        fig.add_trace(go.Scatter(x=x, y=run.series["Total heating [W]"], mode="lines", line=dict(width=2, color=MUTED, dash="dot"), name="total"))
        c1.plotly_chart(style(fig, 420).update_layout(title="Heat sources (current run)", xaxis_title=xlabel, yaxis_title="W"), use_container_width=True)
    else:
        c1.info("Turn on the lumped thermal model to see heat sources.")
    fig = go.Figure()
    for r, c in zip(runs, colors):
        xr, _ = _x_axis(r, xmode)
        fig.add_trace(go.Scatter(x=xr, y=r.series["Anode potential at separator vs Li [V]"] * 1000, mode="lines", line=dict(width=2, color=c), name=r.label))
    fig.add_hline(y=0, line=dict(color=CRITICAL, width=1.5, dash="dash"), annotation_text="0 V: plating threshold", annotation_position="bottom right")
    c2.plotly_chart(style(fig, 420).update_layout(title="Anode potential at the separator", xaxis_title=xlabel, yaxis_title="mV vs Li/Li⁺"), use_container_width=True)
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
            fig.add_trace(go.Scatter(x=xr, y=r.series[lab], mode="lines", line=dict(width=2, color=c), name=r.label))
    st.plotly_chart(style(fig, 440).update_layout(xaxis_title=xlabel, yaxis_title=lab), use_container_width=True)
    card(lab)
    with st.expander("Data table (current run)"):
        st.dataframe(runs[0].series, use_container_width=True, height=300)
        st.download_button("Download CSV", runs[0].series.to_csv().encode(), file_name="run.csv")


# --- page -------------------------------------------------------------------


def page():
    mode =st.sidebar.radio("Data", ["Simulate (PyBaMM)", "Measured datasets"], horizontal=True,
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

    cfg = _sidebar_config()
    try:
        run = simulate(json.dumps(cfg, sort_keys=True))
    except Exception as e:
        st.error(f"The simulation failed for this combination: {e}")
        st.stop()
    runs = _pins(run)
    colors = categorical(len(runs))

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
        st.markdown(f"**{run.label}**")
        st.caption(TEST_HELP[cfg["test"]])
        if run.meta.get("borrowed_parameters"):
            st.caption(f"No thermal/current-collector data in this set; {len(run.meta['borrowed_parameters'])} values borrowed from Chen2020.")
        _kpis(run)
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
