# battery-sandbox

A self-directed playground for battery degradation (Route A) and BMS
algorithms (Route B). This repo starts with the **shared foundation**,
`batterylab`. It is a small Python package that loads raw cycler data from
public datasets into one canonical format and returns tidy per-cycle and
per-step tables.

## Quick start

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

```python
import batterylab as bl

# Any dataset -> (cycles, steps)
cycles, steps = bl.tidy("batteryarchive", "data/snl/SNL_18650_NCA_25C_0-100_0.5-1C_a_timeseries.csv")

# Or keep the raw canonical time series too
cell = bl.load("nasa_pcoe", "data/nasa/B0005.mat")
cell.timeseries          # canonical samples
cell.cycles(), cell.steps()
cell.extras["eis_summary"]

# Synthetic data with known degradation (the ground-truth trick)
cell = bl.load("pybamm", n_cycles=3, degradation={"LLI": 0.08, "LAM_PE": 0.05})
cell.metadata["ground_truth"]
```

`bl.available_sources()` lists the registered loaders.

## Dashboard

On Windows, double-click **`run_dashboard.bat`**. On first run it creates the
`.venv` and installs everything, then opens the dashboard in your browser.
Or from a terminal:

```bash
.venv/Scripts/python -m streamlit run dashboard/app.py
```

It opens at http://localhost:8501 with two views and a progress tracker.

**Electrochemistry** shows what happens inside a cell. Pick a chemistry
(NMC811, NMC532, LFP, LCO, NCA), a test (CC discharge, CC-CV charge, HPPC,
GITT, drive cycle, relaxation) and conditions (C-rate, temperature, model,
LLI/LAM ageing). PyBaMM re-runs and every view updates. Add up to three
tests, each with its own settings, and every tab compares them. Save a set
of tests as a preset (stored in `dashboard/presets.json`) and load it later,
or start from one of the built-in presets. Plots of a
single quantity overlay one colour per test. Charts that already use colour
for several mechanisms (voltage losses, heat sources) get a toggle to pick
the test instead.

A side panel stays in view on every tab. It shows each test's description,
its headline numbers and a small voltage/current/temperature plot. While an
animated tab plays, a cursor on that plot follows the animation.

* *Inside the cell*: a to-scale cross-section of the cell, above animated
  profiles through the electrode stack: electrolyte concentration and
  potential, particle surface stoichiometry, interfacial current,
  overpotential.
* *Inside the particles*: an animated cross-section of each test's
  particle, with shells coloured by lithium fraction, above the radial
  profiles.
* *Space–time maps*: one through-cell quantity over the whole test, as a
  colour map of position against time. Reaction fronts, salt depletion and
  relaxation show up as shapes.
* *Voltage losses*: OCV − V split into particle diffusion, reaction,
  electrolyte and ohmic losses.
* *Electrode windows*: the stretch of each electrode's OCP curve a test
  used.
* *Heat & plating*: heat sources, and the anode potential vs Li at the
  separator (below 0 V, plating is possible). Below them, the cathode and
  anode potentials against a lithium reference in the separator, with each
  electrode's OCP, show how the terminal voltage is cathode minus anode.
* *Physics knobs*: each test can scale ten material and design properties
  in the sidebar: solid diffusivities, reaction rates, electrolyte
  conductivity and diffusivity, particle radii, electrode thicknesses. A
  sensitivity ranking halves and doubles each one to show which limits the
  test.
