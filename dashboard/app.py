"""Battery sandbox dashboard.

Run with:  streamlit run dashboard/app.py
"""

import streamlit as st

st.set_page_config(page_title="Battery sandbox", page_icon="🔋", layout="wide")

import common  # noqa: E402,F401  (puts the repo root on sys.path for workbench/)
from views import algorithms, electrochemistry, progress  # noqa: E402

nav = st.navigation(
    [
        st.Page(electrochemistry.page, title="Electrochemistry", icon="⚗️", url_path="electrochemistry", default=True),
        st.Page(algorithms.page, title="Algorithms", icon="🧮", url_path="algorithms"),
        st.Page(progress.page, title="Progress", icon="✅", url_path="progress"),
    ]
)
nav.run()
