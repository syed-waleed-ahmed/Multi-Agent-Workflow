"""Workflow definitions a caller can run: the built-in templates plus their own.

Custom definitions are validated against the agent registry before they are
stored, belong to one scope, cannot reuse a built-in id, and are capped per
scope. They live in process memory: a restart forgets them (documented in
docs/API.md); the JSON can always be posted again.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal

from ..core.registry import AgentRegistry
from ..domain.scope import Scope
from ..exceptions import CapacityError, ConfigurationError, NotFoundError
from ..orchestration.definition import WorkflowDefinition, validate_definition
from ..orchestration.templates import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE, get_template

MAX_CUSTOM_WORKFLOWS = 50
#: The workflow a campaign request runs when it names none.
DEFAULT_CAMPAIGN = DEFAULT_TEMPLATE

WorkflowSource = Literal["builtin", "custom"]


@dataclass(frozen=True)
class CatalogEntry:
    definition: WorkflowDefinition
    source: WorkflowSource


class WorkflowCatalog:
    def __init__(self, registry: AgentRegistry, *, limit: int = MAX_CUSTOM_WORKFLOWS) -> None:
        self._registry = registry
        self._limit = limit
        self._custom: dict[Scope, dict[str, WorkflowDefinition]] = {}
        self._lock = threading.Lock()

    def save(self, scope: Scope, definition: WorkflowDefinition) -> CatalogEntry:
        """Validate and store (or replace) a custom definition for ``scope``.

        Raises:
            ConfigurationError: if it reuses a built-in id.
            WorkflowValidationError: if it is invalid for the registry.
            CapacityError: if the scope already stores the maximum.
        """
        if definition.id in BUILTIN_TEMPLATES:
            raise ConfigurationError(
                f"{definition.id!r} is a built-in workflow id; choose another id."
            )
        validate_definition(definition, self._registry)
        with self._lock:
            own = self._custom.setdefault(scope, {})
            if definition.id not in own and len(own) >= self._limit:
                raise CapacityError(
                    f"This key already stores {len(own)} workflows (the limit is {self._limit})."
                )
            own[definition.id] = definition
        return CatalogEntry(definition, "custom")

    def get(self, scope: Scope, workflow_id: str) -> CatalogEntry:
        """A built-in template or one of ``scope``'s own definitions.

        Raises:
            NotFoundError: if neither exists.
        """
        if workflow_id in BUILTIN_TEMPLATES:
            return CatalogEntry(get_template(workflow_id), "builtin")
        with self._lock:
            definition = self._custom.get(scope, {}).get(workflow_id)
        if definition is None:
            raise NotFoundError(f"Workflow {workflow_id!r} not found.")
        return CatalogEntry(definition, "custom")

    def entries(self, scope: Scope) -> list[CatalogEntry]:
        builtin = [CatalogEntry(info.build(), "builtin") for info in BUILTIN_TEMPLATES.values()]
        with self._lock:
            own = sorted(self._custom.get(scope, {}).values(), key=lambda d: d.id)
        return builtin + [CatalogEntry(d, "custom") for d in own]
