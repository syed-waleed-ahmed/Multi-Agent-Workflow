"""Synthesis agent (wave 1): the final campaign report.

The model writes only the **narrative**: six required sections, validated, with
one repair attempt. Code then appends the **exact deliverables**, rendered from
the upstream artifacts:

* copy, ads, calendar, emails, visual prompts and the channel plan;
* every model assumption collected from the research claims;
* a list of what this run did not produce.

The hand-off document therefore never paraphrases or invents the actual
deliverables (defect D4, 2.0 contract).
"""

from __future__ import annotations

from collections.abc import Mapping

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ...domain.brand import BRAND_KIND
from ...domain.provenance import Basis, Evidence
from ...evaluation.checks import REPORT_SECTIONS, missing_sections
from ...exceptions import OutputParsingError
from ...providers.base import CompletionRequest, CompletionResponse, ModelClass
from ...security.untrusted import with_untrusted_policy
from ..schemas import (
    ADS_KIND,
    AUDIENCE_KIND,
    BRAND_CHECK_KIND,
    CORE_COPY_KIND,
    EMAIL_KIND,
    MARKET_KIND,
    MODELS_BY_KIND,
    PLAN_KIND,
    POSITIONING_KIND,
    REPORT_DIVIDER,
    REPORT_KIND,
    SOCIAL_KIND,
    VISUALS_KIND,
    CampaignReport,
)
from .common import (
    BRIEF,
    AgentCategory,
    brief_of,
    context,
    framed_brief,
    make_spec,
    produced_by,
    total_usage,
)

MIN_NARRATIVE_WORDS = 120

#: (input name, artifact kind, appendix title)
_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("audience", AUDIENCE_KIND, "Audience research"),
    ("market", MARKET_KIND, "Market research"),
    ("positioning", POSITIONING_KIND, "Positioning"),
    ("plan", PLAN_KIND, "Channel plan"),
    ("copy", CORE_COPY_KIND, "Core copy"),
    ("ads", ADS_KIND, "Ads"),
    ("social", SOCIAL_KIND, "Social calendar"),
    ("email", EMAIL_KIND, "Email sequence"),
    ("visuals", VISUALS_KIND, "Visual concepts and image prompts"),
    ("brand", BRAND_KIND, "Brand profile"),
    ("brand_check", BRAND_CHECK_KIND, "Brand check"),
)
#: Inputs the report needs in order to be meaningful.
_REQUIRED = {"copy"}
#: Inputs that only some runs have (a brand is optional), so their absence is not reported.
_EXTRAS = {"brand", "brand_check"}


def _render(artifact: Artifact) -> str | None:
    model = MODELS_BY_KIND[artifact.kind]
    to_markdown = getattr(artifact.as_model(model), "to_markdown", None)
    return str(to_markdown()) if callable(to_markdown) else None


