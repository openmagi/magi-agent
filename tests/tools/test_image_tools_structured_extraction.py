"""Tests for structured image extraction mode (TDD — RED → GREEN → REFACTOR).

Structured extraction asks the vision model to return EXACT data (numbers,
table rows, coordinates, labels) in a clean, machine-parseable form rather
than a prose description. This is critical for GAIA-style questions where the
agent must compute on visual data (e.g. "area of the green polygon", "average
of the red numbers in the table").

Key assertions:
1. image_understand(…, mode="structured") (or image_extract) sends a PROMPT
   that explicitly requests exact/structured output — not prose.
2. The structured prompt contains keywords signalling exactness:
   "exact", "number", "table", "list" / "row" / "column" — no prose summarisation.
3. The tool returns the model's structured payload as-is (not wrapped in prose).
4. Two-pass verify fires a SECOND litellm.completion call when requested.
5. Two-pass is disabled by default (single call).
6. Default prose mode still works and sends the old/default prompt (not the
   structured prompt).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers shared across all test classes
# ---------------------------------------------------------------------------


def _make_litellm_response(text: str) -> object:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


def _ctx(tmp_path: Path, *, adk_ctx: object = None) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
        adk_tool_context=adk_ctx,
    )


def _write_png(tmp_path: Path, name: str = "img.png") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 60)
    return p


# ---------------------------------------------------------------------------
# 1. Structured prompt content assertions
# ---------------------------------------------------------------------------


class TestStructuredExtractionPrompt:
    """The vision call in structured mode must use a prompt that demands exact data."""

    def test_structured_prompt_contains_exact_keyword(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Structured prompt must contain 'exact' (case-insensitive) somewhere."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        captured: list[dict[str, Any]] = []

        def fake_litellm(**kw: Any) -> object:
            captured.append(kw)
            return _make_litellm_response('{"values": [1, 2, 3]}')

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        image_understand({"path": "img.png", "mode": "structured"}, ctx)

        assert captured, "litellm.completion was not called"
        messages = captured[0]["messages"]
        # Collect all text parts in user message
        user_text = _extract_user_text(messages)
        assert "exact" in user_text.lower(), (
            f"Structured prompt must contain 'exact'; got: {user_text!r}"
        )

    def test_structured_prompt_does_not_say_describe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The structured prompt must NOT instruct prose description.

        Prose-style verbs like 'Describe this image in detail' are wrong for
        structured extraction because they invite narrative rather than data.
        """
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        captured: list[dict[str, Any]] = []

        def fake_litellm(**kw: Any) -> object:
            captured.append(kw)
            return _make_litellm_response("42")

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        image_understand({"path": "img.png", "mode": "structured"}, ctx)

        assert captured, "litellm.completion was not called"
        messages = captured[0]["messages"]
        user_text = _extract_user_text(messages)

        # The default prose prompt "Describe this image in detail." must not be sent
        assert "describe this image in detail" not in user_text.lower(), (
            f"Structured mode must not use the default prose prompt; "
            f"got: {user_text!r}"
        )

    def test_structured_prompt_requests_numbers_or_table_or_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Structured prompt should ask for numbers, table rows, or list form."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        captured: list[dict[str, Any]] = []

        def fake_litellm(**kw: Any) -> object:
            captured.append(kw)
            return _make_litellm_response("row1: 5, row2: 10")

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        image_understand({"path": "img.png", "mode": "structured"}, ctx)

        assert captured
        user_text = _extract_user_text(captured[0]["messages"]).lower()
        data_keywords = {"number", "table", "list", "row", "column", "value", "label"}
        found = data_keywords & set(user_text.split())  # check whole-word presence
        # Also do a substring search for multi-char tokens
        found |= {kw for kw in data_keywords if kw in user_text}
        assert found, (
            f"Structured prompt should mention at least one data keyword "
            f"from {data_keywords}; prompt was: {user_text!r}"
        )

    def test_structured_prompt_says_no_summarise_or_transcribe_exactly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Prompt should either warn against summarisation or instruct transcription."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        captured: list[dict[str, Any]] = []

        def fake_litellm(**kw: Any) -> object:
            captured.append(kw)
            return _make_litellm_response("7, 14, 21")

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        image_understand({"path": "img.png", "mode": "structured"}, ctx)

        assert captured
        user_text = _extract_user_text(captured[0]["messages"]).lower()
        prohibit_or_transcribe = (
            "do not summarize" in user_text
            or "do not summarise" in user_text
            or "transcribe" in user_text
            or "verbatim" in user_text
            or "do not paraphrase" in user_text
        )
        assert prohibit_or_transcribe, (
            f"Structured prompt should discourage summarisation or instruct verbatim "
            f"transcription; prompt was: {user_text!r}"
        )


