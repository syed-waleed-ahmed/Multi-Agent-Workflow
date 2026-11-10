"""The v1 ``CampaignWorkflow`` facade over the engine: policy, errors and compatibility."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from campaign_forge.config import AgentRoute, Settings
from campaign_forge.exceptions import (
    ConfigurationError,
    ProviderBadRequestError,
    ProviderNotAllowedError,
    QuotaExhaustedError,
)
from campaign_forge.llm import LLMClient
from campaign_forge.models import CampaignBrief
from campaign_forge.orchestration import WorkflowEngine, classic_campaign, default_registry
from campaign_forge.providers import ChatMessage, CompletionRequest, FakeProvider, ProviderRouter
from campaign_forge.providers.compat import LegacyClientProvider
from campaign_forge.workflow import CampaignWorkflow

from .conftest import ART_PAYLOAD, COPY_PAYLOAD, MANAGER_TEXT, RESEARCH_TEXT, FakeLLM

SCRIPT = {
    "legacy.research": RESEARCH_TEXT,
    "legacy.copywriter": json.dumps(COPY_PAYLOAD),
    "legacy.art_director": json.dumps(ART_PAYLOAD),
    "legacy.manager": MANAGER_TEXT,
}


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "CF_GROQ_API_KEY", "CF_OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("no_keys")
def test_free_only_now_covers_the_v1_entry_point() -> None:
    paid = Settings(  # type: ignore[call-arg]
        _env_file=None, base_url="https://api.openai.com/v1", openai_api_key="sk-" + "x" * 30
    )
    with pytest.raises(ProviderNotAllowedError, match="FREE_ONLY"):
        CampaignWorkflow(paid)
    allowed = paid.model_copy(update={"free_only": False})
    assert isinstance(CampaignWorkflow(allowed).engine, WorkflowEngine)


@pytest.mark.usefixtures("no_keys")
def test_missing_key_still_fails_at_construction() -> None:
    with pytest.raises(ConfigurationError, match="GROQ_API_KEY"):
        CampaignWorkflow(Settings(_env_file=None))  # type: ignore[call-arg]


def test_facade_keeps_no_run_history(settings: Settings, sample_brief: CampaignBrief) -> None:
    workflow = CampaignWorkflow(settings, llm=FakeLLM())  # type: ignore[arg-type]
    workflow.run(sample_brief)
    assert workflow.engine.list_runs() == []


def test_quota_pause_surfaces_the_original_error(
    settings: Settings, sample_brief: CampaignBrief
) -> None:
    class QuotaLLM(FakeLLM):
        def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
            if "copywriter" in system_prompt.lower():
                raise QuotaExhaustedError("tokens per day")
            return super().complete(system_prompt, user_prompt, json_mode=json_mode)

    workflow = CampaignWorkflow(settings, llm=QuotaLLM())  # type: ignore[arg-type]
    with pytest.raises(QuotaExhaustedError):
        workflow.run(sample_brief)


def test_v1_override_keys_apply_to_legacy_agents(sample_brief: CampaignBrief) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, provider="fake", agent_overrides={"manager": AgentRoute(max_tokens=1500)}
    )
    fake = FakeProvider(SCRIPT)
    router = ProviderRouter(settings, providers={"fake": fake})
    result = WorkflowEngine(default_registry(), router).start(
        classic_campaign(), {"brief": sample_brief}
    )
    assert result.ok
    by_agent = {r.metadata["agent"]: r.max_tokens for r in fake.requests}
    assert by_agent["legacy.manager"] == 1500
    assert by_agent["legacy.research"] == 1000  # legacy spec default (ADR-030)


def test_legacy_client_provider_adapts_v1_clients() -> None:
    class _Client:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kw: SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content="hi"), finish_reason="stop"
                            )
                        ],
                        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
                        model="served",
                    )
                )
            )

    settings = Settings(_env_file=None, groq_api_key="k")  # type: ignore[call-arg]
    real = LLMClient(settings, client=_Client())  # type: ignore[arg-type]
    provider = LegacyClientProvider(real, name="groq")
    response = provider.complete(CompletionRequest.from_prompts("s", "u"))
    assert (response.text, response.provider, response.usage.total_tokens) == ("hi", "groq", 4)

    duck = LegacyClientProvider(FakeLLM(), name="groq")  # type: ignore[arg-type]
    request = CompletionRequest.from_prompts("You are a Marketing Research Agent.", "brief")
    assert duck.complete(request).usage.complete is False
    assert duck.health_check().ok and duck.model_info().model == "fake-model"
    three = CompletionRequest(
        messages=(
            ChatMessage(role="system", content="s"),
            ChatMessage(role="user", content="u"),
            ChatMessage(role="assistant", content="a"),
        )
    )
    with pytest.raises(ProviderBadRequestError, match="one system and one user"):
        duck.complete(three)


def test_unexpected_engine_state_becomes_a_clear_error(
    settings: Settings, sample_brief: CampaignBrief, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = CampaignWorkflow(settings, llm=FakeLLM())  # type: ignore[arg-type]
    original = workflow.engine.start

    def no_errors(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        result.record.status = result.record.status.__class__("failed")
        result.errors.clear()
        return result

    monkeypatch.setattr(workflow.engine, "start", no_errors)
    with pytest.raises(Exception, match="Campaign run failed"):
        workflow.run(sample_brief)


@pytest.mark.usefixtures("no_keys")
def test_long_output_agents_route_to_the_strong_model() -> None:
    """D17: the final briefs overflow qwen3.8's 1,000-token output cap; gpt-oss-120b does not."""
    from campaign_forge.agents.legacy import LegacyManagerAgent, LegacyResearchAgent
    from campaign_forge.agents.library import CampaignReporterAgent

    router = ProviderRouter(Settings(_env_file=None, groq_api_key="k"))  # type: ignore[call-arg]
    for spec in (LegacyManagerAgent.spec, CampaignReporterAgent.spec):
        route = router.resolve(spec.id, model_class=spec.model_class)
        assert route.model == "openai/gpt-oss-120b", spec.id
    assert LegacyManagerAgent.spec.limits.max_tokens == 2000
    research = LegacyResearchAgent.spec
    assert router.resolve(research.id, model_class=research.model_class).model == (
        "qwen/qwen3.8-27b"
    )
    explicit = Settings(_env_file=None, groq_api_key="k", model="my-model")  # type: ignore[call-arg]
    manager = LegacyManagerAgent.spec
    assert (
        ProviderRouter(explicit).resolve(manager.id, model_class=manager.model_class).model
        == "my-model"
    )  # an explicit CF_MODEL still wins (v1)
