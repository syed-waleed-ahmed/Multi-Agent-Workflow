"""End-to-end tests for the CLI (workflow patched with a FakeLLM-backed one)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_forge import cli
from campaign_forge.config import Settings
from campaign_forge.workflow import CampaignWorkflow

from .conftest import FakeLLM

_VALID_BRIEF = {
    "product_name": "EcoSip",
    "product_description": "Insulated bottle.",
    "target_audience": "professionals",
    "goal": "sales",
    "tone": "fun",
    "channels": ["email", "instagram"],
}


class _FakeWorkflow(CampaignWorkflow):
    """Real orchestration logic, but backed by the deterministic FakeLLM."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, llm=FakeLLM())  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _patch_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "CampaignWorkflow", _FakeWorkflow)


def test_run_json_output_and_save(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        [
            "run",
            "--product-name",
            "EcoSip",
            "--description",
            "Insulated bottle.",
            "--audience",
            "professionals",
            "--goal",
            "sales",
            "--tone",
            "fun",
            "--channels",
            "email,instagram",
            "--output-dir",
            str(tmp_path),
            "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["marketing_copy"]["tagline"] == "Sip Smart, Live Green"
    assert data["saved_path"]
    assert Path(data["saved_path"]).exists()


def test_run_defaults_to_run_subcommand(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        [
            "--product-name",
            "P",
            "--description",
            "D",
            "--audience",
            "A",
            "--goal",
            "G",
            "--tone",
            "T",
            "--channels",
            "email",
            "--no-save",
            "--json",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["saved_path"] is None


def test_run_missing_fields_non_interactive_returns_2() -> None:
    # Under pytest stdin is not a TTY, so missing fields cannot be prompted for.
    assert cli.main(["run", "--json"]) == 2


def test_batch_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    briefs_file = tmp_path / "briefs.json"
    briefs_file.write_text(json.dumps([_VALID_BRIEF, _VALID_BRIEF]), encoding="utf-8")

    code = cli.main(
        ["batch", str(briefs_file), "--output-dir", str(tmp_path), "--workers", "2", "--json"]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 2
    assert all(item["ok"] for item in data)
    assert all(Path(item["saved_path"]).exists() for item in data)


def test_batch_partial_failure_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _FailingWorkflow(CampaignWorkflow):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings, llm=FakeLLM(fail_on="Broken"))  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "CampaignWorkflow", _FailingWorkflow)
    good = dict(_VALID_BRIEF)
    broken = {**_VALID_BRIEF, "product_name": "Broken"}
    briefs_file = tmp_path / "briefs.json"
    briefs_file.write_text(json.dumps([good, broken]), encoding="utf-8")

    code = cli.main(["batch", str(briefs_file), "--output-dir", str(tmp_path), "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    statuses = {item["product_name"]: item["ok"] for item in data}
    assert statuses["EcoSip"] is True
    assert statuses["Broken"] is False


def test_run_human_readable_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        [
            "run",
            "--product-name",
            "EcoSip",
            "--description",
            "Insulated bottle.",
            "--audience",
            "professionals",
            "--goal",
            "sales",
            "--tone",
            "fun",
            "--channels",
            "email",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Campaign Brief" in out
    assert "Image prompts" in out
    assert "Saved" in out


def test_batch_human_readable_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    briefs_file = tmp_path / "briefs.json"
    briefs_file.write_text(json.dumps([_VALID_BRIEF, _VALID_BRIEF]), encoding="utf-8")
    code = cli.main(["batch", str(briefs_file), "--output-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Batch summary" in out
    assert "ok" in out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "campaign-forge" in capsys.readouterr().out
