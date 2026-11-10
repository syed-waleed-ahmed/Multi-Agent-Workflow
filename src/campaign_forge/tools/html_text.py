"""Readable text from HTML with the standard library only (no parser dependency).

The extraction keeps titles, headings, paragraphs and list items. It drops
scripts, styles, navigation chrome and hidden template content. It is good enough
to give a model grounded excerpts; it is not a full reader view. TECH_STACK.md
names ``trafilatura`` as the upgrade path if evals show it is needed.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "nav", "footer"})
_BLOCK = frozenset(
    {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}
)
_SPACES = re.compile(r"[ \t\r\f\v]+")
_LINES = re.compile(r"\n\s*\n+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return ``(title, text)`` extracted from an HTML document."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = _SPACES.sub(" ", "".join(parser.parts))
    text = _LINES.sub("\n\n", "\n".join(line.strip() for line in text.splitlines()))
    return " ".join(parser.title.split()), text.strip()
