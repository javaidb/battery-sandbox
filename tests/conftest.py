"""A synthetic cycling profile with analytically known answers.

Each cycle: charge +1 A for 1 h (V 3.5 -> 4.1), rest 10 min, discharge -2 A
for 30 min (V 4.0 -> 3.0), rest 10 min. Consecutive steps share their
boundary timestamp, so trapezoidal integration is exact:

* charge 1 Ah / 3.8 Wh, discharge 1 Ah / 3.5 Wh per cycle.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

CHARGE_AH, DISCHARGE_AH = 1.0, 1.0
CHARGE_WH, DISCHARGE_WH = 3.8, 3.5

# (duration s, current A, V start, V end)
STEPS = [(3600, 1.0, 3.5, 4.1), (600, 0.0, 4.1, 4.0), (1800, -2.0, 4.0, 3.0), (600, 0.0, 3.0, 3.2)]
STEP_TYPES = ["charge", "rest", "discharge", "rest"]
CYCLE_S = sum(s[0] for s in STEPS)


def make_profile(n_cycles: int = 2, dt: float = 10.0) -> pd.DataFrame:
    rows, t0 = [], 0.0
    for cycle in range(1, n_cycles + 1):
        for step, (dur, cur, v0, v1) in enumerate(STEPS, start=1):
            t = np.arange(0, dur + dt / 2, dt)
            rows.append(
                pd.DataFrame(
                    {
                        "test_time_s": t0 + t,
                        "cycle_index": cycle,
                        "step_index": step,
                        "current_a": cur,
                        "voltage_v": v0 + (v1 - v0) * t / dur,
                        "temperature_c": 25.0,
                    }
                )
            )
            t0 += dur
    return pd.concat(rows, ignore_index=True)


@pytest.fixture
def profile() -> pd.DataFrame:
    return make_profile()
