"""Token-usage accounting shared by providers, budgets and artifacts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TokenUsage(BaseModel):
    """Token counts for one or more LLM calls.

    ``complete`` is ``False`` when at least one contributing call did not report
    usage. The totals are then a lower bound, and the UI says so, instead of
    presenting a guess as a measurement.
    """

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    complete: bool = True

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @classmethod
    def unreported(cls) -> TokenUsage:
        """Usage for a call whose provider returned no token counts."""
        return cls(complete=False)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            complete=self.complete and other.complete,
        )
