"""The Streamlit pages (the ``[ui]`` extra). Imported only by the app in ``app/``.

Layout only: state and data come from :mod:`.session` and :mod:`.views`.

Rules every page follows:

* **Demo output is labeled SIMULATED** wherever it appears.
* **Nothing runs without approval.** New Run shows the plan (graph, steps,
  budget) and starts only on an explicit "Approve and run".
* **No hidden reasoning.** Pages show validated artifacts, code-written notes and
  error messages, never a model's private reasoning (providers strip it).
* **Model text is rendered without raw HTML.** Streamlit's Markdown escapes
  HTML; the HTML export disables it too.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

import streamlit as st
from pydantic import ValidationError

from .. import __version__
from ..core.budget import BudgetLimits
from ..domain.brand import BRAND_KIND, BrandProfile
from ..exceptions import CampaignForgeError, MemoryNotFoundError, WorkflowValidationError
from ..exports import SIMULATED_NOTICE, artifact_markdown, is_simulated, run_html, run_json
from ..exports import run_markdown as export_markdown
from ..models import CampaignBrief
from ..orchestration.definition import WorkflowDefinition, load_definition
from ..orchestration.state import RunRecord, RunStatus, StepStatus
from ..orchestration.templates import BUILTIN_TEMPLATES, get_template
from .session import AppSession, build_session
from .views import (
    EXAMPLE_BRIEF,
    STUDIOS,
    STUDIOS_BY_ID,
    Studio,
    agent_rows,
    claim_rows,
    customize,
    dag_dot,
    errors_of,
    evidence_rows,
    health_rows,
    parse_brand_json,
    parse_brief_json,
    plan_rows,
    provenance,
    run_rows,
    split_terms,
    step_rows,
    template_ids,
    text_sources,
    usage_summary,
)

SESSION_KEY = "campaign_forge_session"
SELECTED_RUN = "campaign_forge_selected_run"
CUSTOM_DEFINITION = "campaign_forge_custom_definition"
_CUSTOM = "custom"
_NO_BRAND = "(no brand)"
_STATUS_COLOR = {
    "completed": "green",
    "succeeded": "green",
    "running": "blue",
    "planning": "blue",
    "pending": "gray",
    "skipped": "gray",
    "paused": "orange",
    "waiting_for_approval": "orange",
    "failed": "red",
    "cancelled": "red",
}
VIEWS = "views"


def current_session() -> AppSession:
    """This browser session's state, created on first use (demo mode by default)."""
    session = st.session_state.get(SESSION_KEY)
    if not isinstance(session, AppSession):
        session = build_session()
        st.session_state[SESSION_KEY] = session
    return session


def _status(value: str) -> str:
    color = _STATUS_COLOR.get(value, "gray")
    return f":{color}[{value.replace('_', ' ')}]"


def _open_run(run_id: str) -> None:
    st.session_state[SELECTED_RUN] = run_id
    st.switch_page(f"{VIEWS}/inspector.py")


# --------------------------------------------------------------------------- #
# Navigation and sidebar
# --------------------------------------------------------------------------- #
def navigation() -> Any:
    """The 16 navigation entries of ADR-018 (9 views and 7 studios)."""

    def page(name: str, title: str, icon: str, *, default: bool = False) -> Any:
        return st.Page(
            f"{VIEWS}/{name}.py", title=title, icon=f":material/{icon}:", default=default
        )

    return st.navigation(
        {
            "Workspace": [
                page("dashboard", "Dashboard", "dashboard", default=True),
                page("new_run", "New Run", "play_circle"),
                page("inspector", "Run Inspector", "monitoring"),
                page("history", "History", "history"),
            ],
            "Studios": [
                page("studio_research", "Research Studio", "travel_explore"),
                page("studio_strategy", "Strategy Studio", "strategy"),
                page("studio_content", "Content Studio", "edit_note"),
                page("studio_seo", "SEO Studio", "trending_up"),
                page("studio_creative", "Creative Studio", "palette"),
                page("studio_social", "Social Studio", "forum"),
                page("studio_analytics", "Analytics Studio", "query_stats"),
            ],
            "Library": [
                page("brand_memory", "Brand Memory", "sell"),
                page("agents", "Agents", "smart_toy"),
                page("workflow_builder", "Workflow Builder", "account_tree"),
                page("evaluation_lab", "Evaluation Lab", "science"),
            ],
            "System": [page("settings", "Settings and Health", "settings")],
        }
    )


