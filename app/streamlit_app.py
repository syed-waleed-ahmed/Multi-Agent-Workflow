"""Campaign Forge: the Streamlit app (run with ``streamlit run app/streamlit_app.py``).

The app starts in demo mode with no keys: every agent is answered by a simulated
responder and nothing is stored on the server. Set ``CF_UI_MODE=local`` to use
your configured provider and the CLI's run store and memory.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Run from a checkout without installing the package (e.g. on Streamlit Community Cloud).
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st  # noqa: E402

from campaign_forge.ui.pages import navigation, render_sidebar  # noqa: E402

st.set_page_config(page_title="Campaign Forge", layout="wide")
page = navigation()
render_sidebar()
page.run()
