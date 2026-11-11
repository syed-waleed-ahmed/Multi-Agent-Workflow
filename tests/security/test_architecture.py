"""Architecture rules, enforced in CI with the stdlib ``ast`` module (no extra dependency).

1. Imports point downward only (docs/ARCHITECTURE.md, T3). A module may import
   from its own layer or a lower one, never a higher one.
2. The package never evaluates code or spawns processes: no ``eval``/``exec``/
   ``compile``/``__import__`` calls, no ``os.system``/``os.popen``, and no imports
   of ``subprocess``, ``pickle`` or ``marshal`` (docs/SECURITY.md, T10).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "campaign_forge"

# Lower number = lower layer. Longest matching prefix wins.
LAYERS: dict[str, int] = {
    "campaign_forge.exceptions": 0,
    "campaign_forge.parsing": 0,
    "campaign_forge.security": 0,
    "campaign_forge.domain": 0,
    "campaign_forge.models": 0,
    "campaign_forge.logging_config": 0,
    "campaign_forge.config": 1,
    "campaign_forge.loaders": 1,
    "campaign_forge.providers": 2,
    "campaign_forge.tools": 2,
    "campaign_forge.llm": 2,
    "campaign_forge.memory": 2,
    "campaign_forge.core": 3,
    "campaign_forge.agents": 4,
    "campaign_forge.evaluation": 4,
    "campaign_forge.orchestration": 5,
    "campaign_forge.workflow": 5,
    "campaign_forge.services": 5,
    "campaign_forge.exports": 5,
    "campaign_forge.cli": 6,
    "campaign_forge.ui": 6,
    "campaign_forge.api": 6,
    "campaign_forge.__main__": 6,
    "campaign_forge": 6,
}

FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_OS_ATTRS = {"system", "popen"}
FORBIDDEN_MODULES = {"subprocess", "pickle", "marshal"}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _layer(module: str) -> int | None:
    matches = [key for key in LAYERS if module == key or module.startswith(key + ".")]
    return LAYERS[max(matches, key=len)] if matches else None


def _resolve(node: ast.ImportFrom, importer: str, is_package: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = importer.split(".")
    if not is_package:
        base = base[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _sources() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def test_resolver_and_layers_are_not_vacuous() -> None:
    def first_import(source: str) -> ast.ImportFrom:
        node = ast.parse(source).body[0]
        assert isinstance(node, ast.ImportFrom)
        return node

    assert _resolve(first_import("from ..llm import X"), "campaign_forge.agents.base", False) == (
        "campaign_forge.llm"
    )
    assert _resolve(first_import("from .base import X"), "campaign_forge.core", True) == (
        "campaign_forge.core.base"
    )
    assert _layer("campaign_forge.providers.router") == 2
    assert _layer("campaign_forge.domain.usage") == 0
    assert _layer("campaign_forge") == 6


def test_every_module_has_a_layer() -> None:
    unmapped = [_module_name(p) for p in _sources() if _layer(_module_name(p)) is None]
    assert unmapped == []


@pytest.mark.parametrize("path", _sources(), ids=lambda p: _module_name(p))
def test_imports_point_downward(path: Path) -> None:
    importer = _module_name(path)
    importer_layer = _layer(importer)
    assert importer_layer is not None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.ImportFrom):
            targets = [_resolve(node, importer, path.name == "__init__.py")]
        elif isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        for target in targets:
            if not target.startswith("campaign_forge"):
                continue
            target_layer = _layer(target)
            if target_layer is not None and target_layer > importer_layer:
                violations.append(f"{importer} (layer {importer_layer}) imports {target}")
    assert violations == []


@pytest.mark.parametrize("path", _sources(), ids=lambda p: _module_name(p))
def test_no_code_execution_or_process_spawning(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                problems.append(f"call to {func.id}()")
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr in FORBIDDEN_OS_ATTRS
            ):
                problems.append(f"call to os.{func.attr}()")
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        problems.extend(f"import of {n}" for n in names if n in FORBIDDEN_MODULES)
    assert problems == []


#: Optional-extra libraries and the only modules allowed to import them, so the core
#: package (library and CLI) works without the extra installed.
EXTRA_IMPORTS: dict[str, set[str]] = {
    "streamlit": {"campaign_forge.ui.pages"},
    "fastapi": {"campaign_forge.api.app"},
    "starlette": {"campaign_forge.api.app"},
    "uvicorn": {"campaign_forge.api.__main__"},
}


def test_optional_extras_stay_out_of_the_core() -> None:
    importers: dict[str, set[str]] = {extra: set() for extra in EXTRA_IMPORTS}
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in importers:
                    importers[name].add(_module_name(path))
    assert importers == EXTRA_IMPORTS


#: What the API layer may import: the service layer and plain types, never the engine,
#: agents, providers, tools or memory. Business logic therefore cannot live in the API.
API_ALLOWED_PREFIXES = (
    "campaign_forge.api",
    "campaign_forge.services",
    "campaign_forge.config",
    "campaign_forge.exceptions",
    "campaign_forge.models",
    "campaign_forge.domain",
    "campaign_forge.security",
    "campaign_forge.exports",
    "campaign_forge.logging_config",
    "campaign_forge.core.agent",
    "campaign_forge.orchestration.definition",
    "campaign_forge.orchestration.state",
)


def test_the_api_holds_no_business_logic() -> None:
    api_files = sorted((PACKAGE_ROOT / "api").glob("*.py"))
    assert api_files
    problems = []
    for path in api_files:
        importer = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolve(node, importer, path.name == "__init__.py")
            if not target.startswith("campaign_forge"):
                continue
            allowed = target == "campaign_forge" or any(
                target == prefix or target.startswith(prefix + ".")
                for prefix in API_ALLOWED_PREFIXES
            )
            if not allowed:
                problems.append(f"{importer} imports {target}")
    assert problems == []