def render_sidebar() -> None:
    session = current_session()
    with st.sidebar:
        if session.simulated:
            st.warning(
                "**Demo mode.** Agents are answered by a simulated responder, and nothing you "
                "enter is stored on the server."
            )
        else:
            st.info(f"**Local mode.** Provider: {session.engine.router.global_provider}.")
        st.caption(f"Campaign Forge {__version__}")


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def render_dashboard() -> None:
    session = current_session()
    st.title("Campaign Forge")
    st.write(
        "A multi-agent marketing workflow platform: research, strategy, copy, channel content "
        "and a final campaign brief, with every claim labeled and every step inspectable."
    )
    if session.simulated:
        st.info(
            "You are in demo mode: every run is simulated by a deterministic offline responder, "
            "so you can try every workflow without an API key."
        )
    runs = session.runs()
    needs_action = (RunStatus.PAUSED, RunStatus.WAITING_FOR_APPROVAL)
    cols = st.columns(4)
    cols[0].metric("Runs this session", len(runs))
    cols[1].metric("Completed", sum(r.status is RunStatus.COMPLETED for r in runs))
    cols[2].metric("Need action", sum(r.status in needs_action for r in runs))
    cols[3].metric("Tokens used", sum(r.usage.total_tokens for r in runs))
    st.page_link(f"{VIEWS}/new_run.py", label="Start a new run", icon=":material/play_circle:")
    st.subheader("Workflows")
    catalog = [
        {"workflow": i.id, "name": i.name, "what it produces": i.description}
        for i in BUILTIN_TEMPLATES.values()
    ]
    st.dataframe(catalog, hide_index=True)
    if runs:
        st.subheader("Recent runs")
        st.dataframe(run_rows(runs[:5]), hide_index=True)


# --------------------------------------------------------------------------- #
# New Run and studios
# --------------------------------------------------------------------------- #
def _custom_definition() -> WorkflowDefinition | None:
    raw = st.session_state.get(CUSTOM_DEFINITION)
    if not isinstance(raw, str):
        return None
    try:
        return load_definition(raw)
    except WorkflowValidationError:
        return None


def _brief_input(prefix: str) -> CampaignBrief | None:
    form_tab, upload_tab = st.tabs(["Fill in the brief", "Upload a JSON brief"])
    with upload_tab:
        uploaded = st.file_uploader("Brief (JSON)", type=["json"], key=f"{prefix}-brief-file")
    with form_tab:
        name = st.text_input("Product name", EXAMPLE_BRIEF.product_name, key=f"{prefix}-name")
        description = st.text_area(
            "Product description", EXAMPLE_BRIEF.product_description, key=f"{prefix}-desc"
        )
        audience = st.text_input(
            "Target audience", EXAMPLE_BRIEF.target_audience, key=f"{prefix}-audience"
        )
        goal = st.text_input("Goal", EXAMPLE_BRIEF.goal, key=f"{prefix}-goal")
        tone = st.text_input("Tone", EXAMPLE_BRIEF.tone, key=f"{prefix}-tone")
        channels = st.text_input(
            "Channels (comma-separated)",
            ", ".join(EXAMPLE_BRIEF.channels),
            key=f"{prefix}-channels",
        )
    try:
        if uploaded is not None:
            st.caption(f"Using the uploaded brief: {uploaded.name}")
            return parse_brief_json(uploaded.getvalue())
        return CampaignBrief(
            product_name=name,
            product_description=description,
            target_audience=audience,
            goal=goal,
            tone=tone,
            channels=channels,
        )
    except ValidationError as exc:
        st.error("The brief is not valid yet:\n\n" + "\n".join(f"- {e}" for e in errors_of(exc)))
    except CampaignForgeError as exc:
        st.error(str(exc))
    return None


