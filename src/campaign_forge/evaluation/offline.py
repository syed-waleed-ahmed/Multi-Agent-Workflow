"""A deterministic, offline responder that produces valid output for every built-in agent.

It powers the offline eval (a key-free CI gate), the integration tests, and later
the public demo mode. It routes on the ``agent`` metadata of each request, and
grounds its output in the product name and channels it reads back from the
framed brief, so the deterministic checks exercise real data.

Its output is clearly synthetic and must always be labeled as simulated wherever
it is shown.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable

from ..agents.schemas import ad_channels
from ..domain.provenance import Evidence
from ..exceptions import ConfigurationError
from ..providers.base import CompletionRequest
from ..tools.base import ToolSpec
from .checks import DEFAULT_RUBRIC, RUBRICS

_PRODUCT_RE = re.compile(r"Product name:\s*(.+)")
_EVIDENCE_RE = re.compile(r"\[(ev-[0-9a-f]{12})\]")
_CLAIM_RE = re.compile(r"^Claim (\d+):", re.MULTILINE)
#: The competitor named in every simulated search result.
OFFLINE_COMPETITOR = "RivalCo"

OFFLINE_SEARCH_SPEC = ToolSpec(
    id="offline_search",
    name="Offline search (simulated)",
    description="Deterministic simulated search results for tests, evals and demo mode.",
    network=False,
)


class OfflineSearch:
    """A deterministic search tool: two simulated reference results per query."""

    @property
    def spec(self) -> ToolSpec:
        return OFFLINE_SEARCH_SPEC

    def search(self, query: str, *, limit: int = 3) -> list[Evidence]:
        slug = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
        results = [
            Evidence.create(
                source=f"offline://reference/{slug}/{n}",
                title=f"Simulated reference {n}: {query}",
                excerpt=(
                    f"Simulated background on {query}. Buyers in this category value durability "
                    f"and convenience. {OFFLINE_COMPETITOR} is a well-known alternative that "
                    "competes mainly on price."
                ),
            )
            for n in (1, 2)
        ]
        return results[:limit]


def _evidence_ids(request: CompletionRequest) -> list[str]:
    ids: list[str] = []
    for message in request.messages:
        for found in _EVIDENCE_RE.findall(message.content):
            if found not in ids:
                ids.append(found)
    return ids


def _cited(request: CompletionRequest, text: str) -> dict[str, object]:
    """A finding that cites the first evidence id in the prompt, if any."""
    ids = _evidence_ids(request)
    if ids:
        return {"statement": text, "basis": "external_source", "evidence_ids": ids[:1]}
    return {"statement": text, "basis": "model_assumption"}


_CHANNELS_RE = re.compile(r"Channels:\s*(.+)")


def _brief_field(request: CompletionRequest, pattern: re.Pattern[str], default: str) -> str:
    for message in request.messages:
        match = pattern.search(message.content)
        if match:
            return match.group(1).strip()
    return default


def _product(request: CompletionRequest) -> str:
    return _brief_field(request, _PRODUCT_RE, "the product")


def _channels(request: CompletionRequest) -> list[str]:
    raw = _brief_field(request, _CHANNELS_RE, "email")
    return [c.strip() for c in raw.split(",") if c.strip()] or ["email"]


def _finding(text: str, basis: str = "model_assumption") -> dict[str, str]:
    return {"statement": text, "basis": basis}


def _audience(request: CompletionRequest) -> str:
    product = _product(request)
    return json.dumps(
        {
            "summary": f"Busy buyers who want {product} to make a daily routine simpler.",
            "personas": [
                {
                    "name": "Practical planner",
                    "description": f"Researches before buying and expects {product} to last.",
                    "motivations": ["save time", "buy once, buy well"],
                    "pain_points": ["unreliable products", "too much choice"],
                    "objections": ["price"],
                    "preferred_channels": _channels(request)[:2],
                }
            ],
            "insights": [
                _finding(f"The brief positions {product} for this audience.", "user_provided"),
                _cited(request, "Buyers in this category value durability and convenience."),
                _finding("Buyers in this category respond to proof of durability."),
            ],
        }
    )


def _market(request: CompletionRequest) -> str:
    return json.dumps(
        {
            "category_summary": f"{_product(request)} competes in a crowded, quality-led category.",
            "trends": [
                _cited(request, "Shoppers in this category value durability and convenience."),
                _finding("Shoppers increasingly favour products that last longer."),
            ],
            "competitor_angles": [_finding("Competitors tend to lead with price promotions.")],
            "positioning_angles": ["durability", "everyday convenience", "honest value"],
        }
    )


def _positioning(request: CompletionRequest) -> str:
    product = _product(request)
    return json.dumps(
        {
            "positioning_statement": f"For busy people who value reliability, {product} is the "
            "everyday essential that just works, unlike disposable alternatives.",
            "value_propositions": ["Lasts for years", "Simple to use every day"],
            "messaging_pillars": [
                {"name": "Reliability", "message": "Built to last.", "proof_points_needed": []},
                {"name": "Ease", "message": "Fits any routine.", "proof_points_needed": []},
            ],
            "benefit_ladder": [
                {
                    "feature": "Durable build",
                    "benefit": "Fewer replacements",
                    "emotional_payoff": "Peace of mind",
                }
            ],
            "differentiators": ["Designed for daily use"],
        }
    )


def _plan(request: CompletionRequest) -> str:
    channels = _channels(request)
    return json.dumps(
        {
            "objectives": [f"Grow awareness of {_product(request)}"],
            "kpis": ["reach", "conversion rate"],
            "channels": [
                {
                    "channel": c,
                    "role": "Drive awareness and action",
                    "weight": 5,
                    "kpis": ["clicks"],
                }
                for c in channels
            ],
            "phases": [
                {"name": "Launch", "weeks": 2, "focus": "Awareness", "activities": ["Announce"]},
                {"name": "Sustain", "weeks": 2, "focus": "Conversion", "activities": ["Retarget"]},
            ],
        }
    )


def _copy(request: CompletionRequest) -> str:
    product = _product(request)
    return json.dumps(
        {
            "tagline": f"{product}: made to last",
            "primary_message": f"{product} keeps up with your day, every day, for years.",
            "headlines": [f"Meet {product}", "Built for every day", "Buy once, use for years"],
            "body_copy": f"{product} is designed for people who want things that simply work.",
            "call_to_action": f"Get {product} today",
        }
    )


def _ads(request: CompletionRequest) -> str:
    product = _product(request)
    variants = []
    for channel in ad_channels(_channels(request)) * 2:
        variants.append(
            {
                "platform": channel,
                "headline": f"Meet {product}"[:30],
                "primary_text": f"{product} is built for every day. Try it now."[:120],
                "description": "Made to last."[:90],
                "call_to_action": "Shop now",
            }
        )
    return json.dumps({"variants": variants})


def _social(request: CompletionRequest) -> str:
    product = _product(request)
    channels = _channels(request)
    posts = [
        {
            "platform": channels[i % len(channels)],
            "day": day,
            "format": ("post", "reel", "story")[i % 3],
            "caption": f"Day {day}: why {product} belongs in your routine."[:200],
            "hashtags": ["#launch", product.replace(" ", "")],
            "visual_idea": f"{product} in everyday use",
        }
        for i, day in enumerate((1, 4, 8, 11))
    ]
    return json.dumps({"weeks": 2, "posts": posts})


def _email(request: CompletionRequest) -> str:
    product = _product(request)
    emails = [
        {
            "send_day": day,
            "purpose": purpose,
            "subject": f"{subject} {product}"[:58],
            "preview_text": "A quick note from the team.",
            "body": f"Here is why {product} is worth a look.",
            "call_to_action": "Learn more",
        }
        for day, purpose, subject in (
            (0, "Introduce", "Meet"),
            (3, "Educate", "How to use"),
            (7, "Convert", "Time to try"),
        )
    ]
    return json.dumps({"name": f"{product} launch sequence", "emails": emails})


def _visuals(request: CompletionRequest) -> str:
    product = _product(request)
    concepts = [
        {
            "name": name,
            "description": f"{product} shown {setting}.",
            "image_prompt": f"{product} {setting}, natural light, clean composition, warm "
            "colours, editorial photography style",
            "mood": mood,
            "palette": ["warm white", "sage green"],
            "aspect_ratio": ratio,
        }
        for name, setting, mood, ratio in (
            ("Morning routine", "on a kitchen counter at sunrise", "calm", "4:5"),
            ("On the move", "in a commuter's bag on a city street", "energetic", "9:16"),
            ("Detail", "in a close-up studio shot", "premium", "1:1"),
        )
    ]
    return json.dumps({"concepts": concepts})


def _competitors(request: CompletionRequest) -> str:
    competitors = []
    prompt = " ".join(m.content for m in request.messages)
    # Name a competitor only if it appears in the material (the agent's own guard enforces it).
    if _evidence_ids(request) and OFFLINE_COMPETITOR in prompt:
        competitors.append(
            {
                "name": OFFLINE_COMPETITOR,
                "summary": f"{OFFLINE_COMPETITOR} is an established alternative.",
                "positioning": _cited(request, f"{OFFLINE_COMPETITOR} competes mainly on price."),
                "strengths": [_cited(request, "Well-known in the category.")],
                "weaknesses": [_finding("Less focus on durability.")],
            }
        )
    return json.dumps(
        {
            "competitors": competitors,
            "opportunities": [
                _finding(f"Lead on durability against {_product(request)}'s rivals.")
            ],
        }
    )


def _fact_check(request: CompletionRequest) -> str:
    indices = sorted({int(i) for m in request.messages for i in _CLAIM_RE.findall(m.content)})
    return json.dumps(
        {
            "verdicts": [
                {"index": i, "verdict": "supported", "note": "Matches the evidence."}
                for i in indices
            ]
        }
    )


def _brand(request: CompletionRequest) -> str:
    product = _product(request)
    return json.dumps(
        {
            "name": product,
            "voice": f"{product} speaks plainly and warmly, like a friend who knows the product.",
            "tone_words": ["warm", "plain", "confident"],
            "audience": "People who want things that simply work.",
            "values": ["durability", "honesty"],
            "guidelines": ["Lead with a concrete benefit", "Use short sentences"],
            "avoid": ["Hype", "Claims the brief cannot support"],
            "preferred_terms": ["every day", "for years"],
            "banned_terms": ["revolutionary", "game-changing", "cheap"],
            "visual_style": "Natural light, real people, uncluttered backgrounds.",
        }
    )


def _brand_tone(request: CompletionRequest) -> str:
    return json.dumps({"score": 4, "notes": ["Plain and warm, as the profile describes."]})


def _plan_or(builder: Callable[[CompletionRequest], str]) -> Callable[[CompletionRequest], str]:
    """Answer a research plan request with a query; otherwise use ``builder``."""

    def respond(request: CompletionRequest) -> str:
        if request.metadata.get("phase") == "plan":
            return json.dumps({"queries": [f"{_product(request)} market"]})
        return builder(request)

    return respond


def _critic(request: CompletionRequest) -> str:
    criteria = {c for rubric in RUBRICS.values() for c in rubric} | set(DEFAULT_RUBRIC)
    return json.dumps(
        {
            "scores": dict.fromkeys(sorted(criteria), 4),
            "issues": [],
            "instructions": "Keep the strongest headline first.",
            "summary": "Solid, on-brief work.",
        }
    )


def _report_narrative(request: CompletionRequest) -> str:
    product = _product(request)
    sections = {
        "Overview": f"This campaign introduces {product} to a busy, reliability-minded audience.",
        "Audience & Insights": "The audience researches first and distrusts flimsy products.",
        "Positioning & Messaging": f"{product} is positioned as the everyday essential that lasts.",
        "Channel Plan": "Effort is split across the brief's channels with a two-phase timeline.",
        "Creative Direction": "Warm, natural visuals show the product in real daily routines.",
        "Next Steps": "Approve the copy, brief the designer, and schedule the first posts.",
    }
    filler = (
        " The team should keep every message consistent with the positioning, measure "
        "results weekly against the agreed KPIs, and adjust the channel mix after the first "
        "phase based on what the data shows."
    )
    return "\n\n".join(f"## {title}\n\n{body}{filler}" for title, body in sections.items())


def _legacy_copy(request: CompletionRequest) -> str:
    data = json.loads(_copy(request))
    return json.dumps(data)


def _legacy_prompts(request: CompletionRequest) -> str:
    concepts = json.loads(_visuals(request))["concepts"]
    return json.dumps({"image_prompts": [c["image_prompt"] for c in concepts]})


def _legacy_manager(request: CompletionRequest) -> str:
    product = _product(request)
    body = (
        f"{product} is introduced to its audience with a consistent message about reliability, "
        "supported by clear visuals and a measured rollout across the brief's channels."
    )
    titles = (
        "Overview",
        "Target Audience & Insights",
        "Positioning & Key Messages",
        "Example Headlines & Copy",
        "Visual Direction",
        "Deliverables",
    )
    return f"# Campaign Brief - {product}\n\n" + "\n\n".join(f"## {t}\n\n{body}" for t in titles)


RESPONDERS: dict[str, Callable[[CompletionRequest], str]] = {
    "audience_research": _plan_or(_audience),
    "market_research": _plan_or(_market),
    "competitor_research": _plan_or(_competitors),
    "fact_checker": _fact_check,
    "positioning": _positioning,
    "marketing_strategist": _plan,
    "copywriter": _copy,
    "ad_copy": _ads,
    "social_media": _social,
    "email_marketing": _email,
    "art_director": _visuals,
    "critic": _critic,
    "campaign_reporter": _report_narrative,
    "brand_strategist": _brand,
    "brand_consistency": _brand_tone,
    "legacy.research": lambda r: f"## Audience\n- Buyers of {_product(r)}\n\n## Angles\n- Value",
    "legacy.copywriter": _legacy_copy,
    "legacy.art_director": _legacy_prompts,
    "legacy.manager": _legacy_manager,
    "planner": lambda r: json.dumps({"template": "full-campaign", "rationale": "Default plan."}),
}


def offline_responder(request: CompletionRequest) -> str:
    """Deterministic output for the requesting agent (``request.metadata['agent']``)."""
    agent = request.metadata.get("agent", "")
    responder = RESPONDERS.get(agent)
    if responder is None:
        raise ConfigurationError(f"The offline responder has no script for agent {agent!r}.")
    return responder(request)
