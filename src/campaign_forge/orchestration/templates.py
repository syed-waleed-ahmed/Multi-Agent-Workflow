"""Built-in workflow templates and the default agent registry.

Templates:

* ``classic-campaign``: exact parity with the v1 four-step pipeline (it backs the
  v1 ``CampaignWorkflow`` API).
* ``full-campaign``: the wave-1 agent library end to end, with critic review.
* ``ad-pack``, ``social-calendar``, ``email-sequence``: focused deliverables.
* ``competitor-report``: evidence-backed competitor research, fact-checked.
* ``brand-kit``: a brand profile, on-brand core copy, and a brand check.
* Wave 2 (Session 8): ``seo-content-pack``, ``performance-review``,
  ``product-launch``, ``landing-page``, ``content-repurpose``,
  ``ecommerce-campaign`` and ``b2b-campaign``. Every content template ends with a
  safety review, which flags risky claims for a human.

Every content template accepts an optional ``brand`` input (``brand.profile@1``,
e.g. ``execute --brand <name>``): the copywriter writes in its voice, banned terms
become contract violations, and ``full-campaign`` adds a brand check.

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
from ..domain.brand import BRAND_KIND
from ..domain.sources import SOURCES_KIND
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
_SOURCES = "input:sources"
_BRAND = "input:brand"
_OPTIONAL_SOURCES = {"sources": SOURCES_KIND}
_OPTIONAL_INPUTS = {"sources": SOURCES_KIND, "brand": BRAND_KIND}
_REVIEW = ReviewSpec(critic="critic", threshold=0.7, max_revisions=1)


def _foundation() -> tuple[StepDefinition, ...]:
    """Audience research, positioning, reviewed core copy (shared by focused templates)."""
    return (
        StepDefinition(
            id="audience",
            agent="audience_research",
            inputs={"brief": _BRIEF, "sources": _SOURCES},
        ),
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
                "brand": _BRAND,
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
            "concepts, a brand check (when a brand is given), and a final report with every "
            "deliverable."
        ),
        inputs={"brief": BRIEF_KIND},
        optional_inputs=_OPTIONAL_INPUTS,
        steps=(
            StepDefinition(
                id="audience",
                agent="audience_research",
                inputs={"brief": _BRIEF, "sources": _SOURCES},
            ),
            StepDefinition(
                id="market", agent="market_research", inputs={"brief": _BRIEF, "sources": _SOURCES}
            ),
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
                    "brand": _BRAND,
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
                id="brand_check",
                agent="brand_consistency",
                inputs={
                    "brand": _BRAND,
                    "copy": "step:copy",
                    "ads": "step:ads",
                    "social": "step:social",
                    "email": "step:email",
                },
                when=(Condition(source=_BRAND, op="exists"),),
                optional=True,
            ),
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
                    "brand": _BRAND,
                    "brand_check": "step:brand_check",
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
            "brand_check": "step:brand_check",
        },
        # About 10 agent calls, plus up to 4 critic calls and 2 revisions. Two parallel steps
        # keep bursts within a free-tier tokens-per-minute quota.
        limits=BudgetLimits(max_llm_calls=40, max_total_tokens=150_000, max_wall_seconds=3600),
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
        optional_inputs=_OPTIONAL_INPUTS,
        steps=(*_foundation(), step),
        outputs={output: f"step:{step.id}", "copy": "step:copy"},
        limits=BudgetLimits(max_llm_calls=20, max_total_tokens=60_000, max_wall_seconds=1800),
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


def competitor_report() -> WorkflowDefinition:
    """Evidence-backed competitor and market research, fact-checked claim by claim."""
    research = {"brief": _BRIEF, "sources": _SOURCES}
    return WorkflowDefinition(
        id="competitor-report",
        name="Competitor report",
        version="1.0.0",
        description=(
            "Competitor profiles (named competitors only) and market research from your sources "
            "and search tools, with every cited claim fact-checked against its evidence."
        ),
        inputs={"brief": BRIEF_KIND},
        optional_inputs=_OPTIONAL_SOURCES,
        steps=(
            StepDefinition(id="competitors", agent="competitor_research", inputs=research),
            StepDefinition(id="market", agent="market_research", inputs=research),
            StepDefinition(
                id="fact_check",
                agent="fact_checker",
                inputs={"competitors": "step:competitors", "market": "step:market"},
            ),
        ),
        outputs={
            "competitors": "step:competitors",
            "market": "step:market",
            "fact_check": "step:fact_check",
        },
        limits=BudgetLimits(max_llm_calls=20, max_total_tokens=60_000, max_wall_seconds=1800),
        max_parallel=2,
    )


def brand_kit() -> WorkflowDefinition:
    """A brand profile from the brief, on-brand core copy, and a brand check of that copy."""
    return WorkflowDefinition(
        id="brand-kit",
        name="Brand kit",
        version="1.0.0",
        description=(
            "Audience research, a brand profile (voice, tone, preferred and banned terms, visual "
            "style), positioning, reviewed on-brand core copy, and a brand check of that copy. "
            "Keep the profile with 'memory brand save --run <run id>'."
        ),
        inputs={"brief": BRIEF_KIND},
        optional_inputs=_OPTIONAL_SOURCES,
        steps=(
            StepDefinition(
                id="audience",
                agent="audience_research",
                inputs={"brief": _BRIEF, "sources": _SOURCES},
            ),
            StepDefinition(
                id="brand",
                agent="brand_strategist",
                inputs={"brief": _BRIEF, "audience": "step:audience"},
            ),
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
                    "brand": "step:brand",
                },
                review=_REVIEW,
            ),
            StepDefinition(
                id="brand_check",
                agent="brand_consistency",
                inputs={"brand": "step:brand", "copy": "step:copy"},
            ),
        ),
        outputs={"brand": "step:brand", "copy": "step:copy", "brand_check": "step:brand_check"},
        limits=BudgetLimits(max_llm_calls=20, max_total_tokens=60_000, max_wall_seconds=1800),
        max_parallel=1,
    )


# --------------------------------------------------------------------------- #
# Wave-2 templates (Session 8)
# --------------------------------------------------------------------------- #
def _copy_steps(*, market: bool = False) -> tuple[StepDefinition, ...]:
    """Research, positioning and reviewed, brand-aware core copy."""
    research = {"brief": _BRIEF, "sources": _SOURCES}
    steps = [StepDefinition(id="audience", agent="audience_research", inputs=research)]
    positioning_inputs = {"brief": _BRIEF, "audience": "step:audience"}
    if market:
        steps.append(StepDefinition(id="market", agent="market_research", inputs=research))
        positioning_inputs["market"] = "step:market"
    steps += [
        StepDefinition(id="positioning", agent="positioning", inputs=positioning_inputs),
        StepDefinition(
            id="copy",
            agent="copywriter",
            inputs={
                "brief": _BRIEF,
                "positioning": "step:positioning",
                "audience": "step:audience",
                "brand": _BRAND,
            },
            review=_REVIEW,
        ),
    ]
    return tuple(steps)


def _safety(*deliverables: str) -> StepDefinition:
    return StepDefinition(
        id="safety",
        agent="safety_reviewer",
        inputs={name: f"step:{name}" for name in deliverables},
    )


def _workflow(
    template_id: str,
    name: str,
    description: str,
    steps: tuple[StepDefinition, ...],
    outputs: tuple[str, ...],
    limits: BudgetLimits,
    *,
    inputs: dict[str, str] | None = None,
    optional_inputs: dict[str, str] | None = None,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=template_id,
        name=name,
        version="1.0.0",
        description=description,
        inputs=inputs or {"brief": BRIEF_KIND},
        optional_inputs=_OPTIONAL_INPUTS if optional_inputs is None else optional_inputs,
        steps=steps,
        outputs={o: f"step:{o}" for o in outputs},
        limits=limits,
        max_parallel=1,
    )


def seo_content_pack() -> WorkflowDefinition:
    return _workflow(
        "seo-content-pack",
        "SEO content pack",
        "Keyword clusters (volumes only from your CSV), a content plan, a long-form article "
        "written section by section, an on-page SEO audit, and a safety review.",
        (
            StepDefinition(
                id="keywords",
                agent="keyword_research",
                inputs={"brief": _BRIEF, "sources": _SOURCES},
            ),
            StepDefinition(
                id="plan",
                agent="seo_content_planner",
                inputs={"brief": _BRIEF, "keywords": "step:keywords"},
            ),
            StepDefinition(
                id="article",
                agent="blog_writer",
                inputs={
                    "brief": _BRIEF,
                    "keywords": "step:keywords",
                    "plan": "step:plan",
                    "sources": _SOURCES,
                    "brand": _BRAND,
                },
            ),
            StepDefinition(
                id="audit",
                agent="seo_optimizer",
                inputs={"article": "step:article", "keywords": "step:keywords"},
            ),
            _safety("article"),
        ),
        ("keywords", "plan", "article", "audit", "safety"),
        BudgetLimits(max_llm_calls=25, max_total_tokens=80_000, max_wall_seconds=2400),
    )


def performance_review() -> WorkflowDefinition:
    return _workflow(
        "performance-review",
        "Performance review",
        "Metrics computed from your campaign CSV (when given), A/B tests with sample sizes, "
        "and a growth backlog ranked by ICE.",
        (
            StepDefinition(
                id="performance",
                agent="performance_analyst",
                inputs={"sources": _SOURCES, "brief": _BRIEF},
                when=(Condition(source=_SOURCES, op="exists"),),
                optional=True,
            ),
            StepDefinition(
                id="experiments",
                agent="experiment_designer",
                inputs={"brief": _BRIEF, "performance": "step:performance"},
            ),
            StepDefinition(
                id="growth",
                agent="growth_strategist",
                inputs={"brief": _BRIEF, "performance": "step:performance"},
            ),
        ),
        ("performance", "experiments", "growth"),
        BudgetLimits(max_llm_calls=15, max_total_tokens=40_000, max_wall_seconds=1200),
        optional_inputs=_OPTIONAL_SOURCES,
    )


def product_launch() -> WorkflowDefinition:
    landing_inputs = {
        "brief": _BRIEF,
        "copy": "step:copy",
        "positioning": "step:positioning",
        "audience": "step:audience",
        "brand": _BRAND,
    }
    return _workflow(
        "product-launch",
        "Product launch",
        "Research, positioning and reviewed copy, then a reviewed landing page, a launch "
        "funnel with lead magnets, a launch email sequence, and a safety review.",
        (
            *_copy_steps(),
            StepDefinition(
                id="landing", agent="landing_page", inputs=landing_inputs, review=_REVIEW
            ),
            StepDefinition(
                id="funnel",
                agent="funnel_strategist",
                inputs={
                    "brief": _BRIEF,
                    "positioning": "step:positioning",
                    "audience": "step:audience",
                    "brand": _BRAND,
                },
            ),
            StepDefinition(
                id="email",
                agent="email_marketing",
                inputs={"brief": _BRIEF, "copy": "step:copy", "audience": "step:audience"},
                optional=True,
            ),
            _safety("copy", "landing", "email"),
        ),
        ("copy", "landing", "funnel", "email", "safety"),
        BudgetLimits(max_llm_calls=30, max_total_tokens=100_000, max_wall_seconds=3600),
    )


def landing_page() -> WorkflowDefinition:
    return _workflow(
        "landing-page",
        "Landing page",
        "Reviewed copy and a reviewed landing page, a conversion checklist with prioritised "
        "fixes, A/B tests with sample sizes, and a safety review.",
        (
            *_copy_steps(),
            StepDefinition(
                id="landing",
                agent="landing_page",
                inputs={
                    "brief": _BRIEF,
                    "copy": "step:copy",
                    "positioning": "step:positioning",
                    "audience": "step:audience",
                    "brand": _BRAND,
                },
                review=_REVIEW,
            ),
            StepDefinition(
                id="cro", agent="cro_advisor", inputs={"landing": "step:landing", "brief": _BRIEF}
            ),
            StepDefinition(
                id="experiments",
                agent="experiment_designer",
                inputs={"brief": _BRIEF, "landing": "step:landing"},
                optional=True,
            ),
            _safety("copy", "landing"),
        ),
        ("copy", "landing", "cro", "experiments", "safety"),
        BudgetLimits(max_llm_calls=25, max_total_tokens=80_000, max_wall_seconds=3000),
    )


def content_repurpose() -> WorkflowDefinition:
    return _workflow(
        "content-repurpose",
        "Content repurposing",
        "Turns your own text (a post, an article, notes) into other formats within each "
        "platform's limits, then a safety review.",
        (
            StepDefinition(
                id="repurposed",
                agent="repurposer",
                inputs={"brief": _BRIEF, "sources": _SOURCES, "brand": _BRAND},
                review=_REVIEW,
            ),
            _safety("repurposed"),
        ),
        ("repurposed", "safety"),
        BudgetLimits(max_llm_calls=10, max_total_tokens=30_000, max_wall_seconds=1200),
        inputs={"brief": BRIEF_KIND, "sources": SOURCES_KIND},
        optional_inputs={"brand": BRAND_KIND},
    )


def ecommerce_campaign() -> WorkflowDefinition:
    content = {"brief": _BRIEF, "copy": "step:copy", "positioning": "step:positioning"}
    return _workflow(
        "ecommerce-campaign",
        "E-commerce campaign",
        "Research, positioning and reviewed copy, reviewed product-page copy, ads, a social "
        "calendar, an email sequence (when email is a channel), and a safety review.",
        (
            *_copy_steps(),
            StepDefinition(
                id="product",
                agent="product_description",
                inputs={**content, "brand": _BRAND},
                review=_REVIEW,
            ),
            StepDefinition(id="ads", agent="ad_copy", inputs=content, optional=True),
            StepDefinition(id="social", agent="social_media", inputs=content, optional=True),
            StepDefinition(
                id="email",
                agent="email_marketing",
                inputs={"brief": _BRIEF, "copy": "step:copy", "audience": "step:audience"},
                when=(Condition(source=_BRIEF, path="channels", op="contains", value="email"),),
                optional=True,
            ),
            _safety("copy", "product", "ads", "social", "email"),
        ),
        ("copy", "product", "ads", "social", "email", "safety"),
        BudgetLimits(max_llm_calls=35, max_total_tokens=120_000, max_wall_seconds=3600),
    )


def b2b_campaign() -> WorkflowDefinition:
    return _workflow(
        "b2b-campaign",
        "B2B campaign",
        "Audience and market research, positioning, a channel plan, reviewed copy, a reviewed "
        "landing page, a nurture email sequence, a demand-generation funnel, and a safety "
        "review.",
        (
            *_copy_steps(market=True),
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
                id="landing",
                agent="landing_page",
                inputs={
                    "brief": _BRIEF,
                    "copy": "step:copy",
                    "positioning": "step:positioning",
                    "audience": "step:audience",
                    "brand": _BRAND,
                },
                review=_REVIEW,
            ),
            StepDefinition(
                id="email",
                agent="email_marketing",
                inputs={"brief": _BRIEF, "copy": "step:copy", "audience": "step:audience"},
                optional=True,
            ),
            StepDefinition(
                id="funnel",
                agent="funnel_strategist",
                inputs={
                    "brief": _BRIEF,
                    "positioning": "step:positioning",
                    "audience": "step:audience",
                    "brand": _BRAND,
                },
            ),
            _safety("copy", "landing", "email"),
        ),
        ("strategy", "copy", "landing", "email", "funnel", "safety"),
        BudgetLimits(max_llm_calls=40, max_total_tokens=150_000, max_wall_seconds=3600),
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
        _info(competitor_report),
        _info(brand_kit),
        _info(seo_content_pack),
        _info(performance_review),
        _info(product_launch),
        _info(landing_page),
        _info(content_repurpose),
        _info(ecommerce_campaign),
        _info(b2b_campaign),
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