def _brand_input(session: AppSession, prefix: str) -> BrandProfile | None:
    brands = session.memory.list_brands(session.scope)
    choice = st.selectbox(
        "Brand (optional)",
        [_NO_BRAND, *(b.name for b in brands)],
        key=f"{prefix}-brand",
        help="The copywriter writes in the brand's voice and may not use its banned terms.",
    )
    return next((b for b in brands if b.name == choice), None)


def _sources_input(prefix: str) -> Any:
    notes = st.text_area(
        "Your notes or source material (optional)",
        key=f"{prefix}-notes",
        help="Research agents cite it as user-provided evidence.",
    )
    files = st.file_uploader(
        "Source files (optional)",
        type=["txt", "md", "csv", "json"],
        accept_multiple_files=True,
        key=f"{prefix}-sources",
    )
    items = [("notes", notes)] + [
        (f.name, f.getvalue().decode("utf-8", errors="replace")) for f in files or []
    ]
    return text_sources(items)


def _plan_controls(definition: WorkflowDefinition, prefix: str) -> WorkflowDefinition:
    optional = [s.id for s in definition.steps if s.optional]
    skip = st.multiselect("Skip optional steps", optional, key=f"{prefix}-skip")
    limits = definition.limits
    cols = st.columns(3)
    calls = cols[0].number_input(
        "Max model calls", 1, limits.max_llm_calls, limits.max_llm_calls, key=f"{prefix}-calls"
    )
    tokens = cols[1].number_input(
        "Max tokens",
        1_000,
        limits.max_total_tokens,
        limits.max_total_tokens,
        step=1_000,
        key=f"{prefix}-tokens",
    )
    seconds = cols[2].number_input(
        "Max seconds",
        60,
        int(limits.max_wall_seconds),
        int(limits.max_wall_seconds),
        step=60,
        key=f"{prefix}-seconds",
    )
    budget = limits.model_copy(
        update={
            "max_llm_calls": int(calls),
            "max_total_tokens": int(tokens),
            "max_wall_seconds": float(seconds),
        }
    )
    return customize(definition, skip=skip, limits=BudgetLimits.model_validate(budget))


def render_new_run(studio: Studio | None = None) -> None:
    session = current_session()
    prefix = studio.id if studio else "new"
    st.title(studio.title if studio else "New Run")
    if studio:
        st.write(studio.description)
        if studio.planned:
            st.info(studio.planned)
            return
    options = template_ids(studio)
    custom = None if studio else _custom_definition()
    if custom is not None:
        options = [_CUSTOM, *options]

    def label(template_id: str) -> str:
        if template_id == _CUSTOM and custom is not None:
            return f"Custom: {custom.name} (from the Workflow Builder)"
        return f"{BUILTIN_TEMPLATES[template_id].name} ({template_id})"

    template = st.selectbox("Workflow", options, format_func=label, key=f"{prefix}-template")
    definition = custom if template == _CUSTOM and custom is not None else get_template(template)
    st.caption(definition.description)

    st.subheader("1. Brief")
    brief = _brief_input(prefix)
    inputs: dict[str, Any] = {}
    extras = definition.input_kind("brand") or definition.input_kind("sources")
    if extras:
        st.subheader("2. Context")
    if definition.input_kind("brand"):
        brand = _brand_input(session, prefix)
        if brand is not None:
            inputs["brand"] = brand
    if definition.input_kind("sources"):
        sources = _sources_input(prefix)
        if sources is not None:
            inputs["sources"] = sources

    st.subheader("3. Plan" if extras else "2. Plan")
    plan = _plan_controls(definition, prefix)
    st.graphviz_chart(dag_dot(plan))
    st.dataframe(plan_rows(plan), hide_index=True)
    note = (
        "Simulated: no provider is called and nothing is stored on the server."
        if session.simulated
        else f"Runs on {session.engine.router.global_provider}; free tiers may pause on rate "
        "limits, and the run resumes from its checkpoint."
    )
    st.caption(
        f"Budget: up to {plan.limits.max_llm_calls} model calls and "
        f"{plan.limits.max_total_tokens:,} tokens. {note}"
    )
    approved = st.button(
        "Approve and run", type="primary", disabled=brief is None, key=f"{prefix}-run"
    )
    if approved and brief is not None:
        try:
            run_id = session.start(plan, {"brief": brief, **inputs})
        except WorkflowValidationError as exc:
            st.error("The plan is not valid:\n\n" + "\n".join(f"- {p}" for p in exc.problems))
            return
        except CampaignForgeError as exc:
            st.error(str(exc))
            return
        _open_run(run_id)


