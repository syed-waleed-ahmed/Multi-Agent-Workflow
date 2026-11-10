"""The :class:`BaseAgent` abstraction shared by every v1 pipeline agent.

Each agent is a small, single-responsibility unit that owns one system prompt
and one transformation step of the pipeline. The base class centralises LLM
access, logging and prompt hygiene, so the concrete agents stay focused on prompt
construction and output parsing. Subclasses declare their identity through the
``name`` and ``system_prompt`` class attributes, which ``__init_subclass__``
enforces.

Prompt hygiene (2.0 security primitives):

* every system prompt carries the untrusted-data policy;
* user-supplied and model-generated content is wrapped with :meth:`_data`,
  never pasted in as if it were instructions.
"""

from __future__ import annotations

from abc import ABC
from typing import ClassVar

from ..llm import LLMClient
from ..logging_config import get_logger
from ..providers.compat import ChatClient
from ..security.untrusted import frame_untrusted, with_untrusted_policy


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

    def __init__(self, llm: ChatClient) -> None:
        # A real client applies this agent's CF_AGENT_OVERRIDES (model, temperature,
        # max_tokens). Duck-typed fakes in tests are used as they are.
        self._llm = llm.for_agent(self.name) if isinstance(llm, LLMClient) else llm
        self._log = get_logger(f"agents.{self.name}")

    def _generate(self, user_prompt: str, *, json_mode: bool = False) -> str:
        """Call the model with this agent's (policy-hardened) system prompt."""
        self._log.debug("Generating (json_mode=%s)", json_mode)
        result = self._llm.complete(
            with_untrusted_policy(self.system_prompt), user_prompt, json_mode=json_mode
        )
        self._log.debug("Received %d characters", len(result))
        return result

    @staticmethod
    def _data(content: str, label: str) -> str:
        """Frame user-supplied or model-generated content as untrusted data."""
        return frame_untrusted(content, label=label)
