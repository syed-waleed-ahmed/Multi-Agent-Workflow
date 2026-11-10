"""The Streamlit app's Python side (Session 6, ADR-033).

* :mod:`.session` and :mod:`.views` hold the app's state and view models. They
  do not import Streamlit, so they are unit-tested like the rest of the package.
* :mod:`.pages` renders the pages with Streamlit (the ``[ui]`` extra). Only the
  app entry point in ``app/`` imports it.
"""

from __future__ import annotations

from .session import SAMPLE_BRAND, AppSession, build_session, demo_session, local_session

__all__ = ["SAMPLE_BRAND", "AppSession", "build_session", "demo_session", "local_session"]
