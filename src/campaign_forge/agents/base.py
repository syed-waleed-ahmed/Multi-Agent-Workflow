"""The :class:`BaseAgent` abstraction shared by every specialised agent.

Each agent is a small, single-responsibility unit that owns one system prompt
and one transformation step of the pipeline. The base class centralises LLM
access and logging so the concrete agents stay focused on prompt construction
and output parsing. Subclasses declare their identity via the ``name`` and
``system_prompt`` class attributes, which ``__init_subclass__`` enforces.
"""

from __future__ import annotations

from abc import ABC
from typing import ClassVar

from ..llm import LLMClient
from ..logging_config import get_logger


class BaseAgent(ABC):
    """Abstract base for pipeline agents. Not instantiated directly."""

    #: Short machine-friendly identifier, e.g. ``"research"``.
    name: ClassVar[str]
    #: The system-role instruction that defines the agent's persona.
    system_prompt: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        missing = [attr for attr in ("name", "system_prompt") if not getattr(cls, attr, None)]
        if missing:
            raise TypeError(f"{cls.__name__} must define class attribute(s): {', '.join(missing)}")

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._log = get_logger(f"agents.{self.name}")

    def _generate(self, user_prompt: str, *, json_mode: bool = False) -> str:
        """Call the model with this agent's system prompt and return the text."""
        self._log.debug("Generating (json_mode=%s)", json_mode)
        result = self._llm.complete(self.system_prompt, user_prompt, json_mode=json_mode)
        self._log.debug("Received %d characters", len(result))
        return result
