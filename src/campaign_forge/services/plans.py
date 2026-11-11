"""Plan adjustments a caller may make before approving a run: skip and tighten, never loosen."""

from __future__ import annotations

from collections.abc import Iterable

from ..core.budget import BudgetLimits
from ..exceptions import ConfigurationError
from ..orchestration.definition import WorkflowDefinition


def customize(
    definition: WorkflowDefinition, *, skip: Iterable[str] = (), limits: BudgetLimits | None = None
) -> WorkflowDefinition:
    """The definition with some optional steps disabled and, optionally, new limits.

    Raises:
        ConfigurationError: if a step to skip is not optional.
    """
    skipped = set(skip)
    optional = {s.id for s in definition.steps if s.optional}
    refused = sorted(skipped - optional)
    if refused:
        raise ConfigurationError(f"Only optional steps can be skipped: {', '.join(refused)}.")
    customised = definition.with_disabled(skipped) if skipped else definition
    if limits is not None:
        customised = customised.model_copy(update={"limits": limits})
    return customised


def tighten(
    limits: BudgetLimits,
    *,
    max_llm_calls: int | None = None,
    max_total_tokens: int | None = None,
    max_wall_seconds: float | None = None,
) -> BudgetLimits:
    """``limits`` with any requested value applied only where it is lower (a caller
    may spend less than the workflow allows, never more)."""
    update: dict[str, float] = {}
    if max_llm_calls is not None:
        update["max_llm_calls"] = min(max_llm_calls, limits.max_llm_calls)
    if max_total_tokens is not None:
        update["max_total_tokens"] = min(max_total_tokens, limits.max_total_tokens)
    if max_wall_seconds is not None:
        update["max_wall_seconds"] = min(max_wall_seconds, limits.max_wall_seconds)
    return BudgetLimits.model_validate(limits.model_copy(update=update))
