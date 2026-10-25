import numpy as np
import pytest

pybamm = pytest.importorskip("pybamm")

import batterylab as bl
from batterylab.synthetic import _capacities, apply_degradation

SLOW_CYCLE = (
    "Discharge at C/10 until 2.5 V",
    "Rest for 1 hour",
    "Charge at C/10 until 4.2 V",
    "Hold at 4.2 V until C/100",
    "Rest for 1 hour",
)


def test_apply_degradation_hits_targets():
    pv = pybamm.ParameterValues("Chen2020")
    fresh = _capacities(pv)
    aged = _capacities(apply_degradation(pv, LLI=0.1, LAM_PE=0.05, LAM_NE=0.2))
    assert aged["Q_Li_ah"] == pytest.approx(0.9 * fresh["Q_Li_ah"])
    assert aged["Q_p_ah"] == pytest.approx(0.95 * fresh["Q_p_ah"])
    assert aged["Q_n_ah"] == pytest.approx(0.8 * fresh["Q_n_ah"])
    assert _capacities(pv) == fresh  # input untouched


def test_apply_degradation_validates():
    pv = pybamm.ParameterValues("Chen2020")
    with pytest.raises(ValueError, match="LLI"):
        apply_degradation(pv, LLI=1.2)


@pytest.mark.slow
def test_simulate_conforms_and_steps_line_up():
    cell = bl.load("pybamm", n_cycles=2)
    steps = cell.steps()
    assert steps["step_type"].tolist() == ["discharge", "rest", "charge", "charge", "rest"] * 2
    cycles = cell.cycles()
    assert cycles["cycle_index"].tolist() == [1, 2]
    # Steady state: the second cycle returns what it took in.
    assert cycles.loc[1, "coulombic_efficiency"] == pytest.approx(1.0, abs=2e-3)
    assert cell.metadata["ground_truth"]["LLI"] == 0.0


@pytest.mark.slow
def test_lli_reduces_capacity_roughly_proportionally():
    def cap(**deg):
        cell = bl.load("pybamm", n_cycles=2, cycle=SLOW_CYCLE, model="SPM", degradation=deg, period="60 seconds")
        return cell.cycles()["discharge_ah"].iloc[-1]

    fresh, aged = cap(), cap(LLI=0.1)
    assert 0.8 * fresh < aged < 0.95 * fresh


@pytest.mark.slow
def test_noise_is_reproducible_and_leaves_truth_alone():
    a = bl.load("pybamm", n_cycles=1, noise_v=1e-3, seed=3)
    b = bl.load("pybamm", n_cycles=1, noise_v=1e-3, seed=3)
    clean = bl.load("pybamm", n_cycles=1)
    np.testing.assert_array_equal(a.timeseries.voltage_v, b.timeseries.voltage_v)
    resid = a.timeseries.voltage_v - clean.timeseries.voltage_v
    assert 5e-4 < resid.std() < 2e-3