def render_studio(studio_id: str) -> None:
    render_new_run(STUDIOS_BY_ID[studio_id])


# --------------------------------------------------------------------------- #
# Run Inspector
# --------------------------------------------------------------------------- #
def render_inspector() -> None:
    session = current_session()
    st.title("Run Inspector")
    runs = session.runs()
    selected = st.session_state.get(SELECTED_RUN)
    ids = [r.run_id for r in runs]
    if isinstance(selected, str) and selected not in ids and session.is_running(selected):
        ids.insert(0, selected)  # started in the background, not yet checkpointed
    if not ids:
        st.info("No runs yet in this session.")
        st.page_link(f"{VIEWS}/new_run.py", label="Start a new run", icon=":material/play_circle:")
        return
    by_id = {r.run_id: r for r in runs}

    def label(run_id: str) -> str:
        record = by_id.get(run_id)
        if record is None:
            return f"{run_id} (starting)"
        return f"{run_id} - {record.definition.name} - {record.status.value.replace('_', ' ')}"

    index = ids.index(selected) if selected in ids else 0
    run_id = st.selectbox("Run", ids, index=index, format_func=label, key="inspector-run")
    st.session_state[SELECTED_RUN] = run_id
    if session.is_running(run_id):
        _live_progress(session, run_id)
        return
    error = session.error(run_id)
    if error:
        st.error(f"The run stopped with an error: {error}")
    record = session.record(run_id)
    if record is None:
        st.warning("This run has no checkpoint yet.")
        return
    _render_record(session, record)


@st.fragment(run_every=2.0)
def _live_progress(session: AppSession, run_id: str) -> None:
    """Refreshes every two seconds while a background run executes."""
    if not session.is_running(run_id):
        st.rerun()
    record = session.record(run_id)
    st.info("Running. This view refreshes every two seconds.")
    if record is not None:
        st.markdown(f"**Status:** {_status(record.status.value)}")
        st.graphviz_chart(dag_dot(record.definition, record.steps))
        st.dataframe(step_rows(record), hide_index=True)


def _render_record(session: AppSession, record: RunRecord) -> None:
    run_id = record.run_id
    if is_simulated(record):
        st.warning(SIMULATED_NOTICE)
    st.markdown(f"**{record.definition.name}** - status {_status(record.status.value)}")
    if record.status_reason:
        st.text(record.status_reason)
    if record.resume_after:
        st.caption(f"The provider suggests resuming after about {record.resume_after:.0f} s.")
    usage = usage_summary(record)
    cols = st.columns(4)
    cols[0].metric("Model calls", usage["model calls"])
    cols[1].metric("Tokens", usage["total tokens"])
    cols[2].metric("Steps finished", usage["steps finished"])
    cols[3].metric("Workflow", record.definition.id)

    st.graphviz_chart(dag_dot(record.definition, record.steps))
    st.dataframe(step_rows(record), hide_index=True)
    _render_actions(session, record)

    outputs = record.outputs()
    if outputs:
        st.subheader("Outputs")
        for tab, (name, artifact) in zip(
            st.tabs([n.replace("_", " ").title() for n in outputs]), outputs.items(), strict=True
        ):
            with tab:
                st.markdown(artifact_markdown(artifact))
                if artifact.claims:
                    with st.expander(f"Claims ({len(artifact.claims)})"):
                        st.dataframe(claim_rows(artifact), hide_index=True)
                if artifact.evidence:
                    with st.expander(f"Evidence ({len(artifact.evidence)})"):
                        st.dataframe(evidence_rows(artifact), hide_index=True)
                with st.expander("Provenance"):
                    st.json(provenance(artifact))
                is_brand = name == "brand" and artifact.kind == BRAND_KIND
                if is_brand and st.button(
                    "Save this brand to Brand Memory", key=f"save-brand-{run_id}"
                ):
                    stored = session.memory.save_brand(
                        session.scope, artifact.as_model(BrandProfile)
                    )
                    st.success(f"Saved brand {stored.name}.")

    with st.expander("Step notes and errors"):
        for step in record.definition.steps:
            state = record.steps[step.id]
            lines = [*state.notes, *([f"error: {state.error}"] if state.error else [])]
            if lines:
                st.markdown(f"**{step.id}**")
                st.text("\n".join(lines))

    st.subheader("Download")
    cols = st.columns(3)
    cols[0].download_button(
        "Markdown", export_markdown(record), f"{run_id}.md", "text/markdown", key=f"md-{run_id}"
    )
    cols[1].download_button(
        "JSON", run_json(record), f"{run_id}.json", "application/json", key=f"json-{run_id}"
    )
    cols[2].download_button(
        "HTML", run_html(record), f"{run_id}.html", "text/html", key=f"html-{run_id}"
    )