* *Ageing*: cycles the LG M50 cell (O'Kane 2022) with SEI growth, lithium
  plating and particle cracking/LAM switched on. You can compare two
  scenarios. It shows capacity fade, the lithium lost to each side
  reaction, LAM per electrode, SEI/plating/crack growth over time (with a
  √t axis), and discharge curves through life. An optional acceleration
  factor scales the side-reaction rates so trends show in fewer cycles.
* *Measured datasets* mode: the downloaded NASA/Oxford/… cells, with fade,
  V–Q curves and raw signals.

**Algorithms** is a test bench for ECMs and state estimators. A PyBaMM
model plays the real cell (the plant) under a drive or pulse profile.
Sensor bias, noise and a wrong initial SoC are applied, and the plant's
ageing is hidden from the estimator. Your estimators get only what a BMS
would know, and are scored against the true SoC: RMSE, max error, and time
to settle within 2 %. There is also a robustness grid (bias, initial error,
noise, aged, cold) and an ECM check (model voltage vs plant, with
residuals).

**Progress** lists the "done when" criteria for every module, with notes,
saved to `dashboard/progress.json`.

Your code plugs in from `workbench/`: `@estimator` and `@ecm` for the
Algorithms bench, and `@panel` for anything else (shown in either view's
"Your analysis" tab). See [workbench/README.md](workbench/README.md). The
module analyses themselves (ICA/DVA, DRT, ECM fitting, EKF/UKF, …) are left
for you to write; the dashboard provides the data, the truth and the
scoring.

## Canonical format

`cell.timeseries` always starts with these columns:

| column | unit | notes |
|---|---|---|
| `test_time_s` | s | monotonic over the whole test |
| `cycle_index` | – | integer |
| `step_index` | – | the source's step number, or -1 if it has none |
| `current_a` | A | **positive = charge**, negative = discharge |
| `voltage_v` | V | |
| `temperature_c` | °C | NaN if not measured |

Ah and Wh in the tables come from **trapezoidal integration of I and I·V**,
never from the cycler's cumulative counters, because those reset
differently on every cycler. Integration never spans a step or cycle
boundary, or a recording gap longer than `max_gap_s` (600 s by default).

Steps are runs of a constant `(cycle, step_index)`. When the source has no
step numbers they are runs of a constant current sign. Each step is classed
`charge`, `discharge`, `rest` or `dynamic`; a step is `dynamic` when at
least 5 % of its samples charge and at least 5 % discharge, as in a drive
cycle.

## Sources

| `source` | Dataset | File(s) |
|---|---|---|
| `batteryarchive` | [batteryarchive.org](https://www.batteryarchive.org): Sandia (SNL), Severson/Attia (MATR), CALCE, Oxford, HNEI… | `*_timeseries.csv` |
| `arbin` | Any Arbin export, including native **CALCE** files | `.csv`/`.xlsx`/`.xls`, or a folder of them for one cell |
| `nasa_pcoe` | NASA PCoE battery aging (B0005…) | `.mat` per cell; EIS goes into `extras` |
| `oxford` | Oxford Battery Degradation Dataset 1 | the single `.mat`, choose `cell="Cell1"`… |
| `mcmaster_hg2` | McMaster LG HG2 (Kollmeyer 2020, Mendeley Data) | `.mat` (`meas` struct) or Digatron `.csv` |
| `biologic` | BioLogic EC-Lab via `galvani` | `.mpr` |
| `pybamm` | PyBaMM simulation with prescribed LLI / LAM | none |

For BEEP's structured features, feed it the raw data directly;
`batterylab` doesn't wrap BEEP.

Put downloaded data under `data/`, which git ignores. Each dataset has its
own licence, so check it before you redistribute anything.

### Getting the data (checked 2026-10-24)

| Dataset | Where | Expected path |
|---|---|---|
| NASA PCoE | [phm-datasets S3 zip](https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip) (210 MB). B0005 is inside `1. BatteryAgingARC-FY08Q4.zip`. | `data/nasa/B0005.mat` |
| Oxford Dataset 1 | [ORA record](https://ora.ox.ac.uk/objects/uuid:03ba4b01-cfed-46d3-9b1a-7d4a7bdf6fac) (262 MB, ODbL) | `data/oxford/Oxford_Battery_Degradation_Dataset_1.mat` |
| Sandia, MATR, HNEI, CALCE… | batteryarchive.org. **Direct CSV links are no longer served**: the old `/data/...` URLs return 404. The FAQ says to email info@batteryarchive.org for downloads. | `data/batteryarchive/*_timeseries.csv` |

The originals of Severson et al. 2019 are also on data.matr.io, as one
MATLAB v7.3 file per batch (several GB each). `batterylab` has no loader for
that format yet.

### Defects found in the real data

* **Oxford:** the README says `t` is in seconds, but it is a MATLAB
  datenum (days); the loader detects this. In Cell1, `cyc7100` is an exact
  copy of `cyc7000` and is dropped. The pseudo-OCV segments of `cyc3200` and
  `cyc4500` have the next characterisation appended, so they are cut where
  the next segment starts. Every repair is listed in
  `metadata["data_issues"]`.
* **NASA B0005:** cycle 91 has two discharge records with no charge between
  them. A few samples have glitches (voltage up to 8.4 V, current up to
  4.5 A); they are left in place. Integrated capacity runs 3–6 mAh above
  NASA's `Capacity` field, because NASA stops counting at 2.7 V.

## Testing and its limits

* `tests/test_tables.py` checks integration, step segmentation, gap
  handling and schema validation against an analytic profile with exact
  answers.
* `tests/test_loaders.py` writes a small file in each source's layout and
  checks that the loader reproduces the known tables. These fixtures
  **encode the assumed layout of each format**. When a real file disagrees,
  fix the fixture and the loader together.
* `tests/test_real_data.py` runs on the downloaded NASA B0005 and all 8
  Oxford cells, and skips when `data/` is absent. It checks integrated
  capacity against each source's own numbers. `batteryarchive`, `arbin`,
  `mcmaster_hg2` and `biologic` have **not** been checked against real
  files yet.
* `tests/test_synthetic.py` checks that the PyBaMM degradation lands on
  target, and is marked `slow`. Skip it with `pytest -m "not slow"`.
* `biologic` has no test, because generating a valid `.mpr` needs EC-Lab.

## Synthetic degradation conventions

`LLI` is the fraction of lithium in the active particles that is lost.
`LAM_PE` and `LAM_NE` are the fractions of active-material volume lost.
LAM keeps its lithium (delithiated LAM); add LLI to model lithiated LAM.
Fresh and aged electrode/lithium capacities in Ah are stored in
`metadata["ground_truth"]`. See the module docstring of
`batterylab/synthetic.py`.

## Roadmap

- [x] Shared foundation: loaders, tidy tables, synthetic generator, tests
- [ ] A1 ICA/DVA and degradation-mode fitting
- [ ] A2 EIS / DRT
- [ ] A3 DoE and calendar-ageing uncertainty
- [ ] A4 Warranty calculator and decision memo
- [ ] A5 Knee and plating detection
- [ ] B1 ECM parameter ID on LG HG2
- [ ] B2 SoC estimation (CC → EKF → UKF)
- [ ] B3 SoH / SoP / RUL
- [ ] B4 Software-in-the-loop with a C port
- [ ] B5 MCU processor-in-the-loop
- [ ] Capstone: degradation-aware estimation
