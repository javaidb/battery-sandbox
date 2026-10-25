import numpy as np
import pandas as pd
import pytest

from batterylab import CellData, SchemaError, cycle_table, step_table
from batterylab.schema import conform

from conftest import (
    CHARGE_AH,
    CHARGE_WH,
    CYCLE_S,
    DISCHARGE_AH,
    DISCHARGE_WH,
    STEP_TYPES,
    make_profile,
)


def test_conform_orders_and_fills(profile):
    df = conform(profile[["voltage_v", "current_a", "cycle_index", "test_time_s"]])
    assert list(df.columns[:6]) == [
        "test_time_s", "cycle_index", "step_index", "current_a", "voltage_v", "temperature_c"
    ]
    assert (df["step_index"] == -1).all()
    assert df["temperature_c"].isna().all()


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda d: d.drop(columns="voltage_v"), "missing"),
        (lambda d: d.assign(test_time_s=d["test_time_s"][::-1].to_numpy()), "monotonic"),
        (lambda d: d.assign(current_a=np.where(d.index == 5, np.nan, d["current_a"])), "NaN"),
        (lambda d: d.iloc[:0], "empty"),
    ],
)
def test_conform_rejects_bad_input(profile, mutate, msg):
    with pytest.raises(SchemaError, match=msg):
        conform(mutate(profile))


def test_step_table_known_answers(profile):
    steps = step_table(conform(profile))
    assert len(steps) == 8
    assert steps["step_type"].tolist() == STEP_TYPES * 2
    assert steps["step_id"].tolist() == list(range(8))
    chg, dis = steps.iloc[0], steps.iloc[2]
    assert chg.charge_ah == pytest.approx(CHARGE_AH)
    assert chg.charge_wh == pytest.approx(CHARGE_WH)
    assert chg.discharge_ah == 0
    assert dis.discharge_ah == pytest.approx(DISCHARGE_AH)
    assert dis.discharge_wh == pytest.approx(DISCHARGE_WH)
    assert chg.duration_s == 3600
    assert dis.voltage_start_v == pytest.approx(4.0) and dis.voltage_end_v == pytest.approx(3.0)


def test_cycle_table_known_answers(profile):
    cycles = cycle_table(conform(profile))
    assert cycles["cycle_index"].tolist() == [1, 2]
    assert cycles["n_steps"].tolist() == [4, 4]
    np.testing.assert_allclose(cycles["charge_ah"], CHARGE_AH)
    np.testing.assert_allclose(cycles["discharge_ah"], DISCHARGE_AH)
    np.testing.assert_allclose(cycles["coulombic_efficiency"], 1.0)
    np.testing.assert_allclose(cycles["energy_efficiency"], DISCHARGE_WH / CHARGE_WH)
    np.testing.assert_allclose(cycles["duration_s"], CYCLE_S)


def test_steps_derived_from_current_sign_match_source_steps(profile):
    derived = step_table(conform(profile.assign(step_index=-1)))
    source = step_table(conform(profile))
    assert derived["step_type"].tolist() == source["step_type"].tolist()
    np.testing.assert_allclose(derived["charge_ah"], source["charge_ah"])
    np.testing.assert_allclose(derived["discharge_ah"], source["discharge_ah"])


def test_reused_source_step_number_gets_new_id(profile):
    # Two consecutive cycles that both use step 1 must not merge.
    steps = step_table(conform(profile.assign(step_index=1)))
    assert steps["cycle_index"].tolist() == [1, 2]


def test_integration_skips_gaps(profile):
    gap = profile.copy()
    later = gap["cycle_index"] == 2
    gap.loc[later, "test_time_s"] += 10_000  # pause between cycles
    cycles = cycle_table(conform(gap), max_gap_s=600)
    np.testing.assert_allclose(cycles["charge_ah"], CHARGE_AH)

    # A gap inside a step is dropped from the integral, not bridged.
    inside = profile.copy()
    first_charge = (inside["cycle_index"] == 1) & (inside["step_index"] == 1)
    half = inside.index[first_charge][len(inside.index[first_charge]) // 2]
    inside.loc[half:, "test_time_s"] += 5000
    steps = step_table(conform(inside), max_gap_s=600)
    assert steps.loc[0, "charge_ah"] == pytest.approx(CHARGE_AH - 10 / 3600)


def test_dynamic_step_detected():
    t = np.arange(0, 600.0)
    ts = pd.DataFrame(
        {
            "test_time_s": t,
            "cycle_index": 0,
            "current_a": 2 * np.sin(t / 10),
            "voltage_v": 3.7 + 0.01 * np.sin(t / 10),
        }
    )
    steps = CellData(ts.assign(step_index=0)).steps()
    assert steps["step_type"].tolist() == ["dynamic"]


def test_celldata_methods_match_functions(profile):
    cell = CellData(profile, {"cell_id": "x"})
    pd.testing.assert_frame_equal(cell.cycles(), cycle_table(cell.timeseries))
    pd.testing.assert_frame_equal(cell.steps(), step_table(cell.timeseries))


def test_step_capacity_restarts_each_step(profile):
    from batterylab.tables import step_capacity

    ts = conform(profile)
    sc = step_capacity(ts)
    ends = sc.groupby("step_id")["q_ah"].agg(["first", "last"])
    np.testing.assert_allclose(ends["first"], 0.0)
    np.testing.assert_allclose(ends["last"], [1, 0, 1, 0] * 2, atol=1e-12)
    pd.testing.assert_index_equal(sc.index, ts.index)


def test_rest_threshold_ignores_noise():
    ts = make_profile(1)
    noise = np.random.default_rng(1).normal(0, 2e-3, len(ts))
    steps = step_table(conform(ts.assign(step_index=-1, current_a=ts["current_a"] + noise)))
    assert steps["step_type"].tolist() == STEP_TYPES
