"""CLI commands for the 2.0 engine: agents, workflows, execute and run management.

Runs started with ``execute`` are checkpointed after every step: to JSON files in
``CF_RUNS_DIR`` (default ``outputs/runs``), or to the SQLite file ``CF_MEMORY_DB``
when ``CF_RUN_STORE=sqlite``. They can be inspected, resumed after a pause,
approved, or cancelled from later CLI invocations. Completed runs are remembered
as campaign history (``CF_MEMORY_HISTORY``, on by default; see ``memory search``).

Exit codes for ``execute``, ``runs resume`` and ``runs approve``:

* ``0``: the run completed;
* ``1``: the run failed or was cancelled;
* ``3``: the run needs action (paused, or waiting for approval).

``2`` is reserved for configuration and validation errors, as in v1.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .cli_memory import build_memory, load_brand
from .config import Settings
from .evaluation.offline import OfflineSearch, offline_responder
from .exceptions import ConfigurationError, MemoryStoreError
from .loaders import load_briefs
from .orchestration.definition import WorkflowDefinition, load_definition, validate_definition
from .orchestration.engine import RunResult, WorkflowEngine
from .orchestration.history import remember_run
from .orchestration.state import RunRecord, RunStatus
from .orchestration.store import JsonFileRunStore, RunStore, SqliteRunStore
from .orchestration.templates import BUILTIN_TEMPLATES, default_registry, get_template
from .providers.fake import FakeProvider
from .providers.router import ProviderRouter
from .tools import ToolBox, build_toolbox, load_sources

EXIT_NEEDS_ACTION = 3
_STATUS_STYLE = {
    "succeeded": "green",
    "completed": "green",
    "running": "cyan",
    "pending": "dim",
    "skipped": "dim",
    "waiting_for_approval": "yellow",
    "paused": "yellow",
    "failed": "red",
    "cancelled": "red",
}


def build_engine(settings: Settings) -> WorkflowEngine:
    """The engine used by the CLI: built-in plus plugin agents, runs stored on disk.

    With ``CF_PROVIDER=fake`` every agent is answered by the deterministic offline
    responder: a key-free, network-free demonstration whose output is simulated.
    """
    router = ProviderRouter(settings)
    if router.global_provider == "fake":
        router.add_provider("fake", FakeProvider(offline_responder))
        tools = ToolBox([OfflineSearch()])
    else:
        tools = build_toolbox(settings)
    store: RunStore = (
        SqliteRunStore(settings.memory_db)
        if settings.run_store == "sqlite"
        else JsonFileRunStore(settings.runs_dir)
    )
    return WorkflowEngine(default_registry(load_plugins=True), router, store=store, tools=tools)


def add_engine_commands(subparsers: Any, common: Any) -> None:
    """Register the 2.0 subcommands. ``common`` adds the shared flags to a parser."""
    agents = subparsers.add_parser("agents", help="Inspect registered agents")
    agents_sub = agents.add_subparsers(dest="agents_command", required=True)
    agents_list = agents_sub.add_parser("list", help="List registered agents")
    common(agents_list)
    agents_list.set_defaults(func=cmd_agents_list, needs_settings=True)

    workflows = subparsers.add_parser("workflows", help="Inspect and validate workflows")
    wf_sub = workflows.add_subparsers(dest="workflows_command", required=True)
    wf_list = wf_sub.add_parser("list", help="List built-in workflow templates")
    common(wf_list)
    wf_list.set_defaults(func=cmd_workflows_list, needs_settings=True)
    wf_show = wf_sub.add_parser("show", help="Print a workflow definition as JSON")
    wf_show.add_argument("workflow", help="Template id or path to a JSON definition")
    common(wf_show)
    wf_show.set_defaults(func=cmd_workflows_show, needs_settings=True)
    wf_validate = wf_sub.add_parser("validate", help="Validate a JSON workflow definition")
    wf_validate.add_argument("workflow", help="Template id or path to a JSON definition")
    common(wf_validate)
    wf_validate.set_defaults(func=cmd_workflows_validate, needs_settings=True)

    execute = subparsers.add_parser("execute", help="Run a workflow with checkpoints")
    execute.add_argument("workflow", help="Template id or path to a JSON definition")
    execute.add_argument("--brief", type=Path, required=True, help="JSON file with one brief")
    execute.add_argument(
        "--auto-resume",
        type=int,
        default=0,
        metavar="N",
        help="Resume up to N times after a rate-limit pause (waits out the hint)",
    )
    execute.add_argument(
        "--brand",
        help="A stored brand name (see 'memory brand list') or a brand profile .json file",
    )
    execute.add_argument(
        "--sources",
        nargs="*",
        default=[],
        help="Your own sources: .txt/.md/.csv/.json files or http(s) URLs (fetched safely)",
    )
    common(execute)
    execute.set_defaults(func=cmd_execute, needs_settings=True)

    runs = subparsers.add_parser("runs", help="Inspect and control checkpointed runs")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    runs_list = runs_sub.add_parser("list", help="List stored runs")
    common(runs_list)
    runs_list.set_defaults(func=cmd_runs_list, needs_settings=True)
    for name, func, text in (
        ("show", cmd_runs_show, "Show a run"),
        ("cancel", cmd_runs_cancel, "Cancel a run"),
    ):
        sub = runs_sub.add_parser(name, help=text)
        sub.add_argument("run_id")
        common(sub)
        sub.set_defaults(func=func, needs_settings=True)
    resume = runs_sub.add_parser("resume", help="Resume a paused, failed or interrupted run")
    resume.add_argument("run_id")
    resume.add_argument("--rerun", nargs="*", default=[], help="Step ids to run again")
    common(resume)
    resume.set_defaults(func=cmd_runs_resume, needs_settings=True)
    approve = runs_sub.add_parser("approve", help="Approve (or reject) a waiting step")
    approve.add_argument("run_id")
    approve.add_argument("step_id")
    approve.add_argument("--reject", action="store_true", help="Reject instead of approve")
    approve.add_argument("--comment", help="Reviewer comment")
    common(approve)
    approve.set_defaults(func=cmd_runs_approve, needs_settings=True)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_agents_list(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    registry = default_registry(load_plugins=True)
    specs = registry.specs()
    if args.json:
        print(json.dumps([s.model_dump(mode="json") for s in specs], indent=2))
        return 0
    table = Table(title="Registered agents")
    table.add_column("Id", no_wrap=True)
    table.add_column("Category", no_wrap=True)
    table.add_column("Inputs", overflow="fold")
    table.add_column("Output", overflow="fold")
    table.add_column("Enabled", no_wrap=True)
    for spec in specs:
        table.add_row(
            spec.id,
            spec.category.value,
            ", ".join(i.name if i.required else f"[{i.name}]" for i in spec.inputs) or "-",
            spec.output_kind,
            "yes" if registry.is_enabled(spec.id) else "no",
        )
    console.print(table)
    return 0


def cmd_workflows_list(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    infos = sorted(BUILTIN_TEMPLATES.values(), key=lambda i: i.id)
    if args.json:
        print(
            json.dumps(
                [{"id": i.id, "name": i.name, "description": i.description} for i in infos],
                indent=2,
            )
        )
        return 0
    table = Table(title="Workflow templates")
    table.add_column("Id", no_wrap=True)
    table.add_column("Name", overflow="fold")
    table.add_column("Description", overflow="fold")
    for info in infos:
        table.add_row(info.id, escape(info.name), escape(info.description))
    console.print(table)
    return 0


def cmd_workflows_show(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    print(_resolve_workflow(args.workflow).model_dump_json(indent=2))
    return 0


def cmd_workflows_validate(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    definition = _resolve_workflow(args.workflow)
    validate_definition(definition, default_registry(load_plugins=True))
    console.print(
        f"[green]Valid:[/green] {definition.id}@{definition.version} "
        f"({len(definition.steps)} steps)"
    )
    return 0


def cmd_execute(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    definition = _resolve_workflow(args.workflow)
    briefs = load_briefs(args.brief)
    if len(briefs) != 1:
        raise ConfigurationError(
            f"{args.brief} holds {len(briefs)} briefs; 'execute' takes exactly one (use 'batch')."
        )
    inputs: dict[str, Any] = {"brief": briefs[0]}
    if args.sources:
        if definition.input_kind("sources") is None:
            raise ConfigurationError(f"Workflow {definition.id!r} does not accept --sources.")
        inputs["sources"] = load_sources(args.sources)
    if args.brand:
        if definition.input_kind("brand") is None:
            raise ConfigurationError(f"Workflow {definition.id!r} does not accept --brand.")
        inputs["brand"] = load_brand(args.brand, settings)
    engine = build_engine(settings)
    result = engine.start(definition, inputs)
    if args.auto_resume > 0:
        result = engine.run_until_settled(result, max_resumes=args.auto_resume)
    return _report(console, result, args.json, settings)


def cmd_runs_list(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    records = build_engine(settings).list_runs()
    if args.json:
        print(json.dumps([r.summary() for r in records], indent=2))
        return 0
    table = Table(title=f"Runs in {settings.runs_dir}")
    table.add_column("Run", no_wrap=True)
    table.add_column("Workflow", overflow="fold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Updated", overflow="fold")
    for record in records:
        table.add_row(
            record.run_id,
            record.definition.id,
            _styled(record.status.value),
            record.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
    console.print(table)
    return 0


def cmd_runs_show(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    record = build_engine(settings).get(args.run_id)
    _render(console, record, args.json)
    return 0


def cmd_runs_resume(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    result = build_engine(settings).resume(args.run_id, rerun=args.rerun)
    return _report(console, result, args.json, settings)


def cmd_runs_approve(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    result = build_engine(settings).approve(
        args.run_id, args.step_id, approved=not args.reject, comment=args.comment
    )
    return _report(console, result, args.json, settings)


def cmd_runs_cancel(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    record = build_engine(settings).cancel(args.run_id)
    _render(console, record, args.json)
    return 0


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _resolve_workflow(ref: str) -> WorkflowDefinition:
    path = Path(ref)
    if ref.endswith(".json") or path.is_file():
        return load_definition(path)
    return get_template(ref)


def _styled(status: str) -> str:
    style = _STATUS_STYLE.get(status, "white")
    return f"[{style}]{status}[/{style}]"


def _render(console: Console, record: RunRecord, as_json: bool) -> None:
    if as_json:
        payload = record.summary()
        payload["outputs"] = {k: a.data for k, a in record.outputs().items()}
        payload["steps_detail"] = {
            sid: s.model_dump(mode="json", exclude={"notes"}) for sid, s in record.steps.items()
        }
        print(json.dumps(payload, indent=2, default=str))
        return
    table = Table(title=f"Run {record.run_id} ({record.definition.id})")
    table.add_column("Step", no_wrap=True)
    table.add_column("Agent", overflow="fold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Tries", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    for step in record.definition.steps:
        state = record.steps[step.id]
        detail = state.error or state.detail or ""
        if state.review_score is not None:
            detail = f"review {state.review_score:.2f}. {detail}".strip()
        table.add_row(
            step.id, step.agent, _styled(state.status.value), str(state.attempts), escape(detail)
        )
    console.print(table)
    usage = record.usage
    tokens = f"{usage.total_tokens}" + ("" if usage.complete else "+ (partly unreported)")
    line = f"Status: {_styled(record.status.value)}"
    if record.status_reason:
        line += f" - {escape(record.status_reason)}"
    console.print(f"{line} | LLM calls: {record.llm_calls} | tokens: {tokens}")
    if record.resume_after:
        console.print(
            f"[yellow]Provider suggests resuming after ~{record.resume_after:.0f}s.[/yellow]"
        )


def _remember(console: Console, record: RunRecord, settings: Settings, quiet: bool) -> None:
    """Add a completed run to campaign history. A memory problem never fails the run."""
    if not settings.memory_history or record.status is not RunStatus.COMPLETED:
        return
    try:
        with build_memory(settings) as memory:
            document = remember_run(memory, record)
    except MemoryStoreError as exc:
        if not quiet:
            reason = escape(str(exc))
            console.print(f"[yellow]Not remembered in campaign history:[/yellow] {reason}")
        return
    if document is not None and not quiet:
        console.print(f"[dim]Remembered in campaign history ({document.id}).[/dim]")


def _report(console: Console, result: RunResult, as_json: bool, settings: Settings) -> int:
    record = result.record
    _render(console, record, as_json)
    _remember(console, record, settings, quiet=as_json)
    if not as_json:
        if any(e.data.get("provider") == "fake" for e in record.events):
            console.print("[yellow]SIMULATED: offline responder output, not a real model.[/yellow]")
        outputs = record.outputs()
        final = outputs.get("final_brief") or outputs.get("report")
        text = (final.data.get("text") or final.data.get("markdown")) if final else None
        if isinstance(text, str):
            console.print(Panel(Markdown(text), title="Final brief", border_style="cyan"))
        console.print(f"[dim]Checkpoint: {settings.runs_dir} (run {record.run_id})[/dim]")
        if record.status is RunStatus.PAUSED:
            console.print(f"Resume with: campaign-forge runs resume {record.run_id}")
        if record.status is RunStatus.WAITING_FOR_APPROVAL:
            console.print(f"Approve with: campaign-forge runs approve {record.run_id} <step>")
    if record.status is RunStatus.COMPLETED:
        return 0
    if record.status in (RunStatus.PAUSED, RunStatus.WAITING_FOR_APPROVAL):
        return EXIT_NEEDS_ACTION
    return 1
