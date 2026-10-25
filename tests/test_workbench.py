"""The workbench plugin mechanism (not the dashboard UI)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import batterylab as bl  # noqa: E402
from workbench import discover  # noqa: E402

from conftest import make_profile  # noqa: E402


def test_example_panel_is_discovered_and_runs():
    panels, errors = discover()
    assert errors == {}
    p = panels["workbench.example_retention.retention"]
    assert p.module == "example"
    assert set(p.params) == {"step_type", "min_fraction"}
    pytest.importorskip("plotly")
    fig, summary = p.fn(bl.CellData(make_profile(3)), step_type="discharge", min_fraction=0.2)
    assert summary == {"steps": 3, "final_retention": 1.0}


def test_example_estimator_is_registered_and_runs():
    from batterylab import bench
    from workbench import ESTIMATORS

    discover()
    cls = ESTIMATORS["Coulomb counting (example)"]
    pytest.importorskip("pybamm")
    plant = bench.run_plant(bench.Scenario(duration_min=10))
    meas = bench.measure(plant, bench.Sensors())
    est = bench.run_estimator(cls, meas, bench.estimator_context(plant, bench.Sensors()))
    assert bench.soc_metrics(plant.truth, est)["max_abs_pct"] < 1.0
