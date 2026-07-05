"""Tests for the resilient LLM client wrapper."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from openai import APIConnectionError, OpenAIError

from campaign_forge.config import Settings
from campaign_forge.exceptions import LLMError
from campaign_forge.llm import LLMClient, _retry_after_seconds


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str | None, *, choices: bool = True) -> None:
        self.choices = [_Choice(content)] if choices else []


class _FakeCompletions:
    def __init__(self, responder: Callable[[dict[str, Any]], _Response]) -> None:
        self._responder = responder

    def create(self, **kwargs: Any) -> _Response:
        return self._responder(kwargs)


class _FakeChat:
    def __init__(self, responder: Callable[[dict[str, Any]], _Response]) -> None:
        self.completions = _FakeCompletions(responder)


class FakeOpenAI:
    """Minimal stand-in for the OpenAI client used to drive LLMClient tests."""

    def __init__(self, responder: Callable[[dict[str, Any]], _Response]) -> None:
        self.chat = _FakeChat(responder)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "groq_api_key": "k", "max_retries": 2}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _transient_error() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("POST", "http://test"))


def test_complete_returns_content() -> None:
    client = LLMClient(_settings(), client=FakeOpenAI(lambda _: _Response("  hello  ")))  # type: ignore[arg-type]
    assert client.complete("sys", "user") == "hello"


def test_json_mode_sets_response_format() -> None:
    captured: dict[str, Any] = {}

    def responder(kwargs: dict[str, Any]) -> _Response:
        captured.update(kwargs)
        return _Response('{"ok": true}')

    client = LLMClient(_settings(), client=FakeOpenAI(responder))  # type: ignore[arg-type]
    client.complete("sys", "user", json_mode=True)
    assert captured["response_format"] == {"type": "json_object"}


def test_empty_response_raises() -> None:
    client = LLMClient(_settings(), client=FakeOpenAI(lambda _: _Response("   ")))  # type: ignore[arg-type]
    with pytest.raises(LLMError, match="empty"):
        client.complete("sys", "user")


def test_no_choices_raises() -> None:
    client = LLMClient(  # type: ignore[arg-type]
        _settings(), client=FakeOpenAI(lambda _: _Response(None, choices=False))
    )
    with pytest.raises(LLMError, match="no choices"):
        client.complete("sys", "user")


def test_retries_transient_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    attempts = {"n": 0}

    def responder(_: dict[str, Any]) -> _Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _transient_error()
        return _Response("recovered")

    client = LLMClient(_settings(max_retries=3), client=FakeOpenAI(responder))  # type: ignore[arg-type]
    assert client.complete("sys", "user") == "recovered"
    assert attempts["n"] == 3


def test_retries_exhausted_wraps_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    attempts = {"n": 0}

    def responder(_: dict[str, Any]) -> _Response:
        attempts["n"] += 1
        raise _transient_error()

    client = LLMClient(_settings(max_retries=2), client=FakeOpenAI(responder))  # type: ignore[arg-type]
    with pytest.raises(LLMError):
        client.complete("sys", "user")
    assert attempts["n"] == 3  # initial try + 2 retries


def test_permanent_error_not_retried() -> None:
    attempts = {"n": 0}

    def responder(_: dict[str, Any]) -> _Response:
        attempts["n"] += 1
        raise OpenAIError("permanent boom")

    client = LLMClient(_settings(max_retries=5), client=FakeOpenAI(responder))  # type: ignore[arg-type]
    with pytest.raises(LLMError):
        client.complete("sys", "user")
    assert attempts["n"] == 1  # not retried


class _FakeResponse:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_retry_after_from_header() -> None:
    exc = OpenAIError("rate limited")
    exc.response = _FakeResponse({"retry-after": "7"})  # type: ignore[attr-defined]
    assert _retry_after_seconds(exc) == 7.0


def test_retry_after_parsed_from_message() -> None:
    exc = Exception("Rate limit reached. Please try again in 5.66s. Upgrade for more.")
    assert _retry_after_seconds(exc) == 5.66


def test_retry_after_absent_returns_none() -> None:
    assert _retry_after_seconds(Exception("generic failure")) is None
    assert _retry_after_seconds(None) is None
