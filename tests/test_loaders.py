"""Each loader is run on a small file written in that source's layout.

The files are generated from the analytic profile in ``conftest.py``, so the
expected cycle and step tables are known exactly. These fixtures encode our
understanding of each format; if a real file disagrees, fix the fixture and
the loader together.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from scipy.io import savemat

import batterylab as bl

from conftest import CHARGE_AH, DISCHARGE_AH, STEP_TYPES, make_profile


def assert_matches_profile(cycles, steps, n_cycles=2, tol=1e-6):
    assert len(cycles) == n_cycles
    np.testing.assert_allclose(cycles["charge_ah"], CHARGE_AH, atol=tol)
    np.testing.assert_allclose(cycles["discharge_ah"], DISCHARGE_AH, atol=tol)
    assert steps["step_type"].tolist() == STEP_TYPES * n_cycles


# --- batteryarchive -------------------------------------------------------


def test_batteryarchive(tmp_path, profile):
    path = tmp_path / "SNL_18650_NCA_25C_0-100_0.5-1C_a_timeseries.csv"
    pd.DataFrame(
        {
            "Date_Time": "2018-01-01 00:00:00",
            "Test_Time (s)": profile.test_time_s,
            "Cycle_Index": profile.cycle_index,
            "Current (A)": profile.current_a,
            "Voltage (V)": profile.voltage_v,
            "Charge_Capacity (Ah)": 0.0,
            "Discharge_Capacity (Ah)": 0.0,
            "Environment_Temperature (C)": 24.0,
            "Cell_Temperature (C)": profile.temperature_c,
        }
    ).to_csv(path, index=False)

    cycles, steps = bl.tidy("batteryarchive", path)
    assert_matches_profile(cycles, steps)
    meta = bl.load("batteryarchive", path).metadata
    assert meta["chemistry"] == "NCA"
    assert meta["ambient_temperature_c"] == 25
    assert meta["soc_window"] == (0, 100)
    assert meta["c_rate_discharge"] == 1.0


@pytest.mark.parametrize(
    "name, expected",
    [
        ("SNL_18650_NCA_25C_0-100_0.5/1C_a", {"chemistry": "NCA", "form_factor": "18650", "c_rate_charge": 0.5}),
        ("CALCE_CX2-16_prism_LCO_25C_0-100_0.5/0.5C_a", {"chemistry": "LCO", "form_factor": "prism"}),
        ("HNEI_18650_NMC_LCO_25C_0-100_0.5/1.5C_a", {"chemistry": "NMC-LCO", "c_rate_discharge": 1.5}),
        ("OX_1-1_pouch_LCO_40C_0-100_2/1.84C_a", {"ambient_temperature_c": 40.0, "group": "OX"}),
        ("SNL_18650_LFP_15C_20-80_0.5-3C_b_timeseries", {"soc_window": (20.0, 80.0), "replicate": "b"}),
    ],
)
def test_batteryarchive_parse_name(name, expected):
    from batterylab.loaders.batteryarchive import parse_name

    meta = parse_name(name)
    assert {k: meta[k] for k in expected} == expected


def test_batteryarchive_unparsed_name(tmp_path, profile):
    path = tmp_path / "MATR_b1c0_timeseries.csv"
    profile.rename(
        columns={
            "test_time_s": "Test_Time (s)",
            "cycle_index": "Cycle_Index",
            "current_a": "Current (A)",
            "voltage_v": "Voltage (V)",
        }
    ).drop(columns=["temperature_c", "step_index"]).to_csv(path, index=False)
    cell = bl.load("batteryarchive", path)
    assert cell.metadata["group"] == "MATR"
    assert cell.timeseries["temperature_c"].isna().all()


# --- Arbin / CALCE --------------------------------------------------------


def _arbin_frame(p: pd.DataFrame, start: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Data_Point": np.arange(len(p)),
            "Date_Time": [start + timedelta(seconds=float(s)) for s in p.test_time_s],
            "Test_Time(s)": p.test_time_s.to_numpy(),
            "Step_Index": p.step_index.to_numpy(),
            "Cycle_Index": p.cycle_index.to_numpy(),
            "Current(A)": p.current_a.to_numpy(),
            "Voltage(V)": p.voltage_v.to_numpy(),
            "Temperature (C)_1": p.temperature_c.to_numpy(),
        }
    )


def test_arbin_directory_restarts_are_stitched(tmp_path):
    one = make_profile(1)
    start = datetime(2010, 9, 1, 8, 0, 0)
    first = _arbin_frame(one, start)
    second = _arbin_frame(one, start + timedelta(seconds=float(one.test_time_s.iloc[-1])))
    # Names sort opposite to time order, so ordering must come from Date_Time.
    first.to_csv(tmp_path / "CS2_35_b.csv", index=False)
    second.to_csv(tmp_path / "CS2_35_a.csv", index=False)

    cell = bl.load("arbin", tmp_path)
    cycles, steps = cell.cycles(), cell.steps()
    assert_matches_profile(cycles, steps)
    assert cycles["cycle_index"].tolist() == [1, 2]
    np.testing.assert_allclose(cell.timeseries.test_time_s, make_profile(2).test_time_s)


def test_arbin_xlsx_reads_channel_sheets(tmp_path, profile):
    path = tmp_path / "CS2_35_1_10_11.xlsx"
    with pd.ExcelWriter(path) as xl:
        pd.DataFrame({"info": ["test"]}).to_excel(xl, sheet_name="Info", index=False)
        _arbin_frame(profile, datetime(2010, 1, 10)).to_excel(xl, sheet_name="Channel_1-008", index=False)
    assert_matches_profile(*bl.tidy("arbin", path))


# --- NASA PCoE ------------------------------------------------------------


def _datevec(dt: datetime) -> np.ndarray:
    return np.array([dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6])


def test_nasa_pcoe(tmp_path, profile):
    start = datetime(2008, 4, 2, 13, 8, 17)
    records = []
    for cycle in (1, 2):
        c = profile[profile.cycle_index == cycle]
        for kind, steps in (("charge", (1, 2)), ("discharge", (3, 4))):
            part = c[c.step_index.isin(steps)]
            t0 = float(part.test_time_s.iloc[0])
            data = {
                "Voltage_measured": part.voltage_v.to_numpy(),
                "Current_measured": part.current_a.to_numpy(),
                "Temperature_measured": part.temperature_c.to_numpy(),
                "Time": part.test_time_s.to_numpy() - t0,
            }
            if kind == "discharge":
                data["Capacity"] = 1.0
            records.append((kind, 24.0, _datevec(start + timedelta(seconds=t0)), data))
        z = np.array([0.05 + 0.01j, 0.06 - 0.002j, 0.08 - 0.01j])
        imp = {"Re": 0.05, "Rct": 0.07, "Rectified_Impedance": z, "Battery_impedance": z}
        records.append(("impedance", 24.0, _datevec(start + timedelta(seconds=cycle * 9000.0)), imp))

    arr = np.empty((1, len(records)), dtype=[("type", "O"), ("ambient_temperature", "O"), ("time", "O"), ("data", "O")])
    for k, r in enumerate(records):
        arr[0, k] = r
    path = tmp_path / "B0005.mat"
    savemat(path, {"B0005": {"cycle": arr}})

    cell = bl.load("nasa_pcoe", path)
    # Records are joined at 1 s resolution, which can shift up to one sample.
    assert_matches_profile(cell.cycles(), cell.steps(), tol=1e-3)
    assert cell.metadata["cell_id"] == "B0005"
    assert len(cell.extras["eis_summary"]) == 2
    assert cell.extras["eis_summary"]["rct_ohm"].tolist() == [0.07, 0.07]
    assert len(cell.extras["eis"]) == 6
    assert cell.extras["reported_capacity"]["capacity_ah"].tolist() == [1.0, 1.0]


# --- Oxford ---------------------------------------------------------------


def test_oxford_days_and_derived_current(tmp_path):
    q_ah, t_day = 0.74, 737000.0
    cell = {}
    for cyc in ("cyc0000", "cyc0100"):
        segs = {}
        for name, hours in (("C1ch", 1.0), ("C1dc", 1.0), ("OCVch", 18.0), ("OCVdc", 18.0)):
            t_h = np.linspace(0, hours, 361)
            frac = t_h / hours
            segs[name] = {
                "t": t_day + t_h / 24,
                "v": 3.0 + 1.2 * (frac if name.endswith("ch") else 1 - frac),
                "q": 1000 * q_ah * frac * (1 if name.endswith("ch") else -1),
                "T": np.full_like(t_h, 40.0),
            }
            t_day += (hours + 0.5) / 24
        cell[cyc] = segs
        t_day += 3.0  # ageing cycles between characterisations are not recorded
    path = tmp_path / "Oxford_Battery_Degradation_Dataset_1.mat"
    savemat(path, {"Cell1": cell, "Cell2": cell})

    data = bl.load("oxford", path, cell="Cell2")
    assert data.metadata["time_unit_detected"] == "days"
    steps = data.steps()
    assert steps["cycle_index"].tolist() == [0] * 4 + [100] * 4
    assert steps["step_type"].tolist() == ["charge", "discharge"] * 4
    np.testing.assert_allclose(steps["charge_ah"] + steps["discharge_ah"], q_ah, rtol=1e-6)
    np.testing.assert_allclose(steps["current_mean_a"].abs()[:2], q_ah, rtol=1e-6)
    with pytest.raises(KeyError, match="Cell9"):
        bl.load("oxford", path, cell="Cell9")


def test_oxford_repairs_duplicate_and_appended_segments():
    from batterylab.loaders.oxford import _repair_overlaps

    def seg(cycle, step, t):
        t = np.asarray(t, float)
        return (cycle, step, "OCVdc", t, t * 0 + 3.7, t, t * 0 + 40)

    segs = [
        seg(0, 3, [0, 1, 2, 10, 11]),  # samples 10, 11 belong to the next segment
        seg(100, 3, [10, 11]),
        seg(200, 3, [10, 11]),  # exact copy of cycle 100
    ]
    out, issues = _repair_overlaps(segs)
    assert [(s[0], s[3].tolist()) for s in out] == [(0, [0, 1, 2]), (100, [10, 11])]
    assert len(issues) == 2


# --- McMaster LG HG2 ------------------------------------------------------


def test_mcmaster_mat(tmp_path):
    p = make_profile(1)
    path = tmp_path / "551_HPPC.mat"
    meas = {
        "Time": p.test_time_s.to_numpy(),
        "Voltage": p.voltage_v.to_numpy(),
        "Current": p.current_a.to_numpy(),
        "Battery_Temp_degC": p.temperature_c.to_numpy(),
        "Ah": np.zeros(len(p)),
    }
    savemat(path, {"meas": meas})
    cycles, steps = bl.tidy("mcmaster_hg2", path)
    assert_matches_profile(cycles, steps, n_cycles=1)
    single = bl.load("mcmaster_hg2", path, steps="single").steps()
    assert single["step_type"].tolist() == ["dynamic"]


def test_mcmaster_csv(tmp_path):
    p = make_profile(1)
    secs = p.test_time_s.to_numpy() + 25 * 3600  # exercise hours > 24
    prog = [f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}" for s in secs]
    body = pd.DataFrame(
        {
            "Time Stamp": "1/1/2019 00:00:00 AM",
            "Step": p.step_index.to_numpy(),
            "Status": "TABLE",
            "Prog Time": prog,
            "Step Time": "0:00:00.000",
            "Cycle": 0,
            "Cycle Level": 0,
            "Procedure": "HPPC",
            "Voltage": p.voltage_v.to_numpy(),
            "Current": p.current_a.to_numpy(),
            "Temperature": p.temperature_c.to_numpy(),
            "Capacity": 0.0,
            "WhAccu": 0.0,
            "Cnt": 0,
            "Empty": "",
        }
    )
    units = pd.DataFrame([["", "", "", "hh:mm:ss.SSS", "hh:mm:ss.SSS", "", "", "", "V", "A", "C", "Ah", "Wh", "Cnt", ""]], columns=body.columns)
    path = tmp_path / "03-11-17_08.47 25degC_5Pulse_HPPC_Pan18650PF.csv"
    with open(path, "w", newline="") as fh:
        fh.write("Digatron export\nCell,LG HG2\n\n")
        pd.concat([units, body.astype(object)]).to_csv(fh, index=False)
    cycles, steps = bl.tidy("mcmaster_hg2", path)
    assert_matches_profile(cycles, steps, n_cycles=1)
    assert steps["start_time_s"].iloc[0] == pytest.approx(25 * 3600)


def test_unknown_source():
    with pytest.raises(ValueError, match="unknown source"):
        bl.load("nope", "x.csv")