# ---------------------------------------------------------------------------
# 2. Structured mode return value
# ---------------------------------------------------------------------------


class TestStructuredExtractionOutput:
    """image_understand in structured mode must return the model's raw payload."""

    def test_structured_mode_returns_model_payload_verbatim(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The extracted_data / description field must be exactly what the model returned."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        structured_payload = '{"numbers": [3.14, 2.71], "labels": ["A", "B"]}'

        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response(structured_payload),
        )

        ctx = _ctx(tmp_path)
        result = image_understand({"path": "img.png", "mode": "structured"}, ctx)

        assert result.status == "ok", f"Unexpected status: {result.status}"
        # The output must include the model's raw structured payload somewhere
        output = result.output
        assert isinstance(output, dict)
        # Accept either "extracted_data" key or falling back to "description"
        raw = output.get("extracted_data") or output.get("description")
        assert raw == structured_payload, (
            f"Expected model payload verbatim; got {raw!r}"
        )

    def test_structured_mode_output_has_extraction_mode_flag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Result output should indicate that structured extraction was used."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("42"),
        )

        ctx = _ctx(tmp_path)
        result = image_understand({"path": "img.png", "mode": "structured"}, ctx)

        assert result.status == "ok"
        output = result.output
        assert isinstance(output, dict)
        # The output should signal which mode was used
        mode_val = output.get("mode") or output.get("extractionMode")
        assert mode_val == "structured", (
            f"Expected output['mode']=='structured'; got {mode_val!r} in {output}"
        )


# ---------------------------------------------------------------------------
# 3. Default prose mode is unchanged
# ---------------------------------------------------------------------------


