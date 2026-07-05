"""Backwards-compatible entry point.

The application now lives in the installable ``campaign_forge`` package. This
thin shim keeps ``python main.py ...`` working. Prefer the installed console
script (``campaign-forge``) or ``python -m campaign_forge``.
"""

from __future__ import annotations

import os
import sys

# Allow running from a source checkout without installing the package first.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from campaign_forge.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
