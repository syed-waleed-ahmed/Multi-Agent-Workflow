"""Declarative workflow definitions: a validated DAG of agent steps.

A workflow is data, not code. It names its inputs, its steps (agent, input
bindings, conditions, review, approval, retries), its outputs and its limits.
Built-in templates are written in Python, and user workflows are stored as JSON
(ADR-015).

**References** connect the parts:

* ``"input:<name>"`` refers to a workflow input artifact;
* ``"step:<id>"`` refers to the artifact produced by an earlier step.

**Two levels of validation:**

* :class:`WorkflowDefinition` validates its own structure on construction:
  unique ids, resolvable references and no cycles.
* :func:`validate_definition` validates against an agent registry: agents exist
  and are enabled, required inputs are bound, artifact kinds match, and review
  steps are wired correctly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from ..core.agent import AGENT_ID_PATTERN
from ..core.budget import BudgetLimits
from ..core.registry import AgentRegistry
from ..domain.artifacts import ARTIFACT_KIND_PATTERN, Artifact
from ..domain.review import REVIEW_FEEDBACK_INPUT, REVIEW_KIND
from ..exceptions import RegistryError, WorkflowValidationError

IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]*$"
_REF_RE = re.compile(r"^(input|step):([a-z][a-z0-9_]*)$")

Identifier = Annotated[str, StringConstraints(pattern=IDENTIFIER_PATTERN, max_length=60)]
Reference = Annotated[str, StringConstraints(pattern=_REF_RE.pattern)]
ArtifactKind = Annotated[str, StringConstraints(pattern=ARTIFACT_KIND_PATTERN)]
AgentId = Annotated[str, StringConstraints(pattern=AGENT_ID_PATTERN, max_length=100)]

ConditionOp = Literal[
    "exists", "not_exists", "equals", "not_equals", "contains", "not_contains", "truthy", "falsy"
]


def parse_ref(ref: str) -> tuple[str, str]:
    """Split ``"input:brief"`` into ``("input", "brief")``."""
    match = _REF_RE.match(ref)
    if not match:
        raise ValueError(f"Invalid reference {ref!r}; expected 'input:<name>' or 'step:<id>'.")
    return match.group(1), match.group(2)


class Condition(BaseModel):
    """A declarative predicate on an input or step artifact. It is never ``eval``-ed.

    ``path`` is a dotted path into the artifact's JSON data. String comparisons
    for ``contains`` are case-insensitive. A missing artifact or path counts as
    "not present".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Reference
    path: str = Field(default="", pattern=r"^([A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*)?$")
    op: ConditionOp
    value: str | int | float | bool | None = None

    def evaluate(self, artifact: Artifact | None) -> bool:
        present, found = _lookup(artifact, self.path)
        if self.op == "exists":
            return present
        if self.op == "not_exists":
            return not present
        if self.op == "truthy":
            return present and bool(found)
        if self.op == "falsy":
            return not (present and bool(found))
        if self.op in ("equals", "not_equals"):
            equal = present and found == self.value
            return equal if self.op == "equals" else not equal
        contained = present and _contains(found, self.value)
        return contained if self.op == "contains" else not contained


def _lookup(artifact: Artifact | None, path: str) -> tuple[bool, Any]:
    if artifact is None:
        return False, None
    current: Any = artifact.data
    for part in [p for p in path.split(".") if p]:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _contains(container: Any, value: Any) -> bool:
    needle = value.casefold() if isinstance(value, str) else value
    if isinstance(container, str):
        return isinstance(needle, str) and needle in container.casefold()
    if isinstance(container, (list, tuple)):
        return any((i.casefold() if isinstance(i, str) else i) == needle for i in container)
    if isinstance(container, Mapping):
        return needle in container
    return False


class ReviewSpec(BaseModel):
    """Bounded critic review of a step's output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    critic: AgentId
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    max_revisions: int = Field(default=1, ge=0, le=3)


class StepDefinition(BaseModel):
    """One agent invocation inside a workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    agent: AgentId
    inputs: dict[Identifier, Reference] = Field(default_factory=dict)
    after: tuple[Identifier, ...] = ()
    when: tuple[Condition, ...] = ()
    review: ReviewSpec | None = None
    approval: bool = False
    retries: int = Field(default=0, ge=0, le=2)
    timeout_s: float | None = Field(default=None, gt=0, le=3600)
    optional: bool = False
    enabled: bool = True

    def dependencies(self) -> set[str]:
        """Step ids this step must wait for (data, ordering and condition sources)."""
        deps = set(self.after)
        refs = [*self.inputs.values(), *(c.source for c in self.when)]
        for ref in refs:
            kind, name = parse_ref(ref)
            if kind == "step":
                deps.add(name)
        return deps


