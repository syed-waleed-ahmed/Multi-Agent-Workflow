"""The HTTP module the installed ``openai`` SDK is built on.

The 2.x SDK uses ``httpx``; 3.x moved to ``httpx2``. Tests that build SDK errors
by hand import the module from here, so they work on either major version
(ADR-025).
"""

from __future__ import annotations

import importlib
from types import ModuleType

import openai

SDK_MAJOR = int(openai.__version__.split(".", 1)[0])
http: ModuleType = importlib.import_module("httpx2" if SDK_MAJOR >= 3 else "httpx")
