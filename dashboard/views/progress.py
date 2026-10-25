"""Tracks progress through the modules against their "done when" criteria."""

import json
from datetime import date

import streamlit as st

from common import ROOT
from modules import MODULES

STORE = ROOT / "dashboard" / "progress.json"


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    STORE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def page():
    state = _load()
    st.title("Progress")
    st.caption("Tick a criterion only when you can show the evidence. Saved to `dashboard/progress.json`, so it is versioned with your work.")

    from workbench import ECMS, ESTIMATORS, discover

    panels, _ = discover()
    counts: dict[str, int] = {}
    for p in panels.values():
        counts[p.module] = counts.get(p.module, 0) + 1
    st.caption(f"Registered in workbench/: {len(panels)} panel(s), {len(ESTIMATORS)} estimator(s), {len(ECMS)} ECM(s).")

    total = sum(len(m["done"]) for m in MODULES)
    saved = lambda m, i: bool((state.get(m["id"], {}).get("done", []) + [False] * len(m["done"]))[i])  # noqa: E731
    ticked = sum(st.session_state.get(f"{m['id']}_{i}", saved(m, i)) for m in MODULES for i in range(len(m["done"])))
    st.progress(ticked / total, text=f"{ticked} of {total} criteria met")

    changed = False
    for route in dict.fromkeys(m["route"] for m in MODULES):
        st.subheader(route)
        for m in (m for m in MODULES if m["route"] == route):
            rec = state.setdefault(m["id"], {"done": [False] * len(m["done"]), "notes": ""})
            rec["done"] = (rec.get("done", []) + [False] * len(m["done"]))[: len(m["done"])]
            n_done = sum(st.session_state.get(f"{m['id']}_{i}", v) for i, v in enumerate(rec["done"]))
            badge = "✅" if n_done == len(m["done"]) else ("🟡" if n_done else "⚪")
            extra = f" · {counts[m['id']]} panel(s)" if m["id"] in counts else ""
            with st.expander(f"{badge} {m['id']} · {m['title']}  ({n_done}/{len(m['done'])}{extra})"):
                for i, crit in enumerate(m["done"]):
                    v = st.checkbox(crit, rec["done"][i], key=f"{m['id']}_{i}")
                    if v != rec["done"][i]:
                        rec["done"][i] = v
                        rec["updated"] = date.today().isoformat()
                        changed = True
                notes = st.text_area("Notes, evidence, links", rec.get("notes", ""), key=f"{m['id']}_notes", height=90)
                if notes != rec.get("notes", ""):
                    rec["notes"] = notes
                    rec["updated"] = date.today().isoformat()
                    changed = True
                if rec.get("updated"):
                    st.caption(f"Last updated {rec['updated']}")
    if changed:
        _save(state)
        st.toast("Progress saved")
