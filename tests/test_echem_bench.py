import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pybamm")

from batterylab import bench, echem  # noqa: E402


@pytest.mark.slow
@pytest.mark.parametrize("chemistry", list(echem.CHEMISTRIES))
def test_every_chemistry_discharges(chemistry):
    run = echem.simulate_test(chemistry, "CC discharge", model="SPMe")
    s = run.series
    assert s["Voltage [V]"].min() == pytest.approx(run.meta["v_min"], abs=0.05)
    assert (s["Current [A]"].iloc[:5] < 0).all()  # discharge is negative in our convention
    assert s["Discharge capacity [Ah]"].iloc[-1] > 0.5 * run.meta["nominal_capacity_ah"]


@pytest.mark.slow
def test_dfn_exposes_fields_and_particles():
    run = echem.simulate_test(test="Relaxation after pulse", model="DFN")
    assert set(echem.FIELDS) <= set(run.fields)
    nt = len(run.t)
    assert run.fields["Electrolyte concentration [mol/m³]"]["full"].shape == (len(run.x_um["full"]), nt)
    assert {"n:near separator", "p:near separator"} <= set(run.particles)
    # The voltage-loss components add up to OCV - V.
    s = run.series
    parts = [k for k in echem.SERIES_GROUPS["Voltage breakdown"] if k != "Open-circuit voltage [V]"]
    gap = s["Voltage [V]"] - s["Open-circuit voltage [V]"]
    np.testing.assert_allclose(s[parts].sum(axis=1), gap, atol=5e-3)


@pytest.mark.slow
def test_fast_cold_charge_drives_anode_below_zero():
    warm = echem.simulate_test(test="CC-CV charge", c_rate=0.5, temperature_c=25, model="SPMe")
    cold = echem.simulate_test(test="CC-CV charge", c_rate=3.0, temperature_c=-10, model="SPMe")
    key = "Anode potential at separator vs Li [V]"
    assert warm.series[key].min() > 0
    assert cold.series[key].min() < warm.series[key].min()


def test_drive_profile_is_seeded_and_scaled():
    t, i = echem.drive_profile("urban", 600, nominal_ah=5.0, seed=3)
    t2, i2 = echem.drive_profile("urban", 600, nominal_ah=5.0, seed=3)
    np.testing.assert_array_equal(i, i2)
    assert len(t) == 600 and (i < 0).any() and (i > 0).any()  # discharge and regen
    assert np.abs(i).max() <= 2.0 * 5.0 + 1e-9


@pytest.fixture(scope="module")
def plant():
    return bench.run_plant(bench.Scenario(duration_min=20))


class Oracle:
    """Cheats by replaying the truth; used only to test the scoring."""

    truth = None

    def reset(self, ctx):
        self.k = 0

    def step(self, t, i, v, temp):
        soc = float(self.truth["soc"].iloc[self.k])
        self.k += 1
        return {"soc": soc, "soc_std": 0.0}


def test_plant_truth_is_consistent(plant):
    tr = plant.truth
    assert tr["soc"].iloc[0] == pytest.approx(0.9)
    # SoC falls by exactly the integrated discharge over the true capacity.
    dq = -np.trapezoid(tr["current_a"], tr["time_s"]) / 3600
    assert tr["soc"].iloc[0] - tr["soc"].iloc[-1] == pytest.approx(dq / plant.context["true_capacity_ah"], rel=1e-2)
    oc = plant.context["ocv_curve"]
    assert np.interp(0.9, oc["soc"], oc["ocv_v"]) == pytest.approx(tr["ocv_v"].iloc[0], abs=1e-3)


def test_context_hides_the_truth(plant):
    ctx = bench.estimator_context(plant, bench.Sensors(soc0_error=0.1))
    assert "true_capacity_ah" not in ctx
    assert ctx["soc0_guess"] == pytest.approx(1.0)  # 0.9 + 0.1, clipped to [0, 1]


def test_measure_applies_bias_and_noise(plant):
    m = bench.measure(plant, bench.Sensors(current_bias_a=0.2, voltage_noise_v=0.01, seed=4))
    np.testing.assert_allclose(m["current_a"] - plant.truth["current_a"], 0.2)
    assert 0.008 < (m["voltage_v"] - plant.truth["voltage_v"]).std() < 0.012


def test_oracle_scores_zero_and_bias_scores_worse(plant):
    Oracle.truth = plant.truth
    clean = bench.measure(plant, bench.Sensors())
    est = bench.run_estimator(Oracle, clean, bench.estimator_context(plant, bench.Sensors()))
    m = bench.soc_metrics(plant.truth, est)
    assert m["rmse_pct"] == pytest.approx(0) and m["convergence_s"] == 0

    shifted = est.assign(soc=est["soc"] + 0.05)
    m2 = bench.soc_metrics(plant.truth, shifted)
    assert m2["rmse_pct"] == pytest.approx(5) and np.isnan(m2["convergence_s"])


def test_robustness_case_builds_variants():
    s, n = bench.robustness_case(bench.Scenario(), bench.Sensors(), bench.ROBUSTNESS_CASES["current bias +1 % of 1C"])
    assert n.current_bias_a == pytest.approx(0.05)  # 1 % of Chen2020's 5 Ah
    s, n = bench.robustness_case(bench.Scenario(), bench.Sensors(), bench.ROBUSTNESS_CASES["cold (0 °C)"])
    assert s.temperature_c == 0.0
