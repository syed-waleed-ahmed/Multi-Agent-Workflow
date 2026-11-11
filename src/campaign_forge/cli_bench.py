"""``campaign-forge bench``: run a dataset through a workflow and score it (the Evaluation Lab).

    campaign-forge bench full-campaign --offline                       # simulated, key-free
    campaign-forge bench full-campaign --offline --dataset injection   # injection cases
    campaign-forge bench seo-content-pack --only baseline-ecommerce    # live: real provider
    campaign-forge bench full-campaign --offline --baseline PATH       # fail on regressions

Without ``--offline`` the configured provider is used, so the report is a real
benchmark. It records the date, provider and models, and runs count against
your free-tier quota. Exit code: 0 when every case passes and nothing regressed;
1 otherwise.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .config import Settings
from .exceptions import ConfigurationError
from .orchestration.definition import load_definition
from .orchestration.factory import build_engine, simulated_engine
from .orchestration.templates import get_template
from .services.lab import (
    BUNDLED_DATASETS,
    CaseResult,
    compare,
    load_dataset,
    load_report,
    run_benchmark,
)


def add_bench_command(subparsers: Any, common: Any) -> None:
    bench = subparsers.add_parser("bench", help="Benchmark a workflow on a dataset")
    bench.add_argument("workflow", help="Template id or path to a JSON definition")
    bench.add_argument(
        "--dataset",
        default="corpus",
        help=f"Bundled dataset ({', '.join(BUNDLED_DATASETS)}) or a JSON file of cases",
    )
    bench.add_argument("--only", nargs="*", default=[], help="Run only these case ids")
    bench.add_argument(
        "--offline", action="store_true", help="Use the simulated responder (no key, no quota)"
    )
    bench.add_argument(
        "--baseline", type=Path, help="Compare with this report; fail on regressions"
    )
    bench.add_argument("--save-baseline", type=Path, help="Write this run's report as a baseline")
    bench.add_argument("--report", type=Path, help="Write the report as JSON")
    bench.add_argument("--markdown", type=Path, help="Write the report as Markdown")
    common(bench)
    bench.set_defaults(func=cmd_bench, needs_settings=True)


def cmd_bench(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    path = Path(args.workflow)
    definition = (
        load_definition(path)
        if args.workflow.endswith(".json") or path.is_file()
        else get_template(args.workflow)
    )
    name, cases = load_dataset(args.dataset)
    if args.only:
        cases = [c for c in cases if c.id in set(args.only)]
        if not cases:
            raise ConfigurationError(f"No case in {name!r} matches --only {' '.join(args.only)}.")
    baseline = load_report(args.baseline) if args.baseline else None
    engine = simulated_engine() if args.offline else build_engine(settings)

    def progress(result: CaseResult) -> None:
        if not args.json:
            mark = "[green]pass[/green]" if result.passed else "[red]FAIL[/red]"
            console.print(f"  {mark} {escape(result.id)} ({result.duration_s:.1f} s)")

    if not args.json:
        mode = "simulated" if engine.router.global_provider == "fake" else "live"
        console.print(f"Benchmark: {definition.id} on {name} ({len(cases)} cases, {mode})")
    report = run_benchmark(
        engine, definition, cases, dataset=name, version=__version__, on_case=progress
    )
    regressions = compare(report, baseline) if baseline else []
    for target, text in (
        (args.report, report.model_dump_json(indent=2)),
        (args.save_baseline, report.model_dump_json(indent=2)),
        (args.markdown, report.to_markdown()),
    ):
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
    if args.json:
        payload = report.model_dump(mode="json")
        payload["regressions"] = regressions
        print(json.dumps(payload, indent=2))
    else:
        _render(console, report, regressions)
    ok = report.summary.passed == report.summary.cases and not regressions
    return 0 if ok else 1


def _render(console: Console, report: Any, regressions: list[str]) -> None:
    table = Table(title=f"{report.workflow} on {report.dataset} ({report.mode})")
    for column in ("Case", "Result", "Seconds", "Calls", "Tokens", "Retries", "Problem"):
        table.add_column(column, overflow="fold", no_wrap=column in ("Case", "Result"))
    for r in report.results:
        table.add_row(
            escape(r.id),
            "[green]pass[/green]" if r.passed else "[red]FAIL[/red]",
            f"{r.duration_s:.1f}",
            str(r.llm_calls),
            f"{r.tokens:,}",
            str(r.retries),
            escape(r.problem()[:100]),
        )
    console.print(table)
    s = report.summary
    console.print(
        f"{s.passed}/{s.cases} passed ({s.success_rate:.0%}); {s.avg_calls:.1f} calls and "
        f"{s.avg_tokens:,.0f} tokens per case; {s.retries} retries; {s.warnings} warning(s); "
        f"estimated cost ${s.estimated_cost_usd:.2f}"
    )
    console.print(
        f"[dim]{report.started_at:%Y-%m-%d %H:%M} UTC; provider {report.provider}; models "
        + escape(", ".join(f"{k}={v}" for k, v in report.models.items()))
        + "[/dim]"
    )
    if regressions:
        console.print("[red]Regressions against the baseline:[/red]")
        for problem in regressions:
            console.print(f"  - {escape(problem)}")
