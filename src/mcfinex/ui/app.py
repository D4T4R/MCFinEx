"""Streamlit entry point: declares the two pages.

`st.navigation` rather than a `pages/` directory, so the pages carry readable
titles instead of being named after their filenames.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="MCFinEx", page_icon="\U0001F4C8", layout="wide")

st.navigation([
    st.Page("ideas.py", title="Ideas", icon=":material/lightbulb:", default=True),
    st.Page("screen.py", title="Detailed screen", icon=":material/table_rows:"),
]).run()
