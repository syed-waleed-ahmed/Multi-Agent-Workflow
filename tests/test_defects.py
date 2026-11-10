"""Regression tests for the defects reproduced in the Session 0 audit (docs/PROJECT_AUDIT.md)."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from campaign_forge import cli
from campaign_forge.agents import ArtDirectorAgent, ManagerAgent
from campaign_forge.agents.manager import MIN_BRIEF_WORDS, check_brief
from campaign_forge.config import (
    AgentRoute,
    Settings,
    apply_overrides,
    get_settings,
    reload_settings,
)
from campaign_forge.exceptions import ConfigurationError, OutputParsingError
from campaign_forge.llm import LLMClient
from campaign_forge.models import CampaignBrief, CopyContent
from campaign_forge.workflow import CampaignWorkflow

from .conftest import MANAGER_TEXT, FakeLLM

REPO_ROOT = Path(__file__).resolve().parents[1]


class _ScriptedLLM(FakeLLM):
    """Returns scripted answers in order, recording every prompt."""

    def __init__(self, *answers: str) -> None:
        super().__init__()
        self._answers = list(answers)

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
        self.calls.append((system_prompt, user_prompt, json_mode))
        return self._answers.pop(0)


# --------------------------------------------------------------------------- #
# D1 - CLI overrides bypassed validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("workers", ["-1", "0", "500", "lots"])
def test_d1_invalid_worker_counts_exit_cleanly_with_code_2(
    workers: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["batch", "briefs.json", "--workers", workers])
    assert excinfo.value.code == 2
    assert "--workers" in capsys.readouterr().err


def test_d1_apply_overrides_validates_and_preserves_explicit_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CF_MODEL", raising=False)
    settings = Settings(_env_file=None, groq_api_key="k")  # type: ignore[call-arg]
    with pytest.raises(ConfigurationError, match="Invalid setting override"):
        apply_overrides(settings, {"max_workers": 0})
    updated = apply_overrides(settings, {"max_workers": 8})
    assert updated.max_workers == 8
    assert "max_workers" in updated.model_fields_set
    assert "model" not in updated.model_fields_set
    assert apply_overrides(settings, {}) is settings


def test_d1_library_batch_rejects_invalid_worker_counts(settings: Settings) -> None:
    workflow = CampaignWorkflow(settings, llm=FakeLLM())  # type: ignore[arg-type]
    for workers in (0, 65):
        with pytest.raises(ConfigurationError, match="max_workers"):
            workflow.run_batch([], max_workers=workers)


# --------------------------------------------------------------------------- #
# D2 - cached settings ignored later environment changes
# --------------------------------------------------------------------------- #
@pytest.fixture
def _fresh_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("_fresh_settings_cache")
def test_d2_reload_settings_picks_up_environment_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_MODEL", "model-a")
    assert get_settings().model == "model-a"
    monkeypatch.setenv("CF_MODEL", "model-b")
    assert get_settings().model == "model-a"  # cached by design (CLI)
    assert reload_settings().model == "model-b"


# --------------------------------------------------------------------------- #
# D3 - empty art-director output counted as success
# --------------------------------------------------------------------------- #
def test_d3_empty_image_prompts_are_repaired_once_then_fail(
    sample_brief: CampaignBrief, sample_copy: CopyContent
) -> None:
    llm = _ScriptedLLM('{"image_prompts": []}', '{"image_prompts": []}')
    with pytest.raises(OutputParsingError, match="no usable image prompts"):
        ArtDirectorAgent(llm).run(sample_brief, "research", sample_copy)  # type: ignore[arg-type]
    assert len(llm.calls) == 2


def test_d3_repair_recovers_image_prompts(
    sample_brief: CampaignBrief, sample_copy: CopyContent
) -> None:
    good = '{"image_prompts": ["A teal bottle on a sunlit beach, vibrant mood"]}'
    llm = _ScriptedLLM("{}", good)
    prompts = ArtDirectorAgent(llm).run(sample_brief, "research", sample_copy)  # type: ignore[arg-type]
    assert prompts == ["A teal bottle on a sunlit beach, vibrant mood"]
    assert "no usable image prompts" in llm.calls[1][1]


# --------------------------------------------------------------------------- #
# D4 - manager output was never validated
# --------------------------------------------------------------------------- #
def test_d4_check_brief() -> None:
    assert check_brief("ok").degenerate
    good = check_brief(MANAGER_TEXT)
    assert not good.degenerate and good.missing_sections == ()
    partial = check_brief("# Brief\n\n## Overview\n" + "word " * MIN_BRIEF_WORDS)
    assert not partial.degenerate
    assert "Deliverables" in partial.missing_sections
    assert any("missing" in p for p in partial.problems())


def test_d4_degenerate_brief_is_repaired_once_then_fails(
    sample_brief: CampaignBrief, sample_copy: CopyContent
) -> None:
    llm = _ScriptedLLM("ok", "still ok")
    with pytest.raises(OutputParsingError, match="unusable"):
        ManagerAgent(llm).run(sample_brief, "research", sample_copy, ["p1"])  # type: ignore[arg-type]
    assert "no Markdown section headings" in llm.calls[1][1]


def test_d4_repair_recovers_a_full_brief(
    sample_brief: CampaignBrief, sample_copy: CopyContent
) -> None:
    llm = _ScriptedLLM("ok", MANAGER_TEXT)
    result = ManagerAgent(llm).run(sample_brief, "research", sample_copy, ["p1"])  # type: ignore[arg-type]
    assert result == MANAGER_TEXT


def test_d4_brief_missing_a_section_is_accepted_after_repair(
    sample_brief: CampaignBrief, sample_copy: CopyContent
) -> None:
    partial = "# Brief\n\n## Overview\n" + "word " * MIN_BRIEF_WORDS
    llm = _ScriptedLLM(partial, partial)
    result = ManagerAgent(llm).run(sample_brief, "research", sample_copy, [])  # type: ignore[arg-type]
    assert result == partial
    assert len(llm.calls) == 2


# --------------------------------------------------------------------------- #
# D5 - a raising batch callback aborted the batch
# --------------------------------------------------------------------------- #
def test_d5_failing_callback_does_not_lose_batch_results(
    settings: Settings, sample_brief: CampaignBrief, caplog: pytest.LogCaptureFixture
) -> None:
    def broken_progress(_item: object) -> None:
        raise RuntimeError("progress UI crashed")

    workflow = CampaignWorkflow(settings, llm=FakeLLM())  # type: ignore[arg-type]
    logger = logging.getLogger("campaign_forge")
    logger.addHandler(caplog.handler)
    try:
        results = workflow.run_batch([sample_brief, sample_brief], on_result=broken_progress)
    finally:
        logger.removeHandler(caplog.handler)
    assert [item.ok for item in results] == [True, True]
    assert "on_result callback failed" in caplog.text


# --------------------------------------------------------------------------- #
# D9 - batch results were written only after the whole batch finished
# --------------------------------------------------------------------------- #
def test_d9_batch_saves_each_result_as_it_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campaign_forge.models import CampaignResult

    briefs = [{**_BRIEF_DATA, "product_name": name} for name in ("Alpha", "Broken Disk", "Gamma")]
    path = tmp_path / "briefs.json"
    path.write_text(json.dumps(briefs), encoding="utf-8")
    saved_before_batch_returned: list[str] = []
    real_save = CampaignResult.save

    def save(self: CampaignResult, output_dir: Path | str) -> Path:
        if self.brief.product_name == "Broken Disk":
            raise OSError("disk full")
        saved_before_batch_returned.append(self.brief.product_name)
        return real_save(self, output_dir)

    class _FakeWorkflow(CampaignWorkflow):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings, llm=FakeLLM())  # type: ignore[arg-type]

        def run_batch(self, *args: Any, **kwargs: Any) -> Any:
            saved_before_batch_returned.clear()
            results = super().run_batch(*args, **kwargs)
            # Saving happened inside on_result, i.e. before run_batch returned.
            assert sorted(saved_before_batch_returned) == ["Alpha", "Gamma"]
            return results

    monkeypatch.setattr(CampaignResult, "save", save)
    monkeypatch.setattr(cli, "CampaignWorkflow", _FakeWorkflow)
    code = cli.main(["batch", str(path), "--output-dir", str(tmp_path / "out"), "--json"])
    assert code == 1  # a result that could not be saved is a failure
    payload = json.loads(capsys.readouterr().out)
    by_name = {item["product_name"]: item for item in payload}
    assert by_name["Broken Disk"]["error"] == "could not save: disk full"
    assert by_name["Alpha"]["saved_path"] and by_name["Gamma"]["saved_path"]

    code = cli.main(["batch", str(path), "--output-dir", str(tmp_path / "out2")])
    assert code == 1
    assert "could not save: disk full" in capsys.readouterr().out


_BRIEF_DATA = {
    "product_name": "x",
    "product_description": "Insulated bottle.",
    "target_audience": "hikers",
    "goal": "sales",
    "tone": "fun",
    "channels": ["email"],
}


# --------------------------------------------------------------------------- #
# D7 - eval grounding passed on stop-words
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def run_eval() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_eval", REPO_ROOT / "evals" / "run_eval.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eval"] = module
    spec.loader.exec_module(module)
    return module


def test_d7_grounding_ignores_generic_words(run_eval: ModuleType) -> None:
    grounded = run_eval._grounded
    assert grounded("The Bottle", "the plan is great") is False
    assert grounded("The Bottle", "A plan for the bottle launch") is True
    assert grounded("EcoSip Reusable Bottle", "Introducing ECOSIP to the world") is True
    assert grounded("The New App", "the new app is here") is True  # only generic tokens
    assert grounded("\u7eff\u8336", "\u5173\u4e8e\u7eff\u8336\u7684\u7b80\u62a5") is True


def test_d7_offline_eval_still_passes(
    run_eval: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_eval.main(["--offline", "--min-pass-rate", "1.0"]) == 0
    assert "10/10 passed" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# D8 - one max_tokens for every agent (truncation of the final brief)
# --------------------------------------------------------------------------- #
class _CapturingClient:
    def __init__(self, content: str) -> None:
        self.kwargs: list[dict[str, Any]] = []

        def create(**kwargs: Any) -> Any:
            self.kwargs.append(kwargs)
            message = SimpleNamespace(content=content)
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_d8_per_agent_overrides_reach_the_request(
    sample_brief: CampaignBrief, sample_copy: CopyContent
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        groq_api_key="k",
        agent_overrides={
            "manager": AgentRoute(max_tokens=1500, model="big-model", temperature=0.2)
        },
    )
    client = _CapturingClient(MANAGER_TEXT)
    llm = LLMClient(settings, client=client)  # type: ignore[arg-type]
    ManagerAgent(llm).run(sample_brief, "research", sample_copy, ["p1"])
    sent = client.kwargs[0]
    assert (sent["max_tokens"], sent["model"], sent["temperature"]) == (1500, "big-model", 0.2)
    assert llm.for_agent("manager").model == "big-model"
    assert llm.for_agent("research") is llm  # no override -> same client
    assert llm.complete_with_usage("s", "u").usage.complete is False
    assert llm.provider.name == "groq"


def test_d8_provider_overrides_are_ignored_by_v1_facade_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, groq_api_key="k", agent_overrides={"manager": AgentRoute(provider="gemini")}
    )
    llm = LLMClient(settings, client=_CapturingClient("x"))  # type: ignore[arg-type]
    logger = logging.getLogger("campaign_forge")
    logger.addHandler(caplog.handler)
    try:
        view = llm.for_agent("manager")
    finally:
        logger.removeHandler(caplog.handler)
    assert view is not llm and view.model == settings.model
    assert "ignores provider overrides" in caplog.text