def _render_actions(session: AppSession, record: RunRecord) -> None:
    run_id = record.run_id
    waiting = [
        s
        for s in record.definition.steps
        if record.steps[s.id].status is StepStatus.WAITING_FOR_APPROVAL
    ]
    for step in waiting:
        step_id = step.id
        st.warning(f"Step **{step_id}** is waiting for your approval.")
        comment = st.text_input("Comment (optional)", key=f"comment-{run_id}-{step_id}")
        cols = st.columns(2)
        if cols[0].button("Approve", type="primary", key=f"approve-{run_id}-{step_id}"):
            _act(partial(session.approve, run_id, step_id, approved=True, comment=comment))
        if cols[1].button("Reject (cancels the run)", key=f"reject-{run_id}-{step_id}"):
            _act(partial(session.approve, run_id, step_id, approved=False, comment=comment))
    cols = st.columns(2)
    if record.status in (RunStatus.PAUSED, RunStatus.FAILED) and cols[0].button(
        "Resume from checkpoint", key=f"resume-{run_id}"
    ):
        _act(lambda: session.resume(run_id))
    active = (RunStatus.PAUSED, RunStatus.WAITING_FOR_APPROVAL, RunStatus.FAILED)
    if record.status in active and cols[1].button("Cancel run", key=f"cancel-{run_id}"):
        _act(lambda: session.cancel(run_id))


def _act(action: Callable[[], object]) -> None:
    try:
        action()
    except CampaignForgeError as exc:
        st.error(str(exc))
        return
    st.rerun()


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def render_history() -> None:
    session = current_session()
    st.title("History")
    if session.simulated:
        st.caption("Demo history lasts only as long as this browser session.")
    runs_tab, campaigns_tab, notes_tab = st.tabs(["Runs", "Campaign history", "Notes"])
    with runs_tab:
        runs = session.runs()
        if not runs:
            st.info("No runs yet.")
        else:
            st.dataframe(run_rows(runs), hide_index=True)
            run_id = st.selectbox("Run", [r.run_id for r in runs], key="history-run")
            if st.button("Open in Run Inspector", key="history-open"):
                _open_run(run_id)
    with campaigns_tab:
        query = st.text_input("Search past campaigns", key="history-query")
        if query.strip():
            documents = [
                hit.document
                for hit in session.memory.search(session.scope, query, kinds=("campaign",))
            ]
        else:
            documents = session.memory.list_documents(session.scope, kind="campaign")
        if not documents:
            st.info("Completed runs are remembered here.")
        for document in documents:
            with st.expander(f"{document.title} ({document.created_at:%Y-%m-%d %H:%M})"):
                st.text(document.text)
    with notes_tab:
        text = st.text_area("New note", key="history-note")
        if st.button("Save note", key="history-save-note", disabled=not text.strip()):
            title = " ".join(text.split()[:8])
            session.memory.add_document(session.scope, "note", title, text)
            st.success("Note saved.")
        for document in session.memory.list_documents(session.scope, kind="note"):
            with st.expander(document.title):
                st.text(document.text)
                if st.button("Delete", key=f"delete-note-{document.id}"):
                    session.memory.delete_document(session.scope, document.id)
                    st.rerun()


