"""Shared dashboard pieces: data discovery, cached simulations, colours, chart styling."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # makes `workbench` importable

import batterylab as bl  # noqa: E402
from batterylab import bench, echem  # noqa: E402
from batterylab.tables import step_capacity  # noqa: E402

# Validated categorical slots and the blue sequential ramp from the dataviz
# reference palette, with separate light and dark steps. Up to 3 series where
# every pair must be distinguishable (overlaid runs); up to 6 for stacks and
# lines, where only neighbours need to be.
_CATEGORICAL = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"],
}
# Ordered early -> late; the late end is the most prominent against the surface.
_SEQUENTIAL = {
    "light": ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"],
    "dark": ["#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4", "#cde2fb"],
}
MUTED = "#898781"
CRITICAL = "#d03b3b"
MAX_PLOT_POINTS = 20_000


def _mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


# Voltage, current and temperature each keep one colour everywhere they are
# plotted alone. Drawn from palette slots not used for run identity (blue,
# orange, aqua), so a metric is never mistaken for a run.
_METRIC = {
    "light": {"voltage": "#6250d6", "current": "#eda100", "temperature": "#e34948"},
    "dark": {"voltage": "#9085e9", "current": "#c98500", "temperature": "#e66767"},
}


def metric_colors() -> dict[str, str]:
    return _METRIC[_mode()]


def categorical(n: int = 3) -> list[str]:
    return _CATEGORICAL[_mode()][:n]


def band_fill() -> str:
    return "rgba(57,135,229,0.18)" if _mode() == "dark" else "rgba(42,120,214,0.15)"


def ramp(n: int) -> list[str]:
    """n colours from the sequential ramp, early to late."""
    seq = _SEQUENTIAL[_mode()]
    if n <= 1:
        return [seq[-3]] * n
    idx = np.linspace(0, len(seq) - 1, n).round().astype(int)
    return [seq[i] for i in idx]


def sequential_scale() -> list[list]:
    """Plotly colourscale from the sequential ramp: low values recede, high values stand out (both themes)."""
    seq = ramp(10)
    return [[i / (len(seq) - 1), c] for i, c in enumerate(seq)]


def diverging_scale() -> list[list]:
    """Plotly colourscale for signed values: blue (negative), neutral grey at zero, red (positive)."""
    if _mode() == "dark":
        return [[0, "#3987e5"], [0.5, "#383835"], [1, "#e66767"]]
    return [[0, "#2a78d6"], [0.5, "#f0efec"], [1, "#e34948"]]


def style(fig, height: int = 420, legend_title: str | None = None):
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
        hovermode="closest",
        legend=dict(title=legend_title, orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(showgrid=True, zeroline=False)
    fig.update_yaxes(showgrid=True, zeroline=False)
    return fig


# --- tabs -------------------------------------------------------------------

# Material Symbols names (fonts.google.com/icons) drawn above each tab's name.
TAB_ICONS = {
    "Inside the cell": "layers",
    "Inside the particles": "bubble_chart",
    "Space–time maps": "grid_on",
    "Voltage losses": "bolt",
    "Electrode windows": "straighten",
    "Heat & plating": "local_fire_department",
    "Physics knobs": "tune",
    "Ageing": "hourglass_bottom",
    "Any metric": "query_stats",
    "Your analysis (workbench)": "construction",
    "Plant & sensors": "sensors",
    "SoC estimation": "battery_charging_full",
    "Robustness": "shield",
    "ECM check": "electrical_services",
    "Your panels (workbench)": "construction",
    "Data": "dataset",
    "Fade": "trending_down",
    "V–Q curves": "ssid_chart",
    "Time series": "timeline",
    "Tables": "table_chart",
    "Extras": "extension",
    "Metadata": "info",
}

# Icon stacked over the name; the gap between tabs grows with the window,
# from 1rem on a laptop to 3rem on a wide monitor. Tabs that do not fit wrap onto a second row
# rather than scrolling out of sight.
_TAB_CSS = (
    "<style>[data-testid='stTabs'] [role='tablist']{flex-wrap:wrap;row-gap:.25rem;"
    "column-gap:clamp(1rem,2.4vw - .5rem,3rem)}"
    "[data-testid='stTab']{height:auto;padding:.4rem .25rem 0}"
    "[data-testid='stTab'] [data-testid='stMarkdownContainer'] p{display:flex;flex-direction:column;"
    "align-items:center;gap:.15rem;line-height:1.2;padding-bottom:.35rem}"
    "[data-testid='stTab'] p>span[role='img']{font-size:1.25rem}</style>"
)


def tab_label(name: str) -> str:
    icon = TAB_ICONS.get(name)
    return f":material/{icon}: {name}" if icon else name


def tabs(names: list[str], **kwargs):
    """st.tabs with an icon above each name; with a key, session state holds tab_label(name)."""
    st.html(_TAB_CSS)
    return st.tabs([tab_label(n) for n in names], **kwargs)


def x_axis(run, mode: str) -> tuple[np.ndarray, str]:
    """x values and axis label for a simulated run: test progress (%), time (h) or capacity moved (Ah)."""
    t = run.series.index.to_numpy()
    if mode == "Time (h)":
        return t / 3600, "time (h)"
    if mode == "Capacity (Ah)":
        q = run.series["Discharge capacity [Ah]"].to_numpy()
        return np.abs(q - q[0]), "|capacity moved| (Ah)"
    return 100 * (t - t[0]) / max(t[-1] - t[0], 1e-9), "test progress (%)"


def thin(df: pd.DataFrame, max_points: int = MAX_PLOT_POINTS) -> pd.DataFrame:
    """Stride-downsample for plotting; analysis always uses the full data."""
    step = max(1, int(np.ceil(len(df) / max_points)))
    return df.iloc[::step]


# --- measured datasets ------------------------------------------------------


@dataclass(frozen=True)
class CellOption:
    label: str
    source: str
    path: str | None
    kwargs_json: str = "{}"

    @property
    def kwargs(self) -> dict:
        return json.loads(self.kwargs_json)


def discover_cells() -> list[CellOption]:
    """Every cell the loaders can read under data/."""
    opts: list[CellOption] = []

    def add(source, path, label, **kw):
        opts.append(CellOption(label, source, str(path), json.dumps(kw, sort_keys=True)))

    for p in sorted((DATA / "nasa").glob("B*.mat")):
        add("nasa_pcoe", p, f"NASA PCoE · {p.stem}")
    for p in sorted((DATA / "oxford").glob("Oxford_Battery_Degradation_Dataset_1*.mat")):
        for i in range(1, 9):
            add("oxford", p, f"Oxford · Cell{i}", cell=f"Cell{i}")
    for p in sorted((DATA / "batteryarchive").glob("*_timeseries.csv")):
        add("batteryarchive", p, f"batteryarchive · {p.name.removesuffix('_timeseries.csv')}")
    arbin = DATA / "arbin"
    if arbin.is_dir():
        for p in sorted(arbin.iterdir()):
            if p.is_dir() or p.suffix.lower() in {".csv", ".xlsx", ".xls"}:
                add("arbin", p, f"Arbin · {p.name}")
    for p in sorted((DATA / "mcmaster").rglob("*")) if (DATA / "mcmaster").is_dir() else []:
        if p.suffix.lower() in {".mat", ".csv"}:
            add("mcmaster_hg2", p, f"McMaster HG2 · {p.relative_to(DATA / 'mcmaster')}")
    for p in sorted((DATA / "biologic").glob("*.mpr")) if (DATA / "biologic").is_dir() else []:
        add("biologic", p, f"BioLogic · {p.name}")
    return opts


@st.cache_resource(max_entries=6, show_spinner="Loading cell…")
def load_cell(opt: CellOption) -> bl.CellData:
    return bl.load(opt.source, opt.path, **opt.kwargs)


@st.cache_data(max_entries=6, show_spinner=False)
def cell_tables(_cell: bl.CellData, key: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(cycles, steps, per-sample step capacity), cached per cell key."""
    return _cell.cycles(), _cell.steps(), step_capacity(_cell.timeseries)


