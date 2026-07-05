"""Command-line interface for Campaign Forge.

Two subcommands:

* ``run``   - generate a single campaign (flags or interactive prompts).
* ``batch`` - generate many campaigns concurrently from a JSON file.

If no subcommand is given, ``run`` is assumed, preserving the original
``python main.py`` interactive experience.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import Settings, get_settings
from .exceptions import CampaignForgeError, ConfigurationError
from .loaders import load_briefs
from .logging_config import configure_logging
from .models import CampaignBrief, CampaignResult
from .workflow import BatchItemResult, CampaignWorkflow

_BRIEF_FIELDS: list[tuple[str, str, str]] = [
    ("product_name", "--product-name", "Product name"),
    ("description", "--description", "Product description"),
    ("audience", "--audience", "Target audience"),
    ("goal", "--goal", "Campaign goal"),
    ("tone", "--tone", "Desired tone (e.g. friendly, premium)"),
]
_KNOWN_COMMANDS = {"run", "batch"}


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _add_brief_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--product-name", dest="product_name", help="Name of the product")
    parser.add_argument("--description", dest="description", help="Short product description")
    parser.add_argument("--audience", dest="audience", help="Target audience")
    parser.add_argument("--goal", dest="goal", help="Campaign goal (e.g. signups, sales)")
    parser.add_argument("--tone", dest="tone", help="Tone of voice (e.g. fun, professional)")
    parser.add_argument(
        "--channels",
        dest="channels",
        help="Comma-separated channels (e.g. instagram, tiktok, email)",
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", dest="output_dir", type=Path, help="Directory for output")
    parser.add_argument("--no-save", action="store_true", help="Do not write result files")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="campaign-forge",
        description="Multi-agent marketing campaign generator.",
    )
    parser.add_argument("--version", action="version", version=f"campaign-forge {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Generate a single campaign")
    _add_brief_arguments(run_parser)
    _add_common_arguments(run_parser)
    run_parser.set_defaults(func=cmd_run)

    batch_parser = subparsers.add_parser("batch", help="Generate campaigns from a JSON file")
    batch_parser.add_argument("input", type=Path, help="Path to a JSON file of briefs")
    batch_parser.add_argument("--workers", type=int, help="Max concurrent campaigns")
    _add_common_arguments(batch_parser)
    batch_parser.set_defaults(func=cmd_batch)

    return parser


# --------------------------------------------------------------------------- #
# Settings / brief construction
# --------------------------------------------------------------------------- #
def _resolve_settings(args: argparse.Namespace) -> Settings:
    overrides: dict[str, Any] = {}
    if getattr(args, "output_dir", None) is not None:
        overrides["output_dir"] = args.output_dir
    if getattr(args, "workers", None):
        overrides["max_workers"] = args.workers
    if getattr(args, "verbose", False):
        overrides["log_level"] = "DEBUG"
    settings = get_settings()
    return settings.model_copy(update=overrides) if overrides else settings


def _brief_from_args(args: argparse.Namespace, console: Console) -> CampaignBrief:
    """Build a validated brief from CLI flags, prompting for any missing fields."""
    interactive = sys.stdin.isatty()
    values: dict[str, str] = {}
    missing: list[str] = []

    for dest, flag, label in _BRIEF_FIELDS:
        value = (getattr(args, dest, None) or "").strip()
        if not value and interactive:
            value = console.input(f"[bold]{label}[/bold]: ").strip()
        if not value:
            missing.append(flag)
        values[dest] = value

    channels = (getattr(args, "channels", None) or "").strip()
    if not channels and interactive:
        channels = console.input("[bold]Channels[/bold] (comma-separated): ").strip()
    if not channels:
        missing.append("--channels")

    if missing:
        raise ConfigurationError(
            "Missing required field(s): "
            + ", ".join(missing)
            + ". Provide the flag(s) or run in an interactive terminal."
        )

    try:
        return CampaignBrief(
            product_name=values["product_name"],
            product_description=values["description"],
            target_audience=values["audience"],
            goal=values["goal"],
            tone=values["tone"],
            channels=channels,
        )
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid campaign brief:\n{exc}") from exc


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _render_result(console: Console, result: CampaignResult) -> None:
    console.print(
        Panel(
            Markdown(result.final_brief),
            title=f"Campaign Brief - {result.brief.product_name}",
            border_style="cyan",
        )
    )
    if result.image_prompts:
        console.print("\n[bold]Image prompts[/bold]")
        for index, prompt in enumerate(result.image_prompts, start=1):
            console.print(f"  {index}. {prompt}")
    console.print(f"\n[dim]model={result.model} | {result.duration_seconds:.1f}s[/dim]")


def _render_batch_summary(console: Console, results: list[BatchItemResult]) -> None:
    table = Table(title="Batch summary", show_lines=False)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Product")
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", overflow="fold")

    for item in results:
        if item.ok:
            assert item.result is not None
            table.add_row(
                str(item.index + 1),
                item.brief.product_name,
                "[green]ok[/green]",
                f"{item.result.duration_seconds:.1f}s",
            )
        else:
            table.add_row(
                str(item.index + 1),
                item.brief.product_name,
                "[red]failed[/red]",
                item.error or "unknown error",
            )
    console.print(table)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace, console: Console) -> int:
    settings = _resolve_settings(args)
    configure_logging(settings.log_level)
    brief = _brief_from_args(args, console)

    workflow = CampaignWorkflow(settings)
    result = workflow.run(brief)

    saved_path: Path | None = None
    if not args.no_save:
        saved_path = result.save(settings.output_dir)

    if args.json:
        payload = result.to_dict()
        payload["saved_path"] = str(saved_path) if saved_path else None
        print(json.dumps(payload, indent=2))
    else:
        _render_result(console, result)
        if saved_path is not None:
            console.print(f"[green]Saved[/green] {saved_path}")
    return 0


def cmd_batch(args: argparse.Namespace, console: Console) -> int:
    settings = _resolve_settings(args)
    configure_logging(settings.log_level)
    briefs = load_briefs(args.input)

    lock = threading.Lock()
    completed = 0
    total = len(briefs)

    def on_result(item: BatchItemResult) -> None:
        nonlocal completed
        with lock:
            completed += 1
            status = "[green]ok[/green]" if item.ok else "[red]failed[/red]"
            if not args.json:
                console.print(
                    f"[dim]({completed}/{total})[/dim] {item.brief.product_name}: {status}"
                )

    workflow = CampaignWorkflow(settings)
    results = workflow.run_batch(briefs, max_workers=args.workers, on_result=on_result)

    saved: dict[int, Path] = {}
    if not args.no_save:
        for item in results:
            if item.ok and item.result is not None:
                saved[item.index] = item.result.save(settings.output_dir)

    if args.json:
        payload = [
            {
                "index": item.index,
                "product_name": item.brief.product_name,
                "ok": item.ok,
                "error": item.error,
                "saved_path": str(saved[item.index]) if item.index in saved else None,
                "result": item.result.to_dict() if item.result is not None else None,
            }
            for item in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        _render_batch_summary(console, results)
        if saved:
            console.print(f"[green]Saved {len(saved)} brief(s) to[/green] {settings.output_dir}")

    return 0 if all(item.ok for item in results) else 1


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    # Default to the `run` subcommand unless an explicit command or a top-level
    # help/version flag is given (preserves the interactive `python main.py` UX).
    top_level_flags = {"-h", "--help", "--version"}
    if not raw_args:
        raw_args = ["run"]
    elif raw_args[0] not in _KNOWN_COMMANDS and raw_args[0] not in top_level_flags:
        raw_args = ["run", *raw_args]

    parser = build_parser()
    args = parser.parse_args(raw_args)
    console = Console()

    try:
        exit_code: int = args.func(args, console)
    except ConfigurationError as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        return 2
    except CampaignForgeError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        return 130
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