# --------------------------------------------------------------------------- #
# Brand Memory
# --------------------------------------------------------------------------- #
def render_brand_memory() -> None:
    session = current_session()
    st.title("Brand Memory")
    st.write(
        "Brand profiles guide the copywriter and the brand check. Banned terms are enforced: "
        "copy that uses one is rewritten."
    )
    brands = session.memory.list_brands(session.scope)
    if brands:
        name = st.selectbox("Stored brands", [b.name for b in brands], key="brand-selected")
        brand = next(b for b in brands if b.name == name)
        st.markdown(brand.to_markdown())
        cols = st.columns(2)
        cols[0].download_button(
            "Download JSON",
            brand.model_dump_json(indent=2),
            f"{brand.slug}.json",
            "application/json",
            key="brand-download",
        )
        if cols[1].button("Delete brand", key="brand-delete"):
            try:
                session.memory.delete_brand(session.scope, brand.name)
            except MemoryNotFoundError as exc:
                st.error(str(exc))
            st.rerun()
    else:
        st.info("No brands stored yet.")

    st.subheader("Add a brand")
    form_tab, upload_tab, run_tab = st.tabs(["Write one", "Upload JSON", "From a run"])
    with form_tab, st.form("brand-form"):
        fields = {
            "name": st.text_input("Name"),
            "voice": st.text_area("Voice (2-3 sentences)"),
            "tone_words": st.text_input("Tone words (comma-separated)"),
            "audience": st.text_input("Audience"),
            "values": st.text_input("Values (comma-separated)"),
            "guidelines": st.text_area("Always (one per line)"),
            "avoid": st.text_area("Never (one per line)"),
            "preferred_terms": st.text_input("Preferred terms (comma-separated)"),
            "banned_terms": st.text_input("Banned terms (comma-separated)"),
            "visual_style": st.text_input("Visual style"),
        }
        if st.form_submit_button("Save brand"):
            lists = {"tone_words", "values", "guidelines", "avoid"}
            lists |= {"preferred_terms", "banned_terms"}
            data: dict[str, Any] = {
                k: split_terms(v) if k in lists else v for k, v in fields.items()
            }
            _save_brand(session, lambda: BrandProfile.model_validate(data))
    with upload_tab:
        uploaded = st.file_uploader("Brand profile (JSON)", type=["json"], key="brand-upload")
        if uploaded is not None and st.button("Save uploaded brand", key="brand-save-upload"):
            _save_brand(session, lambda: parse_brand_json(uploaded.getvalue()))
    with run_tab:
        candidates = [
            r for r in session.runs() if any(a.kind == BRAND_KIND for a in r.outputs().values())
        ]
        if not candidates:
            st.info("Run the brand-kit workflow to create a brand profile.")
        else:
            run_id = st.selectbox("Run", [r.run_id for r in candidates], key="brand-run")
            if st.button("Save the run's brand", key="brand-save-run"):
                record = next(r for r in candidates if r.run_id == run_id)
                artifact = next(a for a in record.outputs().values() if a.kind == BRAND_KIND)
                _save_brand(session, lambda: artifact.as_model(BrandProfile))


def _save_brand(session: AppSession, build: Callable[[], BrandProfile]) -> None:
    try:
        stored = session.memory.save_brand(session.scope, build())
    except ValidationError as exc:
        st.error("The brand is not valid:\n\n" + "\n".join(f"- {e}" for e in errors_of(exc)))
        return
    except CampaignForgeError as exc:
        st.error(str(exc))
        return
    st.success(f"Saved brand {stored.name}.")


# --------------------------------------------------------------------------- #
# Agents, Workflow Builder, Evaluation Lab, Settings
# --------------------------------------------------------------------------- #
def render_agents() -> None:
    session = current_session()
    registry = session.engine.registry
    st.title("Agents")
    st.dataframe(agent_rows(registry), hide_index=True)
    for spec in registry.specs():
        with st.expander(f"{spec.name} ({spec.id})"):
            st.write(spec.description)
            st.json(
                {
                    "inputs": [
                        {"name": i.name, "kind": i.kind, "required": i.required}
                        for i in spec.inputs
                    ],
                    "output": spec.output_kind,
                    "capabilities": list(spec.capabilities),
                    "rubric": list(spec.rubric),
                    "model class": spec.model_class.value,
                    "limits": spec.limits.model_dump(),
                }
            )