def rate_groups(steps: pd.DataFrame, n: int = 3) -> pd.Series:
    """Label each step by its |mean current|, grouping currents within 15 % of each other.

    The n most common groups among charge/discharge steps keep their label
    (the group median, 2 s.f.); the rest become 'other'.
    """
    amps = steps["current_mean_a"].abs().to_numpy()
    order = np.argsort(amps)
    group = np.empty(len(amps), dtype=int)
    g, start = 0, None
    for i in order:
        if start is not None and amps[i] > 1.15 * start + 1e-6:
            g += 1
            start = None
        start = amps[i] if start is None else start
        group[i] = g
    medians = pd.Series(amps).groupby(group).median()
    labels = pd.Series([f"{float(f'{medians[k]:.2g}'):g} A" for k in group], index=steps.index)
    top = labels[steps["step_type"].isin(["charge", "discharge"])].value_counts().index[:n]
    return labels.where(labels.isin(top), "other")


# --- simulations ------------------------------------------------------------


@st.cache_data(max_entries=16, show_spinner="Simulating…")
def simulate(config_json: str) -> echem.EchemRun:
    return echem.simulate_test(**json.loads(config_json))


@st.cache_data(max_entries=12, show_spinner=False)
def simulate_ageing(config_json: str):
    from batterylab.ageing import simulate_ageing as run

    return run(**json.loads(config_json))


@st.cache_data(max_entries=8, show_spinner=False)
def ocp_curves(chemistry: str) -> pd.DataFrame:
    return echem.ocp_curves(chemistry)


@st.cache_data(max_entries=24, show_spinner="Running the plant…")
def run_plant(scenario_json: str) -> bench.PlantRun:
    return bench.run_plant(bench.Scenario(**json.loads(scenario_json)))


def scenario_key(s: bench.Scenario) -> str:
    return json.dumps(asdict(s), sort_keys=True)


def run_to_celldata(run: echem.EchemRun) -> bl.CellData:
    """Wrap a simulated run as CellData so workbench panels can use it."""
    s = run.series
    ts = pd.DataFrame(
        {
            "test_time_s": s.index.to_numpy(),
            "cycle_index": 0,
            "current_a": s["Current [A]"].to_numpy(),
            "voltage_v": s["Voltage [V]"].to_numpy(),
            "temperature_c": s["Cell temperature [°C]"].to_numpy(),
        }
    )
    return bl.CellData(ts, {"source": "pybamm", "cell_id": run.label, **run.meta, "config": run.config})
