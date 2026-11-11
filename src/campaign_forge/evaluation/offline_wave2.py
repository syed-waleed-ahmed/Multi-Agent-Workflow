"""Deterministic offline answers for the wave-2 agents (Session 8).

Like :mod:`.offline`, every answer is clearly synthetic. It is grounded only in
the product name it reads back from the framed brief, and it is always labeled
simulated wherever it is shown. The answers respect every contract rule, so the
offline evals exercise the real validators.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from ..providers.base import CompletionRequest

_PRODUCT_RE = re.compile(r"Product name:\s*(.+)")


def _product(request: CompletionRequest) -> str:
    for message in request.messages:
        match = _PRODUCT_RE.search(message.content)
        if match:
            return match.group(1).strip()
    return "the product"


def _prompt(request: CompletionRequest) -> str:
    return "\n".join(m.content for m in request.messages)


def _funnel(request: CompletionRequest) -> str:
    product = _product(request)
    stages = [
        ("Awareness", "Reach people who have the problem", "Reach"),
        ("Consideration", f"Show why {product} fits their routine", "Engaged visitors"),
        ("Conversion", "Turn interest into a first purchase", "Conversion rate"),
    ]
    return json.dumps(
        {
            "stages": [
                {
                    "name": name,
                    "goal": goal,
                    "content": [f"{name} post about {product}", f"{name} short video"],
                    "conversion_point": f"{name} click-through",
                    "metric": metric,
                }
                for name, goal, metric in stages
            ],
            "lead_magnets": [
                {
                    "title": f"The {product} care guide",
                    "format": "PDF checklist",
                    "promise": "Keep your gear in shape for years.",
                    "stage": "Consideration",
                }
            ],
        }
    )


def _growth(request: CompletionRequest) -> str:
    product = _product(request)
    ideas = [
        ("Referral credit", "email", 7, 6, 6),
        ("Creator unboxing series", "tiktok", 8, 5, 5),
        ("Bundle with a cleaning kit", "website", 6, 7, 8),
        ("Retargeting with reviews", "meta", 6, 6, 7),
        ("Trail-club partnership", "partnerships", 7, 4, 5),
    ]
    return json.dumps(
        {
            "ideas": [
                {
                    "name": name,
                    "hypothesis": f"{name} will grow {product} sales among the target audience.",
                    "channel": channel,
                    "impact": impact,
                    "confidence": confidence,
                    "ease": ease,
                }
                for name, channel, impact, confidence, ease in ideas
            ]
        }
    )


def _experiments(request: CompletionRequest) -> str:
    product = _product(request)
    return json.dumps(
        {
            "experiments": [
                {
                    "name": "Benefit-led headline",
                    "hypothesis": f"Leading with durability lifts {product} conversions.",
                    "control": "Current headline",
                    "variant": "Durability-first headline",
                    "primary_metric": "conversion rate",
                    "baseline_rate": 0.03,
                    "minimum_detectable_effect": 0.2,
                },
                {
                    "name": "Lifestyle ad image",
                    "hypothesis": "Real-use imagery earns more clicks than studio shots.",
                    "control": "Studio product shot",
                    "variant": "Outdoor lifestyle shot",
                    "primary_metric": "click-through rate",
                    "baseline_rate": 0.02,
                    "minimum_detectable_effect": 0.15,
                },
            ]
        }
    )


def _performance(request: CompletionRequest) -> str:
    return json.dumps(
        {
            "summary": "Spend is concentrated where conversion costs are highest; the table "
            "above shows where each dollar works hardest.",
            "insights": [
                "The channel with the highest CTR is not the one with the lowest CPA.",
                "Revenue per dollar differs sharply between channels.",
            ],
            "recommendations": [
                "Shift budget toward the channel with the best ROAS in the table.",
                "Test a new creative where CTR is lowest before cutting spend.",
            ],
        }
    )


def _landing(request: CompletionRequest) -> str:
    product = _product(request)
    return json.dumps(
        {
            "headline": f"{product}: built for every day"[:70],
            "subheadline": f"{product} is made for people who want gear that simply works.",
            "benefits": [
                {"title": "Lasts for years", "text": "Built to be used daily without wearing out."},
                {"title": "Easy to carry", "text": "Fits your bag and your routine."},
                {"title": "Simple to clean", "text": "Takes seconds at the end of the day."},
            ],
            "proof": [
                {"kind": "testimonial", "text": "[Customer quote to be supplied]"},
                {"kind": "feature", "text": f"{product} is designed for daily use."},
            ],
            "faq": [
                {"question": "How do I clean it?", "answer": "Rinse with warm water."},
                {"question": "Does it come with a warranty?", "answer": "See the product page."},
                {"question": "How long is delivery?", "answer": "See the shipping details."},
            ],
            "cta_text": f"Get {product} today.",
            "cta_button": "Get yours now",
        }
    )


def _product_copy(request: CompletionRequest) -> str:
    product = _product(request)
    return json.dumps(
        {
            "title": f"{product} - built for every day"[:150],
            "short_description": f"{product}: dependable, easy to carry, made to last."[:160],
            "long_description": f"{product} is designed for people who want gear that simply "
            "works, day after day.",
            "bullets": ["Made to last for years", "Easy to carry", "Simple to clean"],
            "seo_title": f"{product} | Built to last"[:60],
            "meta_description": f"Discover {product}: dependable, easy to carry and made to last."[
                :160
            ],
        }
    )


def _repurposed(request: CompletionRequest) -> str:
    product = _product(request)
    return json.dumps(
        {
            "source": "the campaign's core copy",
            "pieces": [
                {
                    "format": "x_thread",
                    "content": f"Meet {product}.\n\nBuilt for every day, made to last for "
                    "years.\n\nWhat would you take with you?",
                },
                {
                    "format": "linkedin_post",
                    "content": f"We built {product} for people who want gear that simply works.",
                },
                {
                    "format": "newsletter_blurb",
                    "content": f"This week: why {product} keeps up with your day.",
                },
            ],
        }
    )


def _blog(request: CompletionRequest) -> str:
    product = _product(request)
    text = _prompt(request)
    if "outline a helpful blog article" in text:
        return json.dumps(
            {
                "title": f"How to choose gear like {product}"[:70],
                "meta_description": f"A practical guide to choosing everyday gear such as "
                f"{product}: what matters, what to skip and how to decide."[:160],
                "sections": [
                    {"heading": "What matters most", "points": ["Durability", "Ease of use"]},
                    {"heading": "What to skip", "points": ["Gimmicks"]},
                    {"heading": "How to decide", "points": ["Match it to your routine"]},
                ],
            }
        )
    match = re.search(r"Section: (.+)", text)
    heading = match.group(1).strip() if match else "This section"
    sentence = (
        f"When it comes to {heading.lower()}, {product} is a useful example of what to look "
        "for. Think about how you will use it every day, how long it needs to last, and how "
        "easy it is to keep in good shape. "
    )
    return sentence * 5


def _keywords(request: CompletionRequest) -> str:
    product = _product(request).casefold()
    return json.dumps(
        {
            "seed_keywords": [product, f"best {product}"],
            "clusters": [
                {
                    "name": "Buying",
                    "intent": "commercial",
                    "keywords": [f"best {product}", f"{product} review", f"buy {product}"],
                },
                {
                    "name": "How-to",
                    "intent": "informational",
                    "keywords": [f"how to clean {product}", f"{product} care"],
                },
            ],
        }
    )


def _seo_plan(request: CompletionRequest) -> str:
    product = _product(request)
    titles = ["Buying guide", "Care guide", "Comparison", "Everyday uses"]
    return json.dumps(
        {
            "pieces": [
                {
                    "title": f"{product}: {title}"[:70],
                    "target_keyword": f"{product} {title}".casefold(),
                    "intent": "informational" if index else "commercial",
                    "outline": ["Introduction", "Main points", "Summary"],
                    "internal_links": [titles[(index + 1) % len(titles)]],
                }
                for index, title in enumerate(titles)
            ]
        }
    )


def _recommendations(request: CompletionRequest) -> str:
    return json.dumps(
        {"recommendations": ["Address the failed checks first.", "Then tighten the wording."]}
    )


def _cro(request: CompletionRequest) -> str:
    return json.dumps(
        {
            "recommendations": [
                {
                    "change": "Replace the proof placeholders with real customer quotes.",
                    "reason": "Specific proof reduces doubt at the decision point.",
                    "impact": "high",
                },
                {
                    "change": "Make the button say exactly what happens next.",
                    "reason": "A specific call to action sets expectations.",
                    "impact": "medium",
                },
            ]
        }
    )


WAVE2_RESPONDERS: dict[str, Callable[[CompletionRequest], str]] = {
    "funnel_strategist": _funnel,
    "growth_strategist": _growth,
    "experiment_designer": _experiments,
    "performance_analyst": _performance,
    "landing_page": _landing,
    "product_description": _product_copy,
    "repurposer": _repurposed,
    "blog_writer": _blog,
    "keyword_research": _keywords,
    "seo_content_planner": _seo_plan,
    "seo_optimizer": _recommendations,
    "safety_reviewer": lambda request: json.dumps({"flags": []}),
    "cro_advisor": _cro,
}