class TestProseModUnchanged:
    """Default mode (no mode param, or mode='prose') must work as before."""

    def test_default_mode_still_calls_litellm(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        called: list[int] = []

        def fake_litellm(**kw: Any) -> object:
            called.append(1)
            return _make_litellm_response("A lovely image.")

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        result = image_understand({"path": "img.png"}, ctx)  # no mode param

        assert result.status == "ok"
        assert len(called) == 1, "Default mode must still call litellm once"
        output = result.output
        assert isinstance(output, dict)
        desc = output.get("description")
        assert desc == "A lovely image.", f"Unexpected description: {desc!r}"

    def test_prose_mode_uses_default_or_custom_prompt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Default mode must pass the user-supplied prompt (or default) to litellm."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        captured: list[dict[str, Any]] = []

        def fake_litellm(**kw: Any) -> object:
            captured.append(kw)
            return _make_litellm_response("Blue sky.")

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        image_understand({"path": "img.png", "prompt": "What colour is the sky?"}, ctx)

        assert captured
        user_text = _extract_user_text(captured[0]["messages"])
        assert "What colour is the sky?" in user_text, (
            f"Custom prompt not forwarded in prose mode; got: {user_text!r}"
        )

    def test_explicit_prose_mode_works(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("It is a chart."),
        )

        ctx = _ctx(tmp_path)
        result = image_understand({"path": "img.png", "mode": "prose"}, ctx)
        assert result.status == "ok"
        output = result.output
        assert isinstance(output, dict)
        assert output.get("description") == "It is a chart."


# ---------------------------------------------------------------------------
# 4. Two-pass verify
# ---------------------------------------------------------------------------


class TestTwoPassVerify:
    """When verify=True, a second litellm call must be made to cross-check values."""

    def test_two_pass_fires_second_litellm_call(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With mode='structured' and verify=True, litellm.completion is called twice."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        calls: list[list[dict[str, Any]]] = []

        def fake_litellm(**kw: Any) -> object:
            calls.append(kw["messages"])
            if len(calls) == 1:
                return _make_litellm_response('{"values": [10, 20]}')
            # second call (verify) confirms values
            return _make_litellm_response("Confirmed: [10, 20]")

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        result = image_understand(
            {"path": "img.png", "mode": "structured", "verify": True}, ctx
        )

        assert result.status == "ok"
        assert len(calls) == 2, (
            f"Expected 2 litellm.completion calls with verify=True; got {len(calls)}"
        )

    def test_two_pass_verify_prompt_mentions_verify(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The second (verify) call prompt should ask the model to verify/confirm."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        calls: list[list[dict[str, Any]]] = []

        def fake_litellm(**kw: Any) -> object:
            calls.append(kw["messages"])
            if len(calls) == 1:
                return _make_litellm_response('{"numbers": [5]}')
            return _make_litellm_response("Verified. Correct.")

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        image_understand({"path": "img.png", "mode": "structured", "verify": True}, ctx)

        assert len(calls) == 2, "Need 2 calls to inspect verify prompt"
        verify_text = _extract_user_text(calls[1]).lower()
        verify_keywords = {"verify", "confirm", "correct", "check", "match"}
        found = verify_keywords & {kw for kw in verify_keywords if kw in verify_text}
        assert found, (
            f"Verify call prompt should mention verification; got: {verify_text!r}"
        )

    def test_two_pass_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With verify not specified (default), only one litellm call should be made."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        calls: list[int] = []

        def fake_litellm(**kw: Any) -> object:
            calls.append(1)
            return _make_litellm_response('{"v": 99}')

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        image_understand({"path": "img.png", "mode": "structured"}, ctx)

        assert len(calls) == 1, (
            f"Expected exactly 1 call when verify not set; got {len(calls)}"
        )

    def test_two_pass_result_includes_verify_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When verify fires, result output should carry the verify response."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        calls: list[int] = []

        def fake_litellm(**kw: Any) -> object:
            calls.append(1)
            if len(calls) == 1:
                return _make_litellm_response('{"score": 88}')
            return _make_litellm_response("Verified: score 88 is correct.")

        monkeypatch.setattr("litellm.completion", fake_litellm)

        ctx = _ctx(tmp_path)
        result = image_understand(
            {"path": "img.png", "mode": "structured", "verify": True}, ctx
        )

        assert result.status == "ok"
        output = result.output
        assert isinstance(output, dict)
        verify_out = output.get("verifyOutput") or output.get("verify_output")
        assert verify_out is not None, (
            f"Expected verifyOutput in result; got keys: {list(output.keys())}"
        )
        assert "88" in str(verify_out) or "Verified" in str(verify_out), (
            f"verifyOutput should reflect the verify response; got: {verify_out!r}"
        )


# ---------------------------------------------------------------------------
# 5. Invalid mode value
# ---------------------------------------------------------------------------


class TestInvalidMode:
    def test_invalid_mode_returns_blocked_or_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An unrecognised mode value should return a blocked/error status."""
        from magi_agent.tools.image_tools import image_understand

        _write_png(tmp_path)
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("should not be called"),
        )

        ctx = _ctx(tmp_path)
        result = image_understand({"path": "img.png", "mode": "UNKNOWN_MODE"}, ctx)

        assert result.status in {"blocked", "error"}, (
            f"Expected blocked or error for invalid mode; got {result.status!r}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_user_text(messages: list[dict[str, Any]]) -> str:
    """Flatten all text parts from the user message(s) into one string."""
    parts: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
    return " ".join(parts)
