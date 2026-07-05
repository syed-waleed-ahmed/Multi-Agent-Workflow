"""Specialised agents that make up the campaign pipeline."""

from __future__ import annotations

from .art_director import ArtDirectorAgent
from .base import BaseAgent
from .copywriter import CopywriterAgent
from .manager import ManagerAgent
from .research import ResearchAgent

__all__ = [
    "ArtDirectorAgent",
    "BaseAgent",
    "CopywriterAgent",
    "ManagerAgent",
    "ResearchAgent",
]
