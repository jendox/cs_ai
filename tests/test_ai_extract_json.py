"""LLM response JSON extraction."""

from __future__ import annotations

import pytest

from src.ai.utils import LLMJsonParseError, extract_json_block


def test_extract_json_block_plain_object() -> None:
    raw = '{"category": "customer_support", "confidence": 0.9}'
    assert extract_json_block(raw) == raw


def test_extract_json_block_fenced() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert extract_json_block(raw) == '{"a": 1}'


def test_extract_json_block_surrounded_by_text() -> None:
    raw = 'Sure: {"x": true} thanks'
    assert extract_json_block(raw) == '{"x": true}'


def test_extract_json_block_empty_raises() -> None:
    with pytest.raises(LLMJsonParseError):
        extract_json_block("")
