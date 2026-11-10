"""Loading user-supplied sources (files and URLs) and turning them into evidence."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from itertools import islice
from pathlib import Path

from ..domain.provenance import Evidence
from ..domain.sources import MAX_SOURCE_CHARS, SourceBundle, SourceDocument
from ..exceptions import ConfigurationError, ToolError
from .fetch import SafeFetcher

MAX_FILE_BYTES = 1_000_000
CSV_MAX_ROWS = 200
_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


def _clip(text: str) -> str:
    return text if len(text) <= MAX_SOURCE_CHARS else text[: MAX_SOURCE_CHARS - 3] + "..."


def _csv_text(raw: str) -> str:
    reader = csv.reader(io.StringIO(raw))
    rows = [", ".join(cell.strip() for cell in row) for row in islice(reader, CSV_MAX_ROWS)]
    return "\n".join(rows)


def load_source_file(path: Path) -> SourceDocument:
    """A source document from a ``.txt``, ``.md``, ``.csv`` or ``.json`` file.

    Raises:
        ConfigurationError: if the file is missing, too large, or an unsupported type.
    """
    suffix = path.suffix.lower()
    if suffix not in _TEXT_SUFFIXES | {".csv", ".json"}:
        raise ConfigurationError(
            f"{path}: unsupported source type {suffix!r} (txt, md, csv, json)."
        )
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ConfigurationError(f"{path} is larger than {MAX_FILE_BYTES} bytes.")
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ConfigurationError(f"Cannot read source {path}: {exc}") from exc
    if suffix == ".csv":
        text = _csv_text(raw)
    elif suffix == ".json":
        try:
            text = json.dumps(json.loads(raw), ensure_ascii=False, indent=1)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"{path} is not valid JSON: {exc}") from exc
    else:
        text = raw
    if not text.strip():
        raise ConfigurationError(f"{path} is empty.")
    return SourceDocument(name=path.name, content=_clip(text), origin=f"file:{path.name}")


def load_source_url(url: str, fetcher: SafeFetcher) -> SourceDocument:
    """A source document from a user-supplied URL (fetched with every guard on).

    Raises:
        ConfigurationError: wrapping the tool error, so the CLI reports it as user input.
    """
    try:
        final_url, title, text = fetcher.fetch_text(url)
    except ToolError as exc:
        raise ConfigurationError(f"Source {url}: {exc}") from exc
    return SourceDocument(name=title[:300], content=_clip(text), origin=final_url)


def load_sources(items: Iterable[str], fetcher: SafeFetcher | None = None) -> SourceBundle:
    """Build a bundle from a mix of file paths and http(s) URLs."""
    documents: list[SourceDocument] = []
    for item in items:
        if item.startswith(("http://", "https://")):
            documents.append(load_source_url(item, fetcher or SafeFetcher()))
        else:
            documents.append(load_source_file(Path(item)))
    return SourceBundle(documents=tuple(documents))


def bundle_evidence(bundle: SourceBundle) -> list[Evidence]:
    """Evidence records for every document in a bundle (deterministic ids)."""
    return [
        Evidence.create(
            source=doc.origin if doc.origin.startswith("http") else f"user:{doc.name}",
            title=doc.name,
            excerpt=doc.content[:8000],
        )
        for doc in bundle.documents
    ]
