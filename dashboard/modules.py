"""The playground's modules and their "done when" criteria, for the Progress page."""

MODULES = [
    {
        "id": "F", "route": "Foundation", "title": "Battery data lab",
        "done": [
            "One function returns tidy per-cycle and per-step tables for any dataset",
            "Tests and README",
        ],
    },
    {
        "id": "A1", "route": "A · Degradation", "title": "ICA/DVA and degradation-mode quantification",
        "done": [
            "dQ/dV and dV/dQ from slow-rate data with Savitzky–Golay or Gaussian smoothing",
            "Peak shifts tracked over ageing",
            "Half-cell OCV fit to full-cell pseudo-OCV gives LLI, LAM_PE and LAM_NE",
            "Fit recovers prescribed LLI/LAM on PyBaMM synthetic data",
            "Capacity fade of one Oxford or Sandia cell explained as a mechanism breakdown with error bars",
        ],
    },
    {
        "id": "A2", "route": "A · Degradation", "title": "Impedance spectroscopy",
        "done": [
            "ECM fits with Warburg/CPE elements (impedance.py)",
            "DRT computed (pyDRTtools)",
            "Specific DRT peak changes linked to SEI, charge transfer or diffusion",
            "The ambiguous link stated explicitly",
        ],
    },
    {
        "id": "A3", "route": "A · Degradation", "title": "DoE, accelerated life testing and uncertainty",
        "done": [
            "Calendar-ageing matrix designed over temperature × SoC",
            "Arrhenius × √t model fitted with bootstrap or Bayesian (PyMC) uncertainty",
            "Simplified Attia et al. 2020 closed-loop optimisation reproduced",
            "Test-plan proposal with sample-size justification and confidence level",
        ],
    },
    {
        "id": "A4", "route": "A · Degradation", "title": "Cell-to-product translation",
        "done": [
            "Warranty calculator: ageing model + usage profile → energy, cold power, fast-charge time at years 1/4/8",
            "One-page decision memo (e.g. NMC vs LFP for Product X) using Sandia data",
        ],
    },
    {
        "id": "A5", "route": "A · Degradation", "title": "Failure analysis",
        "done": [
            "Knee points detected in Severson/Sandia curves",
            "Lithium plating detected from post-charge voltage relaxation",
            "Plating detector checked against PyBaMM plating-submodel examples",
            "Post-mortem methods (SEM, XPS, coin-cell harvesting) read up",
        ],
    },
    {
        "id": "B1", "route": "B · Algorithms", "title": "ECM and parameter identification",
        "done": [
            "1RC and 2RC models with OCV–SoC and hysteresis fitted to LG HG2 HPPC data",
            "Parameter tables over SoC × temperature",
            "Fitted in Python (scipy least squares), and in Simulink if available",
            "Held-out drive-cycle voltage RMSE reported per temperature",
            "Why 0 °C performs worst explained",
        ],
    },
    {
        "id": "B2", "route": "B · Algorithms", "title": "SoC estimation",
        "done": [
            "Coulomb counting → EKF → UKF/sigma-point filter",
            "Stress tests: current-sensor bias, wrong initial SoC, aged cell, cold",
            "RMSE, max error and convergence time reported",
            "Robustness table (not a single nice plot)",
        ],
    },
    {
        "id": "B3", "route": "B · Algorithms", "title": "SoH, SoP and RUL",
        "done": [
            "R0 and capacity tracked online (RLS with forgetting, or dual EKF)",
            "SoP over 2/10/30 s windows from V, I, T limits",
            "Severson ΔQ(V) early-prediction features reproduced",
            "GP vs particle filter compared for RUL on NASA data",
            "Capacity tracked within a stated error band; SoP never violates the voltage limit in simulation",
        ],
    },
    {
        "id": "B4", "route": "B · Algorithms", "title": "Software-in-the-loop",
        "done": [
            "PyBaMM DFN as plant, ECM estimator as controller",
            "Estimator ported to C (float32) and called from a Python harness",
            "Unit tests on the C port",
            "C and Python match within tolerance across a regression suite",
        ],
    },
    {
        "id": "B5", "route": "B · Algorithms", "title": "HIL-lite / processor-in-the-loop",
        "done": [
            "C estimator runs on an STM32 Nucleo or ESP32",
            "Recorded cell data streamed from the PC over serial",
            "Execution time and memory measured; runs in real time within a stated CPU budget",
        ],
    },
    {
        "id": "C", "route": "Capstone", "title": "Degradation-aware estimation",
        "done": [
            "Degradation modes diagnosed for one ageing dataset (A1/A2)",
            "SoH estimator updates OCV curves as LAM shifts",
            "Improved SoC and SoP accuracy on aged cells shown (B2/B3)",
        ],
    },
]
