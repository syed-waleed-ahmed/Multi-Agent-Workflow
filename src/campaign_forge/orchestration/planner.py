"""Planners: choose a workflow template and its optional steps for a brief.

Planning is constrained (ADR-002). A plan can only:

* pick a registered template;
* disable some of that template's *optional* steps.

It can never invent steps, agents or loops.

* :class:`RuleBasedPlanner` is deterministic and works offline. It is the default.
* :class:`LLMPlanner` asks a model for a structured proposal, validates it against
  the catalog, and falls back to the rule-based plan on any problem. The fallback
  is always recorded in the plan's rationale.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from ..exceptions import CampaignForgeError, ConfigurationError
from ..logging_config import get_logger
from ..models import CampaignBrief
from ..providers.base import CompletionRequest
from ..providers.router import ProviderRouter
from ..providers.structured import run_structured
from ..security.untrusted import frame_untrusted, with_untrusted_policy
from .definition import WorkflowDefinition
from .templates import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE, TemplateInfo

PLANNER_AGENT_ID = "planner"


@dataclass(frozen=True)
class ExecutionPlan:
    """A validated choice of workflow, with a human-readable rationale."""

    definition: WorkflowDefinition
    rationale: tuple[str, ...] = field(default_factory=tuple)
    planner: str = "rules"


class PlanProposal(BaseModel):
    """What the LLM planner may propose. Anything else is ignored."""

    model_config = ConfigDict(extra="ignore")

    template: str = Field(min_length=1, max_length=60)
    disabled_steps: list[str] = Field(default_factory=list, max_length=50)
    rationale: str = Field(default="", max_length=1000)


def _apply(
    info: TemplateInfo, disabled: list[str], rationale: list[str], planner: str
) -> ExecutionPlan:
    definition = info.build()
    optional = {s.id for s in definition.steps if s.optional}
    invalid = sorted(set(disabled) - optional)
    if invalid:
        raise ConfigurationError(
            f"Only optional steps can be disabled; refused: {', '.join(invalid)}."
        )
    if disabled:
        definition = definition.with_disabled(disabled)
        rationale.append("disabled optional steps: " + ", ".join(sorted(disabled)))
    return ExecutionPlan(definition=definition, rationale=tuple(rationale), planner=planner)


class RuleBasedPlanner:
    """Deterministic planning: an explicit template, else the default."""

    def __init__(self, templates: Mapping[str, TemplateInfo] = BUILTIN_TEMPLATES) -> None:
        self._templates = templates

    def plan(
        self,
        brief: CampaignBrief,
        *,
        template: str | None = None,
        disabled_steps: list[str] | None = None,
    ) -> ExecutionPlan:
        name = template or DEFAULT_TEMPLATE
        info = self._templates.get(name)
        if info is None:
            known = ", ".join(sorted(self._templates))
            raise ConfigurationError(f"Unknown workflow {name!r}. Known: {known}.")
        reason = "template requested explicitly" if template else "default template"
        return _apply(info, list(disabled_steps or []), [f"{info.id}: {reason}"], "rules")


class LLMPlanner:
    """Model-assisted planning that can only choose among validated options."""

    def __init__(
        self,
        router: ProviderRouter,
        templates: Mapping[str, TemplateInfo] = BUILTIN_TEMPLATES,
        *,
        fallback: RuleBasedPlanner | None = None,
    ) -> None:
        self._router = router
        self._templates = templates
        self._fallback = fallback or RuleBasedPlanner(templates)
        self._log = get_logger("planner")

    def plan(self, brief: CampaignBrief) -> ExecutionPlan:
        try:
            proposal = self._propose(brief)
            info = self._templates.get(proposal.template)
            if info is None:
                raise ConfigurationError(f"proposed unknown template {proposal.template!r}")
            rationale = [f"{info.id}: chosen by planner model"]
            if proposal.rationale:
                rationale.append(proposal.rationale)
            return _apply(info, proposal.disabled_steps, rationale, "llm")
        except CampaignForgeError as exc:
            self._log.warning("LLM planner failed (%s); using rule-based plan.", exc)
            plan = self._fallback.plan(brief)
            return ExecutionPlan(
                definition=plan.definition,
                rationale=(*plan.rationale, f"LLM planner unavailable: {exc}"),
                planner="rules (fallback)",
            )

    def _propose(self, brief: CampaignBrief) -> PlanProposal:
        catalog = [
            {
                "template": info.id,
                "description": info.description,
                "optional_steps": [s.id for s in info.build().steps if s.optional],
            }
            for info in self._templates.values()
        ]
        system = with_untrusted_policy(
            "You are a marketing workflow planner. Choose exactly one template from the "
            "catalog for the campaign brief, and optionally list optional steps to disable. "
            'Respond with a JSON object: {"template": "...", "disabled_steps": [...], '
            '"rationale": "one sentence"}.'
        )
        user = (
            "Catalog:\n"
            f"{json.dumps(catalog, indent=2)}\n\n"
            "Campaign brief:\n"
            f"{frame_untrusted(brief.as_prompt_context(), label='campaign brief')}"
        )
        request = CompletionRequest.from_prompts(
            system, user, max_tokens=300, metadata={"agent": PLANNER_AGENT_ID}
        )
        result = run_structured(
            lambda r: self._router.complete(PLANNER_AGENT_ID, r), request, PlanProposal
        )
        return result.value
