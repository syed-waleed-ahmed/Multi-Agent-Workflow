"""Tests for the lenient parsing helpers."""

from __future__ import annotations

import pytest

from campaign_forge.parsing import parse_json_object, parse_prompt_lines, strip_code_fences


def test_strip_plain_text_unchanged() -> None:
    assert strip_code_fences("  hello  ") == "hello"


def test_strip_json_fence() -> None:
    fenced = '```json\n{"a": 1}\n```'
    assert strip_code_fences(fenced) == '{"a": 1}'


def test_parse_plain_json_object() -> None:
    assert parse_json_object('{"a": 1, "b": 2}') == {"a": 1, "b": 2}


def test_parse_json_with_surrounding_prose() -> None:
    text = 'Sure! Here is your JSON:\n{"tagline": "hi"}\nHope that helps.'
    assert parse_json_object(text) == {"tagline": "hi"}


def test_parse_json_fenced() -> None:
    assert parse_json_object('```json\n{"x": [1, 2]}\n```') == {"x": [1, 2]}


def test_non_object_json_rejected() -> None:
    with pytest.raises(ValueError):
        parse_json_object("[1, 2, 3]")


def test_no_json_rejected() -> None:
    with pytest.raises(ValueError):
        parse_json_object("no json here at all")


def test_parse_prompt_lines_numbered() -> None:
    text = (
        "Concepts:\n"
        "1. A vivid beach scene with a teal bottle\n"
        "2. Studio product shot on white background\n"
        "- short\n"
    )
    prompts = parse_prompt_lines(text)
    assert len(prompts) == 2
    assert prompts[0].startswith("A vivid beach")


def test_parse_prompt_lines_strips_leading_markers() -> None:
    prompts = parse_prompt_lines("- *A cinematic wide shot of a mountain lake at dawn*")
    assert prompts == ["A cinematic wide shot of a mountain lake at dawn"]


def test_parse_prompt_lines_skips_short_headings() -> None:
    assert parse_prompt_lines("Concepts:\n1. Too short\n") == []
