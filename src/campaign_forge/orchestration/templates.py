"""Built-in workflow templates and the default agent registry.

Templates:

* ``classic-campaign``: exact parity with the v1 four-step pipeline (it backs the
  v1 ``CampaignWorkflow`` API).
* ``full-campaign``: the wave-1 agent library end to end, with critic review.
* ``ad-pack``, ``social-calendar``, ``email-sequence``: focused deliverables.

See docs/AGENT_CATALOG.md, section 5.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..agents.legacy import register_legacy_agents
from ..agents.library import register_library_agents
from ..agents.schemas import BRIEF_KIND
from ..core.budget import BudgetLimits
from ..core.registry import AgentRegistry
from ..exceptions import ConfigurationError
from .definition import Condition, ReviewSpec, StepDefinition, WorkflowDefinition


def default_registry(*, load_plugins: bool = False) -> AgentRegistry:
    """A registry with the built-in agents (and, optionally, installed plugin agents)."""
    registry = AgentRegistry()
    register_legacy_agents(registry)
    register_library_agents(registry)
    if load_plugins:
        registry.load_entry_points(strict=False)
    return registry


def classic_campaign() -> WorkflowDefinition:
    """The v1 pipeline: research -> copy -> art direction -> final brief."""
    brief = "input:brief"
    return WorkflowDefinition(
        id="classic-campaign",
        name="Classic campaign (v1 pipeline)",
        version="1.0.0",
        description=(
            "The original four-agent pipeline: research, copywriting, art direction and "
            "a final Markdown campaign brief."
        ),
        inputs={"brief": BRIEF_KIND},
        steps=(
            StepDefinition(id="research", agent="legacy.research", inputs={"brief": brief}),
            StepDefinition(
                id="copy",
                agent="legacy.copywriter",
                inputs={"brief": brief, "research": "step:research"},
            ),
            StepDefinition(
                id="visuals",
                agent="legacy.art_director",
                inputs={"brief": brief, "research": "step:research", "copy": "step:copy"},
            ),
            StepDefinition(
                id="report",
                agent="legacy.manager",
                inputs={
                    "brief": brief,
                    "research": "step:research",
                    "copy": "step:copy",
                    "prompts": "step:visuals",
                },
            ),
        ),
        outputs={
            "research": "step:research",
            "copy": "step:copy",
            "image_prompts": "step:visuals",
            "final_brief": "step:report",
        },
        # v1 had no wall-clock limit and retries can wait out rate limits, so this
        # template is generous on time and tight on calls (4 steps, at most 7 calls).
        limits=BudgetLimits(max_llm_calls=20, max_total_tokens=100_000, max_wall_seconds=1800),
        max_parallel=1,
    )


# --------------------------------------------------------------------------- #
# Wave-1 agent library templates
# --------------------------------------------------------------------------- #
_BRIEF = "input:brief"
_REVIEW = ReviewSpec(critic="critic", threshold=0.7, max_revisions=1)


def _foundation() -> tuple[StepDefinition, ...]:
    """Audience research, positioning, reviewed core copy (shared by focused templates)."""
    return (
        StepDefinition(id="audience", agent="audience_research", inputs={"brief": _BRIEF}),
        StepDefinition(
            id="positioning",
            agent="positioning",
            inputs={"brief": _BRIEF, "audience": "step:audience"},
        ),
        StepDefinition(
            id="copy",
            agent="copywriter",
            inputs={
                "brief": _BRIEF,
                "positioning": "step:positioning",
                "audience": "step:audience",
            },
            review=_REVIEW,
        ),
    )


def full_campaign() -> WorkflowDefinition:
    """Research, positioning, plan and copy, channel content, then the final report."""
    content = {"brief": _BRIEF, "copy": "step:copy", "positioning": "step:positioning"}
    return WorkflowDefinition(
        id="full-campaign",
        name="Full campaign (agent library)",
        version="1.0.0",
        description=(
            "Audience and market research, positioning, a channel plan, reviewed core copy and "
            "ads, a social calendar, an email sequence (when email is a channel), visual "
            "concepts, and a final report with every deliverable."
        ),
        inputs={"brief": BRIEF_KIND},
        steps=(
            StepDefinition(id="audience", agent="audience_research", inputs={"brief": _BRIEF}),
            StepDefinition(id="market", agent="market_research", inputs={"brief": _BRIEF}),
            StepDefinition(
                id="positioning",
                agent="positioning",
                inputs={"brief": _BRIEF, "audience": "step:audience", "market": "step:market"},
            ),
            StepDefinition(
                id="strategy",
                agent="marketing_strategist",
                inputs={
                    "brief": _BRIEF,
                    "positioning": "step:positioning",
                    "audience": "step:audience",
                },
            ),
            StepDefinition(
                id="copy",
                agent="copywriter",
                inputs={
                    "brief": _BRIEF,
                    "positioning": "step:positioning",
                    "audience": "step:audience",
                },
                review=_REVIEW,
            ),
            StepDefinition(
                id="ads", agent="ad_copy", inputs=content, review=_REVIEW, optional=True
            ),
            StepDefinition(id="social", agent="social_media", inputs=content, optional=True),
            StepDefinition(
                id="email",
                agent="email_marketing",
                inputs={"brief": _BRIEF, "copy": "step:copy", "audience": "step:audience"},
                when=(Condition(source=_BRIEF, path="channels", op="contains", value="email"),),
                optional=True,
            ),
            StepDefinition(id="visuals", agent="art_director", inputs=content, optional=True),
            StepDefinition(
                id="report",
                agent="campaign_reporter",
                inputs={
                    "brief": _BRIEF,
                    "audience": "step:audience",
                    "market": "step:market",
                    "positioning": "step:positioning",
                    "plan": "step:strategy",
                    "copy": "step:copy",
                    "ads": "step:ads",
                    "social": "step:social",
                    "email": "step:email",
                    "visuals": "step:visuals",
                },
            ),
        ),
        outputs={
            "report": "step:report",
            "plan": "step:strategy",
            "copy": "step:copy",
            "ads": "step:ads",
            "social": "step:social",
            "email": "step:email",
            "visuals": "step:visuals",
        },
        # About 10 agent calls, plus up to 4 critic calls and 2 revisions. Two parallel steps
        # keep bursts within a free-tier tokens-per-minute quota.
        limits=BudgetLimits(max_llm_calls=40, max_total_tokens=150_000, max_wall_seconds=2400),
        max_parallel=2,
    )


def _focused(
    template_id: str, name: str, description: str, step: StepDefinition, output: str
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=template_id,
        name=name,
        version="1.0.0",
        description=description,
        inputs={"brief": BRIEF_KIND},
        steps=(*_foundation(), step),
        outputs={output: f"step:{step.id}", "copy": "step:copy"},
        limits=BudgetLimits(max_llm_calls=20, max_total_tokens=60_000, max_wall_seconds=1200),
        max_parallel=1,
    )


def ad_pack() -> WorkflowDefinition:
    return _focused(
        "ad-pack",
        "Ad pack",
        "Audience, positioning and reviewed core copy, then reviewed ad variants per channel.",
        StepDefinition(
            id="ads",
            agent="ad_copy",
            inputs={"brief": _BRIEF, "copy": "step:copy", "positioning": "step:positioning"},
            review=_REVIEW,
        ),
        "ads",
    )


def social_calendar() -> WorkflowDefinition:
    return _focused(
        "social-calendar",
        "Social calendar",
        "Audience, positioning and reviewed core copy, then a reviewed two-week social calendar.",
        StepDefinition(
            id="social",
            agent="social_media",
            inputs={"brief": _BRIEF, "copy": "step:copy", "positioning": "step:positioning"},
            review=_REVIEW,
        ),
        "social",
    )


def email_sequence() -> WorkflowDefinition:
    return _focused(
        "email-sequence",
        "Email sequence",
        "Audience, positioning and reviewed core copy, then a reviewed email sequence.",
        StepDefinition(
            id="email",
            agent="email_marketing",
            inputs={"brief": _BRIEF, "copy": "step:copy", "audience": "step:audience"},
            review=_REVIEW,
        ),
        "email",
    )


@dataclass(frozen=True)
class TemplateInfo:
    """A catalog entry: what the planner and the UI know about a template."""

    id: str
    name: str
    description: str
    factory: Callable[[], WorkflowDefinition]

    def build(self) -> WorkflowDefinition:
        return self.factory()


def _info(factory: Callable[[], WorkflowDefinition]) -> TemplateInfo:
    definition = factory()
    return TemplateInfo(definition.id, definition.name, definition.description, factory)


BUILTIN_TEMPLATES: Mapping[str, TemplateInfo] = {
    info.id: info
    for info in (
        _info(full_campaign),
        _info(classic_campaign),
        _info(ad_pack),
        _info(social_calendar),
        _info(email_sequence),
    )
}
DEFAULT_TEMPLATE = "full-campaign"


def get_template(template_id: str) -> WorkflowDefinition:
    """Build a built-in template by id."""
    try:
        return BUILTIN_TEMPLATES[template_id].build()
    except KeyError:
        known = ", ".join(sorted(BUILTIN_TEMPLATES))
        raise ConfigurationError(f"Unknown workflow {template_id!r}. Known: {known}.") from None
