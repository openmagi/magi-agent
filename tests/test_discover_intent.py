"""PR-F-UX6 — ``discover_intent`` LLM step unit tests.

ZERO network. Reuses the fake-ADK-model harness from
``tests/test_rule_compiler.py``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.customize.rule_compiler import (
    EXPECTS_VOCAB,
    _parse_intent_map,
    discover_intent,
)


# ---------------------------------------------------------------------------
# Fake-model harness
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _factory(response_text: str):
    def _f() -> object:
        class _Model:
            model = "fake-discover-intent-model"

            async def generate_content_async(
                self, req: Any, stream: bool = False
            ) -> AsyncGenerator:
                yield _FakeLlmResponse(response_text)

        return _Model()

    return _f


# ---------------------------------------------------------------------------
# Vocab sanity
# ---------------------------------------------------------------------------


def test_expects_vocab_lists_eight_canonical_tags() -> None:
    assert EXPECTS_VOCAB == frozenset(
        {
            "evidence_ref",
            "verifier_ref",
            "field",
            "tool_name",
            "lifecycle",
            "scope",
            "value",
            "freeform",
        }
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_intent_map_normalizes_questions_caps_to_three() -> None:
    raw = json.dumps(
        {
            "whatToCheck": "audit AWS keys",
            "whereInLifecycle": "after_tool_use",
            "whatToDoOnFail": "block",
            "openQuestions": [
                {
                    "question": "Which lifecycle should this fire at?",
                    "expects": "lifecycle",
                    "inventory": ["pre_final", "after_tool_use"],
                },
                {"question": "Which tool?", "expects": "tool_name"},
                {"question": "Threshold?", "expects": "value"},
                {"question": "Extra Q4?", "expects": "freeform"},
            ],
            "confidence": 0.4,
        }
    )
    intent = _parse_intent_map(raw)
    assert intent is not None
    assert intent["whatToCheck"] == "audit AWS keys"
    assert intent["whereInLifecycle"] == "after_tool_use"
    assert intent["whatToDoOnFail"] == "block"
    assert len(intent["openQuestions"]) == 3
    assert intent["openQuestions"][0]["expects"] == "lifecycle"
    assert intent["openQuestions"][0]["inventory"] == [
        "pre_final",
        "after_tool_use",
    ]
    assert intent["confidence"] == 0.4


def test_parse_intent_map_drops_unknown_expects_to_freeform() -> None:
    raw = json.dumps(
        {
            "whatToCheck": "x",
            "openQuestions": [
                {"question": "Q?", "expects": "bogus_tag_not_in_vocab"}
            ],
        }
    )
    intent = _parse_intent_map(raw)
    assert intent is not None
    assert intent["openQuestions"][0]["expects"] == "freeform"


def test_parse_intent_map_dedupes_questions_by_text() -> None:
    raw = json.dumps(
        {
            "openQuestions": [
                {"question": "Same?", "expects": "freeform"},
                {"question": "Same?", "expects": "freeform"},
                {"question": "Different?", "expects": "freeform"},
            ]
        }
    )
    intent = _parse_intent_map(raw)
    assert intent is not None
    assert len(intent["openQuestions"]) == 2


def test_parse_intent_map_clamps_confidence_to_unit_interval() -> None:
    intent_lo = _parse_intent_map(json.dumps({"confidence": -1.5}))
    intent_hi = _parse_intent_map(json.dumps({"confidence": 2.0}))
    intent_bad = _parse_intent_map(json.dumps({"confidence": "abc"}))
    assert intent_lo is not None and intent_lo["confidence"] == 0.0
    assert intent_hi is not None and intent_hi["confidence"] == 1.0
    assert intent_bad is not None and intent_bad["confidence"] == 0.0


def test_parse_intent_map_returns_none_on_non_dict_root() -> None:
    assert _parse_intent_map("[]") is None
    assert _parse_intent_map("not json") is None


# ---------------------------------------------------------------------------
# discover_intent contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_intent_returns_ok_intent_on_success() -> None:
    payload = json.dumps(
        {
            "whatToCheck": "audit AWS keys",
            "whereInLifecycle": "after_tool_use",
            "whatToDoOnFail": "block",
            "openQuestions": [
                {
                    "question": "Which tool's output should we scan?",
                    "expects": "tool_name",
                    "inventory": ["FileRead", "shell_exec"],
                }
            ],
            "confidence": 0.6,
        }
    )
    result = await discover_intent(
        "audit AWS keys", model_factory=_factory(payload)
    )
    assert result["ok"] is True
    intent = result["intent"]
    assert intent["whatToCheck"] == "audit AWS keys"
    assert intent["openQuestions"][0]["expects"] == "tool_name"
    assert intent["openQuestions"][0]["inventory"] == [
        "FileRead",
        "shell_exec",
    ]


@pytest.mark.asyncio
async def test_discover_intent_fails_open_on_none_factory() -> None:
    result = await discover_intent("x", model_factory=None)
    assert result["ok"] is False
    assert "unavailable" in result["error"]


@pytest.mark.asyncio
async def test_discover_intent_returns_error_on_unparseable_response() -> None:
    result = await discover_intent("x", model_factory=_factory("not json at all"))
    assert result["ok"] is False
    assert "unparseable" in result["error"]


@pytest.mark.asyncio
async def test_discover_intent_factory_exception_degrades_gracefully() -> None:
    def _boom() -> object:
        raise RuntimeError("model factory exploded")

    result = await discover_intent("x", model_factory=_boom)
    assert result["ok"] is False
    assert "model factory failed" in result["error"]
