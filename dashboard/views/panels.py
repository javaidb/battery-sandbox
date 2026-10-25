"""Runs workbench @panel functions inside either view."""

import traceback

import pandas as pd
import streamlit as st

from workbench import discover


def render(cell, prefixes: tuple[str, ...]):
    """Show the workbench panels whose module starts with one of ``prefixes``."""
    panels, errors = discover()
    for mod, tb in errors.items():
        with st.expander(f"⚠️ {mod} failed to import", expanded=True):
            st.code(tb, language="python")
    mine = {p.key: p for p in panels.values() if p.module.startswith(prefixes)}
    if not mine:
        example = "A1" if "A" in prefixes else "B1"
        st.info(f"No panels for this view yet. Add a function decorated with `@panel(..., module=\"{example}\")` in `workbench/` "
                "(see `workbench/example_retention.py`), then press **R** to rerun.")
        return
    titles = {f"{p.module} · {p.title}": p for p in mine.values()}
    p = titles[st.selectbox("Panel", list(titles), key=f"panel_pick_{prefixes}")]
    if p.description:
        st.markdown(p.description)

    kwargs = {}
    if p.params:
        cols = st.columns(min(4, len(p.params)))
        for i, (name, spec) in enumerate(p.params.items()):
            box, key = cols[i % len(cols)], f"{p.key}.{name}"
            if isinstance(spec, bool):
                kwargs[name] = box.checkbox(name, spec, key=key)
            elif isinstance(spec, list):
                kwargs[name] = box.selectbox(name, spec, key=key)
            elif isinstance(spec, tuple) and len(spec) == 3:
                kwargs[name] = box.slider(name, spec[0], spec[1], spec[2], key=key)
            else:
                st.error(f"Unsupported param spec for {name!r}: {spec!r}")
                return
    try:
        out = p.fn(cell, **kwargs)
    except Exception:
        st.error("The panel raised an exception:")
        st.code(traceback.format_exc(), language="python")
        return
    for item in out if isinstance(out, (list, tuple)) else [out]:
        if item is None:
            continue
        if isinstance(item, str):
            st.markdown(item)
        elif isinstance(item, pd.DataFrame):
            st.dataframe(item, use_container_width=True)
        elif isinstance(item, dict):
            st.json(item)
        elif type(item).__module__.startswith("plotly"):
            st.plotly_chart(item, use_container_width=True)
        elif type(item).__module__.startswith("matplotlib"):
            st.pyplot(item)
        else:
            st.write(item)
