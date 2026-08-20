"""Streamlit entry point: declares the two pages.

`st.navigation` rather than a `pages/` directory, so the pages carry readable
titles instead of being named after their filenames.
"""

from __future__ import annotations

import os

import streamlit as st


def _bridge_secrets() -> None:
    """Expose Streamlit secrets as environment variables.

    The settings layer reads the environment and imports no Streamlit, so it
    stays usable from the CLI. This is the one place the two meet. Existing
    environment variables win, so a local .env still overrides.
    """
    try:
        secrets = st.secrets
    except Exception:
        return
    for key in ("MCFINEX_PG", "MCFINEX_DB", "MCFINEX_REQUEST_DELAY"):
        try:
            value = secrets[key]
        except Exception:
            continue
        if value and not os.environ.get(key):
            os.environ[key] = str(value)


_bridge_secrets()

st.set_page_config(page_title="MCFinEx", page_icon="\U0001F4C8", layout="wide")

st.navigation([
    st.Page("ideas.py", title="Ideas", icon=":material/lightbulb:", default=True),
    st.Page("screen.py", title="Detailed screen", icon=":material/table_rows:"),
]).run()