def render_workflow_builder() -> None:
    session = current_session()
    st.title("Workflow Builder")
    st.write(
        "Start from a template, skip optional steps and set the budget; or upload a JSON "
        "definition. Every definition is validated against the agent registry."
    )
    template_tab, upload_tab = st.tabs(["From a template", "Upload JSON"])
    definition: WorkflowDefinition | None = None
    with template_tab:
        template = st.selectbox(
            "Template",
            list(BUILTIN_TEMPLATES),
            format_func=lambda t: f"{BUILTIN_TEMPLATES[t].name} ({t})",
            key="builder-template",
        )
        definition = _plan_controls(get_template(template), "builder")
    with upload_tab:
        uploaded = st.file_uploader("Workflow definition (JSON)", type=["json"], key="builder-file")
        if uploaded is not None:
            try:
                definition = load_definition(uploaded.getvalue().decode("utf-8", errors="replace"))
                st.caption(f"Using the uploaded definition: {uploaded.name}")
            except WorkflowValidationError as exc:
                st.error("Invalid definition:\n\n" + "\n".join(f"- {p}" for p in exc.problems))
                return
    try:
        session.engine.validate(definition)
    except WorkflowValidationError as exc:
        st.error("Not valid:\n\n" + "\n".join(f"- {p}" for p in exc.problems))
        return
    st.success(f"Valid: {definition.id}@{definition.version}, {len(definition.steps)} steps.")
    st.graphviz_chart(dag_dot(definition))
    st.dataframe(plan_rows(definition), hide_index=True)
    payload = definition.model_dump_json(indent=2)
    cols = st.columns(2)
    cols[0].download_button(
        "Download JSON", payload, f"{definition.id}.json", "application/json", key="builder-dl"
    )
    if cols[1].button("Use in New Run", type="primary", key="builder-use"):
        st.session_state[CUSTOM_DEFINITION] = payload
        st.switch_page(f"{VIEWS}/new_run.py")
    with st.expander("Definition (JSON)"):
        st.code(payload, language="json")


def render_evaluation_lab() -> None:
    st.title("Evaluation Lab")
    st.info(
        "The interactive Evaluation Lab arrives in Session 8. Until then, the same checks run "
        "from the command line."
    )
    st.code(
        "python evals/run_eval.py --offline                          # v1 pipeline\n"
        "python evals/run_eval.py --offline --workflow full-campaign  # agent library\n"
        "python evals/run_eval.py --workflow full-campaign --only baseline-ecommerce  # live",
        language="bash",
    )
    st.write(
        "Every run is scored by deterministic checks (schema, grounding, labeled claims, "
        "citations, platform limits, injection canaries) and, for reviewed steps, a critic "
        "rubric mapped to a 0-1 score by code."
    )


def render_settings() -> None:
    session = current_session()
    st.title("Settings and Health")
    st.write(
        "Configuration comes from environment variables (see `.env.example`). Keys are shown "
        "only as set or not set."
    )
    st.dataframe(
        health_rows(session.settings, session.engine.router, mode=session.mode), hide_index=True
    )
    if st.button("Check the provider", key="settings-health"):
        router = session.engine.router
        try:
            status = router.provider(router.global_provider).health_check()
        except CampaignForgeError as exc:
            st.error(str(exc))
        else:
            if status.ok:
                st.success(f"{status.provider}: OK ({status.latency_s:.2f} s). {status.detail}")
            else:
                st.error(f"{status.provider}: not OK. {status.detail}")
    st.caption(
        "Set CF_UI_MODE=local to use your configured provider and the CLI's stores; the "
        "default is demo mode."
    )


__all__ = [
    "STUDIOS",
    "current_session",
    "navigation",
    "render_agents",
    "render_brand_memory",
    "render_dashboard",
    "render_evaluation_lab",
    "render_history",
    "render_inspector",
    "render_new_run",
    "render_settings",
    "render_sidebar",
    "render_studio",
    "render_workflow_builder",
]
