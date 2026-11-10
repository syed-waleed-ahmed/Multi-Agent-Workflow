"""CLI commands for memory: brand profiles, notes, campaign history search, and forgetting.

Memory lives in ``CF_MEMORY_DB`` (default ``outputs/campaign_forge.db``), in the
local scope. Every string is redacted before it is stored (ADR-031).

``memory forget`` deletes everything in the scope and is destructive, so it
refuses to run without ``--yes`` and only reports what it would delete.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .config import Settings
from .domain.brand import BRAND_KIND, BrandProfile
from .domain.scope import Scope
from .exceptions import ConfigurationError, MemoryNotFoundError
from .memory.base import MEMORY_KINDS
from .memory.sqlite import SqliteMemoryStore

_SCOPE = Scope.local()


def build_memory(settings: Settings) -> SqliteMemoryStore:
    """The CLI's memory store: one SQLite file, redacting every configured secret."""
    return SqliteMemoryStore(settings.memory_db, secrets=settings.secret_values())


def load_brand(value: str, settings: Settings) -> BrandProfile:
    """A brand profile from a JSON file path, else by name from memory."""
    path = Path(value)
    if value.endswith(".json") or path.is_file():
        try:
            return BrandProfile.model_validate_json(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(f"Cannot read brand file {value}: {exc}") from exc
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "profile"
            message = first["msg"]
            raise ConfigurationError(f"Invalid brand file {value} ({where}): {message}") from exc
    with build_memory(settings) as memory:
        try:
            return memory.get_brand(_SCOPE, value)
        except MemoryNotFoundError:
            raise ConfigurationError(
                f"No brand named {value!r} in memory. List brands with "
                "'campaign-forge memory brand list', or pass a .json file."
            ) from None


def add_memory_commands(subparsers: Any, common: Any) -> None:
    memory = subparsers.add_parser("memory", help="Brand profiles, notes and campaign history")
    sub = memory.add_subparsers(dest="memory_command", required=True)

    brand = sub.add_parser("brand", help="Manage stored brand profiles")
    brand_sub = brand.add_subparsers(dest="brand_command", required=True)
    save = brand_sub.add_parser("save", help="Store a brand profile (from a file or a run)")
    source = save.add_mutually_exclusive_group(required=True)
    source.add_argument("file", nargs="?", type=Path, help="JSON file with a brand profile")
    source.add_argument("--run", dest="run_id", help="Take the brand profile a run produced")
    common(save)
    save.set_defaults(func=cmd_brand_save, needs_settings=True)
    brand_list = brand_sub.add_parser("list", help="List stored brands")
    common(brand_list)
    brand_list.set_defaults(func=cmd_brand_list, needs_settings=True)
    for name, func, text in (
        ("show", cmd_brand_show, "Show a stored brand profile"),
        ("delete", cmd_brand_delete, "Delete a stored brand profile"),
    ):
        command = brand_sub.add_parser(name, help=text)
        command.add_argument("name")
        common(command)
        command.set_defaults(func=func, needs_settings=True)

    note = sub.add_parser("note", help="Store a note in memory")
    note.add_argument("text", help="The note")
    note.add_argument("--title", help="Title (default: the first words of the note)")
    common(note)
    note.set_defaults(func=cmd_note, needs_settings=True)

    search = sub.add_parser("search", help="Search campaign history and notes")
    search.add_argument("query")
    search.add_argument("--kind", choices=MEMORY_KINDS, help="Only this kind of document")
    search.add_argument("--limit", type=int, default=5, help="Maximum results (1-20)")
    common(search)
    search.set_defaults(func=cmd_search, needs_settings=True)

    listing = sub.add_parser("list", help="List stored documents, newest first")
    listing.add_argument("--kind", choices=MEMORY_KINDS, help="Only this kind of document")
    listing.add_argument("--limit", type=int, default=20, help="Maximum documents")
    common(listing)
    listing.set_defaults(func=cmd_list, needs_settings=True)

    forget = sub.add_parser("forget", help="Delete everything stored in memory")
    forget.add_argument("--yes", action="store_true", help="Confirm the deletion")
    forget.add_argument("--runs", action="store_true", help="Also delete stored runs")
    common(forget)
    forget.set_defaults(func=cmd_forget, needs_settings=True)


# --------------------------------------------------------------------------- #
# Brands
# --------------------------------------------------------------------------- #
def cmd_brand_save(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    if args.run_id:
        from .cli_runs import build_engine

        record = build_engine(settings).get(args.run_id)
        found = [a for a in record.outputs().values() if a.kind == BRAND_KIND]
        if not found:
            raise ConfigurationError(f"Run {args.run_id} has no brand profile output.")
        profile = found[0].as_model(BrandProfile)
    else:
        profile = load_brand(str(args.file), settings)
    with build_memory(settings) as memory:
        stored = memory.save_brand(_SCOPE, profile)
    if args.json:
        print(stored.model_dump_json(indent=2))
    else:
        console.print(f"[green]Saved brand[/green] {escape(stored.name)} ({stored.slug})")
    return 0


def cmd_brand_list(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    with build_memory(settings) as memory:
        brands = memory.list_brands(_SCOPE)
    if args.json:
        print(json.dumps([b.model_dump(mode="json") for b in brands], indent=2))
        return 0
    table = Table(title="Stored brands")
    table.add_column("Name", overflow="fold")
    table.add_column("Key", no_wrap=True)
    table.add_column("Tone", overflow="fold")
    table.add_column("Banned terms", no_wrap=True)
    for brand in brands:
        table.add_row(
            escape(brand.name),
            brand.slug,
            escape(", ".join(brand.tone_words)),
            str(len(brand.banned_terms)),
        )
    console.print(table)
    return 0


def cmd_brand_show(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    with build_memory(settings) as memory:
        brand = memory.get_brand(_SCOPE, args.name)
    if args.json:
        print(brand.model_dump_json(indent=2))
    else:
        console.print(Panel(Markdown(brand.to_markdown()), title=escape(brand.name)))
    return 0


def cmd_brand_delete(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    with build_memory(settings) as memory:
        memory.delete_brand(_SCOPE, args.name)
    console.print(f"Deleted brand {escape(args.name)}")
    return 0


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
def cmd_note(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    title = args.title or " ".join(args.text.split()[:8]) or "Note"
    with build_memory(settings) as memory:
        document = memory.add_document(_SCOPE, "note", title, args.text)
    if args.json:
        print(document.model_dump_json(indent=2))
    else:
        console.print(f"[green]Stored note[/green] {document.id}: {escape(document.title)}")
    return 0


def cmd_search(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    kinds = (args.kind,) if args.kind else MEMORY_KINDS
    with build_memory(settings) as memory:
        hits = memory.search(_SCOPE, args.query, kinds=kinds, limit=args.limit)
        engine = "full-text" if memory.full_text else "keyword"
    if args.json:
        print(json.dumps([h.model_dump(mode="json") for h in hits], indent=2))
        return 0
    if not hits:
        console.print(f"No matches for {escape(args.query)!r}.")
        return 0
    table = Table(title=f"Memory search ({engine}): {escape(args.query)}")
    table.add_column("Id", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Title", overflow="fold")
    table.add_column("Created", no_wrap=True)
    for hit in hits:
        doc = hit.document
        table.add_row(
            doc.id, doc.kind, escape(doc.title), doc.created_at.strftime("%Y-%m-%d %H:%M")
        )
    console.print(table)
    return 0


def cmd_list(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    with build_memory(settings) as memory:
        documents = memory.list_documents(_SCOPE, kind=args.kind, limit=args.limit)
    if args.json:
        print(json.dumps([d.model_dump(mode="json") for d in documents], indent=2))
        return 0
    table = Table(title=f"Memory in {settings.memory_db}")
    table.add_column("Id", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Title", overflow="fold")
    table.add_column("Created", no_wrap=True)
    for doc in documents:
        table.add_row(
            doc.id, doc.kind, escape(doc.title), doc.created_at.strftime("%Y-%m-%d %H:%M")
        )
    console.print(table)
    return 0


def cmd_forget(args: argparse.Namespace, console: Console, settings: Settings) -> int:
    from .cli_runs import build_engine

    with build_memory(settings) as memory:
        counts = memory.counts(_SCOPE)
        engine = build_engine(settings) if args.runs else None
        runs = engine.list_runs(scope=_SCOPE) if engine is not None else []
        summary = ", ".join(f"{n} {kind}" for kind, n in counts.items())
        if args.runs:
            summary += f", {len(runs)} run(s)"
        if not args.yes:
            console.print(
                f"[yellow]Would delete:[/yellow] {summary}. Nothing was deleted; "
                "re-run with --yes to confirm."
            )
            return 1
        removed = memory.delete_scope(_SCOPE)
    if engine is not None:
        for record in runs:
            engine.store.delete(record.run_id, _SCOPE)
    if args.json:
        print(json.dumps({"memory_records": removed, "runs": len(runs)}))
    else:
        console.print(f"[green]Deleted[/green] {removed} memory record(s) and {len(runs)} run(s).")
    return 0
