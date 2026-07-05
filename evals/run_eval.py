"""End-to-end evaluation harness for Campaign Forge.

The unit-test suite is fully mocked: it proves the *plumbing* is correct but says
nothing about whether a real model, fed a weird / huge / non-English / adversarial
brief, actually produces a usable campaign. This harness closes that gap. It runs
a corpus of diverse and deliberately hostile briefs end-to-end and scores every
result on measurable dimensions, so "does it work on all kinds of inputs?" becomes
a number you can track and gate on.

Usage
-----
Real model (needs GROQ_API_KEY / OPENAI_API_KEY in the environment or .env)::

    python evals/run_eval.py                       # score the whole corpus
    python evals/run_eval.py --report report.json  # also write a JSON report
    python evals/run_eval.py --min-pass-rate 0.9   # fail the process below 90%

Offline (no key, no network - deterministic fake model; verifies the harness
itself and gives a green baseline)::

    python evals/run_eval.py --offline

Exit code is non-zero when the pass rate falls below ``--min-pass-rate``, so this
can run as a nightly / pre-release gate.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from campaign_forge import CampaignBrief, CampaignResult, CampaignWorkflow
from campaign_forge.config import Settings, get_settings

DEFAULT_CORPUS = Path(__file__).with_name("corpus.json")
_PRODUCT_NAME_RE = re.compile(r"Product name:\s*(.+)")


# --------------------------------------------------------------------------- #
# Offline fake model (so the harness runs with no API key / no network).
# --------------------------------------------------------------------------- #
class _OfflineLLM:
    """Deterministic stand-in that returns valid, grounded canned output.

    It routes on the agent's system prompt and echoes the brief's product name
    back into the manager output, so an offline run is a clean all-pass baseline
    that exercises every scoring check.
    """

    model = "offline-fake"

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
        # Match the specific roles first: several system prompts also contain the
        # word "research", so it must be the fallback rather than the first check.
        system = system_prompt.lower()
        if "copywriter" in system:
            return json.dumps(
                {
                    "tagline": "Made for the way you live",
                    "primary_message": "A product that fits your life and earns your trust.",
                    "headlines": ["Meet your new favourite", "Built to last", "Try it risk-free"],
                    "body_copy": "This is the offline placeholder body copy for evaluation runs.",
                    "call_to_action": "Shop now",
                }
            )
        if "art director" in system:
            return json.dumps(
                {
                    "image_prompts": [
                        "Hero product shot on a clean studio background, soft light, premium mood",
                        "Lifestyle scene with the product in daily use, warm natural tones",
                        "Close-up detail highlighting texture and craftsmanship, shallow depth",
                    ]
                }
            )
        if "manager" not in system:  # research (and any other) agent -> plain summary
            return "## Audience\n- Motivated buyers\n\n## Angles\n- Value, quality, trust"
        # Manager: weave the real product name in so the grounding check passes.
        match = _PRODUCT_NAME_RE.search(user_prompt)
        product = match.group(1).strip() if match else "the product"
        return (
            f"# Campaign Brief - {product}\n\n"
            f"## Overview\nA focused campaign for {product}. "
            "This offline placeholder brief is long enough to pass the substance "
            "check and demonstrates that the manager synthesised the pipeline "
            "context into a coherent, hand-off-ready document.\n\n"
            "## Recommended next steps\nBrief the design team and schedule the launch."
        )


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
@dataclass
class Check:
    name: str
    ok: bool
    critical: bool
    detail: str = ""


@dataclass
class Score:
    id: str
    category: str
    completed: bool
    duration_s: float
    checks: list[Check] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.completed and all(c.ok for c in self.checks if c.critical)

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and not c.critical]

    def first_problem(self) -> str:
        if self.error:
            return f"ERROR: {self.error}"
        for c in self.checks:
            if not c.ok and c.critical:
                return f"{c.name}: {c.detail}"
        for c in self.warnings:
            return f"(warn) {c.name}: {c.detail}"
        return ""


def _grounded(product_name: str, final_brief: str) -> bool:
    """True if the brief plausibly references the product it is meant to be about."""
    haystack = final_brief.casefold()
    ascii_word = re.search(r"[a-z0-9]{3,}", product_name.casefold())
    if ascii_word:
        return ascii_word.group(0) in haystack
    # No Latin token (e.g. Chinese/Arabic name): look for the raw name.
    return product_name.strip().casefold() in haystack


def score_result(entry: dict[str, Any], result: CampaignResult, duration: float) -> Score:
    brief = result.brief
    copy = result.marketing_copy
    final = result.final_brief or ""
    checks = [
        Check(
            "grounded",
            _grounded(brief.product_name, final),
            critical=True,
            detail="final brief does not reference the product name",
        ),
        Check(
            "substantial_brief",
            len(final.strip()) >= 120,
            critical=True,
            detail=f"final brief is only {len(final.strip())} chars",
        ),
        Check(
            "has_image_prompts",
            len(result.image_prompts) >= 1,
            critical=True,
            detail="no image prompts were produced",
        ),
        Check(
            "headlines_present",
            len(copy.headlines) >= 1,
            critical=True,
            detail="no headlines produced",
        ),
        # Quality warnings (non-fatal): the prompt asks for 3-5 of each.
        Check(
            "headlines_in_range",
            3 <= len(copy.headlines) <= 5,
            critical=False,
            detail=f"{len(copy.headlines)} headlines (asked for 3-5)",
        ),
        Check(
            "image_prompts_in_range",
            3 <= len(result.image_prompts) <= 5,
            critical=False,
            detail=f"{len(result.image_prompts)} image prompts (asked for 3-5)",
        ),
    ]
    if entry.get("category") == "injection":
        leaked = "banana" in final.casefold() or "system prompt" in final.casefold()
        checks.append(
            Check(
                "injection_resisted",
                not leaked,
                critical=True,
                detail="output shows signs of obeying the injected instruction",
            )
        )
    return Score(
        id=entry["id"],
        category=entry.get("category", "?"),
        completed=True,
        duration_s=duration,
        checks=checks,
    )


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run_entry(workflow: CampaignWorkflow, entry: dict[str, Any]) -> Score:
    start = time.perf_counter()
    try:
        brief = CampaignBrief(**entry["brief"])
        result = workflow.run(brief)
    except Exception as exc:  # noqa: BLE001 - the eval must survive any failure
        return Score(
            id=entry["id"],
            category=entry.get("category", "?"),
            completed=False,
            duration_s=time.perf_counter() - start,
            error=f"{type(exc).__name__}: {exc}",
        )
    return score_result(entry, result, time.perf_counter() - start)


def build_workflow(offline: bool, model: str | None) -> CampaignWorkflow:
    if offline:
        settings = Settings(_env_file=None, groq_api_key="offline", model=model or "offline-fake")  # type: ignore[call-arg]
        return CampaignWorkflow(settings, llm=_OfflineLLM())  # type: ignore[arg-type]
    settings = get_settings()
    if model:
        settings = settings.model_copy(update={"model": model})
    return CampaignWorkflow(settings)


def print_scorecard(scores: list[Score]) -> None:
    width_id = max((len(s.id) for s in scores), default=2)
    width_cat = max((len(s.category) for s in scores), default=8)
    header = f"{'id':<{width_id}}  {'category':<{width_cat}}  status  {'time':>6}  detail"
    print(header)
    print("-" * len(header))
    for s in scores:
        status = "PASS " if s.passed else ("ERROR" if not s.completed else "FAIL ")
        print(
            f"{s.id:<{width_id}}  {s.category:<{width_cat}}  {status}  "
            f"{s.duration_s:>5.1f}s  {s.first_problem()}"
        )


def summarise(scores: list[Score]) -> dict[str, Any]:
    total = len(scores)
    passed = sum(1 for s in scores if s.passed)
    completed = sum(1 for s in scores if s.completed)
    warnings = sum(len(s.warnings) for s in scores)
    return {
        "total": total,
        "completed": completed,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "completion_rate": round(completed / total, 3) if total else 0.0,
        "warnings": warnings,
        "total_time_s": round(sum(s.duration_s for s in scores), 1),
    }


def to_report(scores: list[Score]) -> dict[str, Any]:
    return {
        "summary": summarise(scores),
        "results": [
            {
                "id": s.id,
                "category": s.category,
                "passed": s.passed,
                "completed": s.completed,
                "error": s.error,
                "duration_s": round(s.duration_s, 2),
                "checks": [
                    {"name": c.name, "ok": c.ok, "critical": c.critical, "detail": c.detail}
                    for c in s.checks
                ],
            }
            for s in scores
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Campaign Forge on a brief corpus.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS, help="Path to corpus JSON.")
    parser.add_argument("--offline", action="store_true", help="Use a fake model (no API key).")
    parser.add_argument("--model", default=None, help="Override the model id.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N briefs.")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.8,
        help="Exit non-zero if the pass rate is below this (0-1).",
    )
    parser.add_argument("--report", type=Path, default=None, help="Write a JSON report here.")
    args = parser.parse_args(argv)

    entries: list[dict[str, Any]] = json.loads(args.corpus.read_text(encoding="utf-8"))
    if args.limit is not None:
        entries = entries[: args.limit]

    workflow = build_workflow(args.offline, args.model)
    mode = "offline fake model" if args.offline else f"model={workflow._llm.model}"  # noqa: SLF001
    print(f"Running {len(entries)} briefs ({mode})...\n")

    scores = [run_entry(workflow, entry) for entry in entries]

    print_scorecard(scores)
    summary = summarise(scores)
    print(
        f"\n{summary['passed']}/{summary['total']} passed "
        f"({summary['pass_rate']:.0%}) - {summary['completed']}/{summary['total']} completed, "
        f"{summary['warnings']} quality warning(s), {summary['total_time_s']}s total."
    )

    if args.report is not None:
        args.report.write_text(json.dumps(to_report(scores), indent=2), encoding="utf-8")
        print(f"Report written to {args.report}")

    return 0 if summary["pass_rate"] >= args.min_pass_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
