"""Checks against the real downloaded datasets. Skipped when data/ is absent.

See the README for where each file comes from and where to put it.
"""

from pathlib import Path

import numpy as np
import pytest

import batterylab as bl

DATA = Path(__file__).resolve().parent.parent / "data"
NASA = DATA / "nasa" / "B0005.mat"
OXFORD = DATA / "oxford" / "Oxford_Battery_Degradation_Dataset_1.mat"


def needs(path: Path):
    return pytest.mark.skipif(not path.exists(), reason=f"{path.name} not downloaded")


@pytest.fixture(scope="module")
def nasa():
    return bl.load("nasa_pcoe", NASA)


@needs(NASA)
def test_nasa_integrated_capacity_matches_reported(nasa):
    reported = nasa.extras["reported_capacity"]
    single = reported[~reported["cycle_index"].duplicated(keep=False)]
    merged = single.merge(nasa.cycles()[["cycle_index", "discharge_ah"]], on="cycle_index")
    assert len(merged) == 166
    # NASA reports capacity to 2.7 V; we integrate the whole record, which
    # runs slightly past it, so ours is 3-6 mAh larger.
    diff = merged["discharge_ah"] - merged["capacity_ah"]
    assert diff.between(0, 0.01).all()


@needs(NASA)
def test_nasa_structure(nasa):
    steps = nasa.steps()
    assert nasa.cycles()["cycle_index"].max() == 170
    assert (steps["step_type"] == "discharge").sum() == 168
    assert len(nasa.extras["eis_summary"]) == 278
    dis = steps[steps["step_type"] == "discharge"]
    np.testing.assert_allclose(dis["current_mean_a"].median(), -2.0, atol=0.02)


@needs(OXFORD)
@pytest.mark.parametrize("cell", [f"Cell{i}" for i in range(1, 9)])
def test_oxford_all_cells(cell):
    data = bl.load("oxford", OXFORD, cell=cell)
    assert data.metadata["time_unit_detected"] == "days"
    steps = data.steps()
    assert set(steps["step_type"]) == {"charge", "discharge"}
    one_c = steps[steps["step_index"].isin([0, 1])]
    ocv = steps[steps["step_index"].isin([2, 3])]
    # README: 1C = 740 mA, pseudo-OCV = 40 mA.
    np.testing.assert_allclose(one_c["current_mean_a"].abs().median(), 0.74, rtol=0.01)
    np.testing.assert_allclose(ocv["current_mean_a"].abs().median(), 0.04, rtol=0.05)
    # Integrated charge agrees with the file's own charge counter.
    ts = data.timeseries
    counter = ts.groupby(["cycle_index", "step_index"])["source_q_ah"].agg(lambda q: q.iloc[-1] - q.iloc[0])
    integ = steps.set_index(["cycle_index", "step_index"])
    np.testing.assert_allclose(
        (integ["charge_ah"] - integ["discharge_ah"]).sort_index(), counter.sort_index(), atol=2e-3
    )


@needs(OXFORD)
def test_oxford_cell1_defects_are_repaired():
    data = bl.load("oxford", OXFORD, cell="Cell1")
    issues = data.metadata["data_issues"]
    assert sum("duplicate of cyc7000" in i for i in issues) == 4
    assert sum("overlap the next segment" in i for i in issues) == 4
    steps = data.steps()
    assert 7100 not in set(steps["cycle_index"])
    ocv = steps[steps["step_index"] == 3].set_index("cycle_index")["discharge_ah"]
    # The repaired cyc3200 pseudo-OCV sits between its neighbours.
    assert ocv[3100] > ocv[3200] > ocv[3300]


@needs(OXFORD)
def test_oxford_cell1_first_charge():
    steps = bl.load("oxford", OXFORD, cell="Cell1").steps()
    first = steps[(steps["cycle_index"] == 0) & (steps["step_index"] == 0)].iloc[0]
    assert first["charge_ah"] == pytest.approx(0.7241, abs=1e-3)
