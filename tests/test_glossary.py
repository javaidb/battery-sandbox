"""Every metric the Electrochemistry view can plot has an explanation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

from batterylab.ageing import STATES  # noqa: E402
from batterylab.echem import FIELDS, KNOBS, SERIES_GROUPS  # noqa: E402
from glossary import GLOSSARY, LOSS_KEYS  # noqa: E402


def test_every_plotted_metric_is_explained():
    labels = set(FIELDS) | {"Anode potential at separator vs Li [V]", "particles", "windows", "sensitivity"}
    labels |= {f"knob:{k}" for k in KNOBS} | {f"ageing:{s}" for s in STATES}
    labels |= {"ageing:capacity", "ageing:breakdown", "ageing:lam", "ageing:curves", "ageing:acceleration"}
    for group, members in SERIES_GROUPS.items():
        for lab in members:
            labels.add(LOSS_KEYS.get(lab, lab) if group == "Voltage breakdown" else lab)
    missing = sorted(labels - set(GLOSSARY))
    assert missing == []


def test_entries_are_complete():
    for key, e in GLOSSARY.items():
        assert {"title", "what", "moves", "look"} <= set(e), key
        assert all(e[k].strip() for k in ("title", "what", "moves", "look")), key
