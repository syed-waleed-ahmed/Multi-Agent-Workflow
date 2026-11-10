"""Evidence-backed research (ADR-005, ADR-029): plan, retrieve, synthesise.

1. **Plan.** The model proposes up to two search queries, as a structured
   ``ResearchPlan``. It never proposes URLs.
2. **Retrieve.** Code runs those queries on the search tools this agent is
   allowed to use (``RunContext.search``, which is allowlisted and budgeted).
   User-supplied sources are added as evidence.
3. **Synthesise.** The model writes findings that cite evidence ids. The output
   contract validates every cited id against what was actually retrieved, so an
   invented citation is a repair error.

If no evidence is available (no tools configured, and no sources supplied), the
agent works in labeled **assumptions mode**, where external claims are
impossible.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...core.agent import AgentOutput, InputSpec
from ...core.context import RunContext
from ...domain.artifacts import Artifact
from ...domain.provenance import Evidence
from ...domain.sources import SOURCES_KIND, SourceBundle
from ...providers.base import CompletionRequest
from ...security.untrusted import frame_untrusted, with_untrusted_policy
from ...tools.sources import bundle_evidence
from ..schemas import ResearchPlan
from .common import StructuredAgent, brief_of

SOURCES = InputSpec(name="sources", kind=SOURCES_KIND, required=False)
#: Search tools research agents may use, if configured.
RESEARCH_TOOLS = ("wikipedia", "tavily", "offline_search")
MAX_QUERIES = 2
RESULTS_PER_QUERY = 3
MAX_EVIDENCE = 8
EXCERPT_CHARS = 700

ASSUMPTIONS_RULES = (
    "No research evidence is available for this run. Label every finding "
    '"user_provided" if it restates the brief, otherwise "model_assumption", with no '
    "evidence_ids. Do not cite statistics, studies, or named sources."
)
EVIDENCE_RULES = (
    "Use the research evidence above. For each finding: if the evidence supports it, set "
    'basis to "external_source" and list the supporting ids in evidence_ids (for example '
    '["ev-0123456789ab"]); if it restates the brief or the user\'s own documents, use '
    '"user_provided"; otherwise "model_assumption". Cite ONLY ids that appear in the '
    "evidence. Never invent ids, statistics or sources."
)


def evidence_block(evidence: list[Evidence], user_ids: set[str]) -> str:
    """Evidence for the prompt: id, origin and excerpt, framed as untrusted data."""
    lines = []
    for item in evidence:
        origin = "user source" if item.id in user_ids else "web"
        excerpt = " ".join(item.excerpt.split())[:EXCERPT_CHARS]
        lines.append(f"[{item.id}] ({origin}) {item.title} - {item.source}\n{excerpt}")
    return frame_untrusted("\n\n".join(lines), label="research evidence")


class EvidenceResearchAgent(StructuredAgent):
    """A structured agent that gathers evidence before it writes."""

    #: What the agent is researching (used in the plan prompt).
    research_goal: ClassVar[str]

    def gather(
        self, inputs: Mapping[str, Artifact], ctx: RunContext
    ) -> tuple[list[Evidence], set[str]]:
        """Return (evidence, ids of user-supplied evidence)."""
        evidence: list[Evidence] = []
        if "sources" in inputs:
            evidence = bundle_evidence(inputs["sources"].as_model(SourceBundle))
            ctx.evidence.add(evidence)
        user_ids = {e.id for e in evidence}
        if ctx.search_tools(self.spec):
            for query in self._plan(inputs, ctx).queries[:MAX_QUERIES]:
                evidence.extend(ctx.search(self.spec, query, limit=RESULTS_PER_QUERY))
        unique: dict[str, Evidence] = {}
        for item in evidence:
            unique.setdefault(item.id, item)
        return list(unique.values())[:MAX_EVIDENCE], user_ids

    def _plan(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> ResearchPlan:
        brief = brief_of(inputs)
        system = with_untrusted_policy(
            "You are a research planner. You turn a research goal into short web-search "
            "queries. Respond with a single valid JSON object."
        )
        user = (
            "Campaign brief:\n"
            f"{frame_untrusted(brief.as_prompt_context(), label='campaign brief')}\n\n"
            f"Research goal: {self.research_goal}\n"
            f"Task: propose 1-{MAX_QUERIES} concise search queries (3-8 words each) that would "
            "find factual background. Do not include URLs.\n\n"
            'Return ONLY a JSON object: {"queries": ["...", "..."]}'
        )
        request = CompletionRequest.from_prompts(system, user, metadata={"phase": "plan"})
        return ctx.complete_structured(self.spec, request, ResearchPlan).value

    def extra_context(
        self, inputs: Mapping[str, Artifact], evidence: list[Evidence]
    ) -> dict[str, Any]:
        """Additional validation context for the synthesis contract."""
        return {}

    def run(self, inputs: Mapping[str, Artifact], ctx: RunContext) -> AgentOutput:
        evidence, user_ids = self.gather(inputs, ctx)
        if evidence:
            extra = (
                f"\n\nResearch evidence:\n{evidence_block(evidence, user_ids)}\n\n{EVIDENCE_RULES}"
            )
            mode_note = f"evidence mode: {len(evidence)} source(s) available"
        else:
            extra = f"\n\n{ASSUMPTIONS_RULES}"
            mode_note = "assumptions mode: no research evidence was available"
        context = {"evidence_ids": {e.id for e in evidence}, **self.extra_context(inputs, evidence)}
        output = self.produce(
            inputs,
            ctx,
            extra=extra,
            evidence=evidence,
            validation_context=context,
            notes=(mode_note,),
        )
        cited = len(output.artifact.evidence)
        if evidence:
            output = output.model_copy(
                update={"notes": (*output.notes, f"{cited} source(s) cited in the findings")}
            )
        return output