class CampaignReporterAgent:
    spec = make_spec(
        "campaign_reporter",
        "Campaign reporter",
        "The final hand-off brief: validated narrative plus deliverables rendered by code.",
        category=AgentCategory.SYNTHESIS,
        inputs=(
            BRIEF,
            *(
                InputSpec(name=name, kind=kind, required=name in _REQUIRED)
                for name, kind, _ in _SOURCES
            ),
        ),
        output_kind=REPORT_KIND,
        capabilities=("reporting", "synthesis"),
        rubric=("on_brief", "completeness", "actionability", "clarity"),
        max_tokens=1600,
        # A 350-600 word narrative does not fit a 1,000 output-token cap (D17, ADR-032).
        model_class=ModelClass.STRONG,
    )

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        brief = brief_of(inputs)
        sections = "\n".join(f"## {name}" for name in REPORT_SECTIONS)
        system = with_untrusted_policy(
            "You are a campaign lead writing the brief a marketing and design team will work "
            "from. You write clear, specific Markdown."
        )
        user = (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, *(name for name, _, _ in _SOURCES))}\n\n"
            f"Task: write the narrative of the campaign brief for {brief.product_name} using "
            f"exactly these Markdown headings, in this order:\n{sections}\n\n"
            "Explain the reasoning and how the pieces fit together in 350-600 words. Do not "
            "restate every "
            "deliverable: the full copy, ads, calendar, emails and prompts are appended "
            "automatically. Write plain Markdown, not JSON."
        )
        responses: list[CompletionResponse] = []
        request = CompletionRequest.from_prompts(system, user)
        narrative = ""
        for attempt in range(2):
            response = ctx.complete(self.spec, request)
            responses.append(response)
            narrative = response.text.strip()
            problems = self._problems(narrative)
            if not problems:
                break
            if attempt == 0:
                ctx.budget.charge_repair()
                request = CompletionRequest.from_prompts(
                    system,
                    f"{user}\n\nYour previous answer cannot be used because "
                    f"{'; '.join(problems)}. Write it again with all six headings.",
                )
        else:
            raise OutputParsingError(
                self.spec.id, "report narrative unusable: " + "; ".join(problems)
            )

        report = self._assemble(brief.product_name, narrative, inputs)
        artifact = Artifact.from_model(
            REPORT_KIND,
            report,
            produced_by=produced_by(self.spec, tuple(responses)),
            usage=total_usage(tuple(responses)),
        )
        notes = [f"{len(report.assumptions)} labeled assumption(s) listed"]
        if report.missing_inputs:
            notes.append("not produced in this run: " + ", ".join(report.missing_inputs))
        return AgentOutput(artifact=artifact, notes=tuple(notes))

    @staticmethod
    def _problems(narrative: str) -> list[str]:
        problems: list[str] = []
        missing = missing_sections(narrative)
        if missing:
            problems.append("it is missing sections: " + ", ".join(missing))
        words = len(narrative.split())
        if words < MIN_NARRATIVE_WORDS:
            problems.append(f"it is only {words} words long")
        return problems

    @staticmethod
    def _assemble(product: str, narrative: str, inputs: Mapping[str, Artifact]) -> CampaignReport:
        parts = [f"# Campaign Brief - {product}", narrative, REPORT_DIVIDER]
        missing: list[str] = []
        for name, kind, title in _SOURCES:
            artifact = inputs.get(name)
            if artifact is None:
                if name not in _EXTRAS:
                    missing.append(title)
                continue
            rendered = _render(artifact) if kind not in (AUDIENCE_KIND, MARKET_KIND) else None
            if rendered:
                parts.append(f"## {title}\n\n{rendered}")
        assumptions: list[str] = []
        for name, _, _ in _SOURCES:
            source = inputs.get(name)
            if source is None:
                continue
            assumptions.extend(
                c.statement for c in source.claims if c.basis is Basis.MODEL_ASSUMPTION
            )
        if assumptions:
            parts.append(
                "## Assumptions to validate\n\nThese statements came from model knowledge, not "
                "from verified sources. Check them before relying on them.\n\n"
                + "\n".join(f"- {a}" for a in assumptions)
            )
        sources: dict[str, Evidence] = {}
        for name, _, _ in _SOURCES:
            source_artifact = inputs.get(name)
            if source_artifact is not None:
                for item in source_artifact.evidence:
                    sources.setdefault(item.id, item)
        if sources:
            parts.append(
                "## Sources\n\n"
                + "\n".join(
                    f"- [{item.id}] {item.title or item.source} - {item.source} "
                    f"(retrieved {item.retrieved_at:%Y-%m-%d})"
                    for item in sources.values()
                )
            )
        if missing:
            parts.append("## Not produced in this run\n\n" + "\n".join(f"- {m}" for m in missing))
        return CampaignReport(
            title=f"Campaign Brief - {product}",
            markdown="\n\n".join(parts) + "\n",
            missing_inputs=missing,
            assumptions=assumptions,
        )
