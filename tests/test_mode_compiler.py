"""PR-U3.4: NL → agent-mode compiler tests.

Mirrors :mod:`tests.test_rule_compiler`: a fake ADK model, fail-open
contracts, retry on parse failure, and the deterministic honest-degrade
normalization (unknown tools / scoped ids dropped, permission_mode capped).

ZERO network, ZERO real model calls.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.customize.mode_compiler import (
    _normalize_draft,
    _parse_mode_response,
    compile_nl_to_mode,
)


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _make_fake_model(response_text: str, *, prompt_capture: list[str] | None = None):
    class _FakeModel:
        model = "fake-mode-compiler-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            if prompt_capture is not None:
                try:
                    for content in llm_request.contents:
                        for part in content.parts:
                            if hasattr(part, "text") and part.text:
                                prompt_capture.append(part.text)
                except Exception:  # noqa: BLE001
                    pass
            yield _FakeLlmResponse(response_text)

    return _FakeModel()


def _factory_for(response_text: str, *, prompt_capture: list[str] | None = None):
    def _factory() -> object:
        return _make_fake_model(response_text, prompt_capture=prompt_capture)

    return _factory


def _factory_sequence(*responses: str):
    responses_list = list(responses)
    call_index = [0]

    def _factory() -> object:
        idx = call_index[0]
        call_index[0] += 1
        text = responses_list[idx] if idx < len(responses_list) else responses_list[-1]
        return _make_fake_model(text)

    return _factory


_VALID_PAYLOAD = {
    "displayName": "Careful reviewer",
    "systemPrompt": "Act as a careful read-only reviewer; cite sources.",
    "toolDelta": {"exclude": ["FileEdit"], "include": []},
    "scopedPolicyIds": ["custom_rule:cr_cite"],
    "permissionMode": "default",
    "explanation": "A read-only reviewer that must cite sources.",
}
_VALID_RESPONSE = f"```json\n{json.dumps(_VALID_PAYLOAD)}\n```"


# ---------------------------------------------------------------------------
# Fail-open contracts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_factory_fails_open() -> None:
    result = await compile_nl_to_mode("a read-only reviewer", model_factory=None)
    assert result == {"ok": False, "error": "compiler unavailable", "draft": None}


@pytest.mark.asyncio
async def test_factory_returning_none_fails_open() -> None:
    result = await compile_nl_to_mode(
        "a read-only reviewer", model_factory=lambda: None
    )
    assert result["ok"] is False
    assert result["draft"] is None
    assert "unavailable" in result["error"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compiles_valid_mode_draft() -> None:
    result = await compile_nl_to_mode(
        "a careful read-only reviewer that cites sources",
        model_factory=_factory_for(_VALID_RESPONSE),
        available_tools=["FileEdit", "FileRead"],
        scopable_policy_ids=["custom_rule:cr_cite"],
    )
    assert result["ok"] is True
    draft = result["draft"]
    assert draft["displayName"] == "Careful reviewer"
    assert draft["toolDelta"]["exclude"] == ["FileEdit"]
    assert draft["scopedPolicyIds"] == ["custom_rule:cr_cite"]
    assert draft["permissionMode"] == "default"
    assert result["warnings"] == []
    # The compiler must NOT invent a mode id: the frontend derives it on save.
    assert "id" not in draft


@pytest.mark.asyncio
async def test_prompt_includes_the_tool_and_policy_inventories() -> None:
    capture: list[str] = []
    await compile_nl_to_mode(
        "reviewer",
        model_factory=_factory_for(_VALID_RESPONSE, prompt_capture=capture),
        available_tools=["FileRead", "WebFetch"],
        scopable_policy_ids=["custom_rule:cr_cite"],
    )
    joined = "\n".join(capture)
    assert "FileRead" in joined
    assert "WebFetch" in joined
    assert "custom_rule:cr_cite" in joined


# ---------------------------------------------------------------------------
# Honest-degrade normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drops_unknown_tools_and_scoped_ids_with_warnings() -> None:
    payload = {
        "displayName": "M",
        "toolDelta": {"exclude": ["FileEdit", "Bogus"], "include": ["Ghost"]},
        "scopedPolicyIds": ["custom_rule:cr_cite", "custom_rule:nope"],
        "explanation": "x",
    }
    result = await compile_nl_to_mode(
        "m",
        model_factory=_factory_for(f"```json\n{json.dumps(payload)}\n```"),
        available_tools=["FileEdit", "FileRead"],
        scopable_policy_ids=["custom_rule:cr_cite"],
    )
    assert result["ok"] is True
    draft = result["draft"]
    assert draft["toolDelta"]["exclude"] == ["FileEdit"]
    assert draft["toolDelta"]["include"] == []
    assert draft["scopedPolicyIds"] == ["custom_rule:cr_cite"]
    warn = " ".join(result["warnings"])
    assert "Bogus" in warn and "Ghost" in warn and "nope" in warn


def test_normalize_caps_a_loosening_permission_mode() -> None:
    # baseline 'default' is the strictest; any other value loosens it, so the
    # draft must fall back to inherit (None) with a warning.
    draft, warnings, _ = _normalize_draft(
        {"displayName": "M", "permissionMode": "bypassPermissions"},
        available_tools=[],
        scopable_policy_ids=[],
        baseline_permission_mode="default",
    )
    assert draft["permissionMode"] is None
    assert any("loosen" in w for w in warnings)


def test_normalize_keeps_a_tightening_permission_mode() -> None:
    # baseline 'bypassPermissions' is the loosest; 'default' tightens → kept.
    draft, warnings, _ = _normalize_draft(
        {"displayName": "M", "permissionMode": "default"},
        available_tools=[],
        scopable_policy_ids=[],
        baseline_permission_mode="bypassPermissions",
    )
    assert draft["permissionMode"] == "default"
    assert warnings == []


def test_normalize_ignores_invalid_permission_mode() -> None:
    draft, warnings, _ = _normalize_draft(
        {"displayName": "M", "permissionMode": "yolo"},
        available_tools=[],
        scopable_policy_ids=[],
        baseline_permission_mode="default",
    )
    assert draft["permissionMode"] is None
    assert any("invalid permission mode" in w for w in warnings)


def test_normalize_defaults_missing_display_name() -> None:
    draft, _, _ = _normalize_draft(
        {"systemPrompt": "hi"},
        available_tools=[],
        scopable_policy_ids=[],
        baseline_permission_mode="default",
    )
    assert draft["displayName"] == "New mode"


# ---------------------------------------------------------------------------
# Parse + retry
# ---------------------------------------------------------------------------


def test_parse_mode_response_extracts_fenced_json() -> None:
    parsed = _parse_mode_response(_VALID_RESPONSE)
    assert parsed is not None
    assert parsed["displayName"] == "Careful reviewer"


def test_parse_mode_response_rejects_non_object() -> None:
    assert _parse_mode_response("not json") is None
    assert _parse_mode_response("[1, 2, 3]") is None


@pytest.mark.asyncio
async def test_retries_once_on_parse_failure_then_succeeds() -> None:
    result = await compile_nl_to_mode(
        "reviewer",
        model_factory=_factory_sequence("garbage-not-json", _VALID_RESPONSE),
        available_tools=["FileEdit"],
        scopable_policy_ids=["custom_rule:cr_cite"],
    )
    assert result["ok"] is True
    assert result["draft"]["displayName"] == "Careful reviewer"


@pytest.mark.asyncio
async def test_returns_error_after_exhausting_retries() -> None:
    result = await compile_nl_to_mode(
        "reviewer",
        model_factory=_factory_for("still-not-json"),
        available_tools=[],
        scopable_policy_ids=[],
    )
    assert result["ok"] is False
    assert result["draft"] is None
    assert result["error"]