class WorkflowDefinition(BaseModel):
    """A complete, structurally valid workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=60)
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(default="", max_length=1000)
    inputs: dict[Identifier, ArtifactKind]
    #: Inputs a run may omit (e.g. user-supplied sources). Steps that bind them to a
    #: *required* agent input are rejected by validation.
    optional_inputs: dict[Identifier, ArtifactKind] = Field(default_factory=dict)
    steps: tuple[StepDefinition, ...] = Field(min_length=1, max_length=50)
    outputs: dict[Identifier, Reference] = Field(default_factory=dict)
    limits: BudgetLimits = BudgetLimits()
    max_parallel: int = Field(default=3, ge=1, le=8)

    @model_validator(mode="after")
    def _check_structure(self) -> WorkflowDefinition:
        problems: list[str] = []
        ids = [s.id for s in self.steps]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            problems.append(f"duplicate step ids: {', '.join(duplicates)}")
        known = set(ids)

        overlap = sorted(set(self.inputs) & set(self.optional_inputs))
        if overlap:
            problems.append(f"inputs declared both required and optional: {', '.join(overlap)}")

        def check_ref(ref: str, where: str) -> None:
            kind, name = parse_ref(ref)
            if kind == "input" and name not in self.inputs and name not in self.optional_inputs:
                problems.append(f"{where} refers to unknown input {name!r}")
            if kind == "step" and name not in known:
                problems.append(f"{where} refers to unknown step {name!r}")

        for step in self.steps:
            for input_name, ref in step.inputs.items():
                check_ref(ref, f"step {step.id!r} input {input_name!r}")
            for condition in step.when:
                check_ref(condition.source, f"step {step.id!r} condition")
            for dep in step.after:
                if dep not in known:
                    problems.append(f"step {step.id!r} waits for unknown step {dep!r}")
            if step.id in step.dependencies():
                problems.append(f"step {step.id!r} depends on itself")
            if not step.enabled and not step.optional:
                problems.append(f"step {step.id!r} is disabled but not optional")
        for name, ref in self.outputs.items():
            check_ref(ref, f"output {name!r}")
        if not problems:
            cycle = _find_cycle({s.id: s.dependencies() for s in self.steps})
            if cycle:
                problems.append("dependency cycle: " + " -> ".join(cycle))
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def input_kind(self, name: str) -> str | None:
        """The artifact kind of a required or optional workflow input."""
        return self.inputs.get(name) or self.optional_inputs.get(name)

    def step(self, step_id: str) -> StepDefinition:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)

    def topological_order(self) -> list[str]:
        """Step ids in a dependency-respecting order (stable for equal ranks)."""
        deps = {s.id: s.dependencies() for s in self.steps}
        order: list[str] = []
        remaining = [s.id for s in self.steps]
        while remaining:
            ready = [s for s in remaining if deps[s] <= set(order)]
            order.extend(ready)
            remaining = [s for s in remaining if s not in ready]
        return order

    def descendants(self, step_ids: Iterable[str]) -> set[str]:
        """All steps that (transitively) depend on any of ``step_ids``."""
        targets = set(step_ids)
        result: set[str] = set()
        changed = True
        while changed:
            changed = False
            for step in self.steps:
                if step.id not in result and step.dependencies() & (targets | result):
                    result.add(step.id)
                    changed = True
        return result

    def with_disabled(self, step_ids: Iterable[str]) -> WorkflowDefinition:
        """A copy with the given optional steps disabled (used by planners)."""
        disabled = set(step_ids)
        steps = tuple(
            s.model_copy(update={"enabled": False}) if s.id in disabled else s for s in self.steps
        )
        return self.model_copy(update={"steps": steps})


def _find_cycle(graph: Mapping[str, set[str]]) -> list[str] | None:
    visiting: set[str] = set()
    done: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        visiting.add(node)
        path.append(node)
        for dep in sorted(graph.get(node, set())):
            if dep in visiting:
                return [*path[path.index(dep) :], dep]
            if dep not in done:
                found = visit(dep)
                if found:
                    return found
        visiting.discard(node)
        done.add(node)
        path.pop()
        return None

    for node in sorted(graph):
        if node not in done:
            found = visit(node)
            if found:
                return found
    return None


def validate_definition(definition: WorkflowDefinition, registry: AgentRegistry) -> None:
    """Check a definition against the registry.

    Raises:
        WorkflowValidationError: listing every problem found.
    """
    problems: list[str] = []
    output_kinds: dict[str, str] = {}

    def enabled_spec(agent_id: str, where: str) -> Any:
        try:
            spec = registry.spec(agent_id)
        except RegistryError:
            problems.append(f"{where}: unknown agent {agent_id!r}")
            return None
        if not registry.is_enabled(agent_id):
            problems.append(f"{where}: agent {agent_id!r} is disabled")
        return spec

    for step in definition.steps:
        spec = enabled_spec(step.agent, f"step {step.id!r}")
        if spec is not None:
            output_kinds[step.id] = spec.output_kind

    for step in definition.steps:
        if step.id not in output_kinds:
            continue
        spec = registry.spec(step.agent)
        declared = {i.name: i for i in spec.inputs}
        for name in step.inputs:
            if name not in declared:
                problems.append(f"step {step.id!r}: agent {spec.id!r} has no input {name!r}")
        for item in spec.required_inputs:
            if item.name not in step.inputs:
                problems.append(f"step {step.id!r}: required input {item.name!r} is not bound")
        for name, ref in step.inputs.items():
            if name not in declared:
                continue
            kind, source = parse_ref(ref)
            actual = definition.input_kind(source) if kind == "input" else output_kinds.get(source)
            if kind == "input" and source in definition.optional_inputs and declared[name].required:
                problems.append(
                    f"step {step.id!r}: required input {name!r} is bound to optional workflow "
                    f"input {source!r}"
                )
            if actual is not None and actual != declared[name].kind:
                problems.append(
                    f"step {step.id!r} input {name!r} expects {declared[name].kind}, "
                    f"but {ref} provides {actual}"
                )
        if step.review is not None:
            critic = enabled_spec(step.review.critic, f"step {step.id!r} review")
            if critic is not None and critic.output_kind != REVIEW_KIND:
                problems.append(
                    f"step {step.id!r}: critic {critic.id!r} must produce {REVIEW_KIND}"
                )
            if critic is not None:
                for item in critic.inputs:
                    bound = step.inputs.get(item.name)
                    if bound is None:
                        if item.required:
                            problems.append(
                                f"step {step.id!r}: critic {critic.id!r} needs input "
                                f"{item.name!r}, which the step does not bind"
                            )
                        continue
                    kind, source = parse_ref(bound)
                    actual = (
                        definition.input_kind(source)
                        if kind == "input"
                        else output_kinds.get(source)
                    )
                    if actual is not None and actual != item.kind:
                        problems.append(
                            f"step {step.id!r}: critic input {item.name!r} expects {item.kind}, "
                            f"but {bound} provides {actual}"
                        )
            feedback = declared.get(REVIEW_FEEDBACK_INPUT)
            if feedback is None or feedback.kind != REVIEW_KIND or feedback.required:
                problems.append(
                    f"step {step.id!r}: agent {spec.id!r} needs an optional "
                    f"{REVIEW_FEEDBACK_INPUT!r} input of kind {REVIEW_KIND} to be reviewed"
                )
    if problems:
        raise WorkflowValidationError(problems)


def load_definition(source: str | Path) -> WorkflowDefinition:
    """Load a workflow definition from a JSON file path or a JSON string.

    Raises:
        WorkflowValidationError: if the JSON is unreadable or the definition is invalid.
    """
    try:
        text = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else source
        return WorkflowDefinition.model_validate_json(text)
    except OSError as exc:
        raise WorkflowValidationError([f"cannot read {source}: {exc}"]) from exc
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'workflow'}: {e['msg']}" for e in exc.errors()
        ]
        raise WorkflowValidationError(problems) from exc
