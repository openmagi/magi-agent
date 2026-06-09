"""Tests for the ImageUnderstand vision model invocation fix (PR-A).

Key assertions:
- _call_vision_model does NOT probe adk_tool_context.model / ._model.
- It invokes litellm.completion with base64 image content.
- image_understand returns the model's output, not the old stub.
- The firing test: litellm.completion is called exactly once and its output
  is returned.
"""

from __future__ import annotations

import types as pytypes
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_litellm_response(text: str) -> object:
    """Create a minimal mock that looks like a litellm CompletionResponse."""
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


# ---------------------------------------------------------------------------
# PR-A — Firing test: litellm.completion is called, not adk_tool_context.model
# ---------------------------------------------------------------------------


class TestCallVisionModel:
    def test_call_vision_model_does_not_use_adk_context_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_call_vision_model must NOT probe adk_tool_context.model.

        Before the fix it returned '[vision model not available in this context]'
        because it tried adk_tool_context.model which does not exist on ADK Context.
        After the fix it must call litellm.completion and return its result.
        """
        from magi_agent.tools.image_tools import _call_vision_model

        called: list[dict[str, Any]] = []

        def fake_litellm_completion(**kwargs: Any) -> object:
            called.append(kwargs)
            return _make_litellm_response("test description")

        monkeypatch.setattr("litellm.completion", fake_litellm_completion)

        # adk_ctx has NO .model attribute — old code returned stub immediately
        adk_ctx = object()
        result = _call_vision_model(
            image_bytes=b"fakepng",
            mime_type="image/png",
            prompt="Describe this.",
            adk_tool_context=adk_ctx,
        )

        # FIRING ASSERTION: litellm.completion was actually called
        assert len(called) == 1, (
            f"Expected litellm.completion to be called exactly once; "
            f"got {len(called)} calls. "
            f"Result was: {result!r}"
        )
        assert result == "test description", (
            f"Expected model output; got {result!r}. "
            "This means the fix has not been applied."
        )

    def test_call_vision_model_passes_base64_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The image bytes must be base64-encoded and passed in the messages."""
        import base64

        from magi_agent.tools.image_tools import _call_vision_model

        captured: list[dict[str, Any]] = []

        def fake_litellm_completion(**kwargs: Any) -> object:
            captured.append(kwargs)
            return _make_litellm_response("a description")

        monkeypatch.setattr("litellm.completion", fake_litellm_completion)

        raw_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        _call_vision_model(
            image_bytes=raw_bytes,
            mime_type="image/png",
            prompt="What is this?",
            adk_tool_context=object(),
        )

        assert captured, "litellm.completion not called"
        messages = captured[0].get("messages", [])
        assert messages, "No messages passed to litellm.completion"

        # Flatten to string and search for base64 payload
        content_str = str(messages)
        expected_b64 = base64.b64encode(raw_bytes).decode()
        assert expected_b64 in content_str, (
            f"Expected base64 image bytes in messages; not found. "
            f"Messages: {content_str[:200]}"
        )

    def test_call_vision_model_does_not_return_not_available_stub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The old '[vision model not available in this context]' stub must NOT appear."""
        from magi_agent.tools.image_tools import _call_vision_model

        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("a real answer"),
        )

        result = _call_vision_model(
            image_bytes=b"\x00" * 50,
            mime_type="image/png",
            prompt="Describe.",
            adk_tool_context=object(),
        )
        assert "[vision model not available in this context]" not in result, (
            f"Got old stub text: {result!r}. Fix has not been applied."
        )


# ---------------------------------------------------------------------------
# End-to-end: image_understand tool invokes litellm.completion
# ---------------------------------------------------------------------------


class TestImageUnderstandInvokesVisionModel:
    def test_image_understand_tool_invokes_vision_model(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ImageUnderstand with adk_tool_context must call litellm.completion.

        This is the primary firing test: inject a fake litellm and assert:
        1. litellm.completion is called exactly once.
        2. The tool result contains the model's output (not a stub).
        3. The image bytes appear base64-encoded in the call args.
        """
        import base64

        from magi_agent.tools.image_tools import image_understand

        # Write a minimal PNG (just enough bytes to pass the extension check)
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        called: list[dict[str, Any]] = []

        def fake_litellm_completion(**kwargs: Any) -> object:
            called.append(kwargs)
            return _make_litellm_response("A simple test image.")

        monkeypatch.setattr("litellm.completion", fake_litellm_completion)

        ctx = _ctx(tmp_path, adk_ctx=object())
        result = image_understand({"path": "test.png", "prompt": "What is this?"}, ctx)

        # Primary assertions
        assert result.status == "ok", f"Expected status=ok, got {result.status!r}"
        assert result.output["description"] == "A simple test image.", (  # type: ignore[index]
            f"Expected model output 'A simple test image.', "
            f"got {result.output['description']!r}"  # type: ignore[index]
        )

        # FIRING ASSERTION
        assert len(called) == 1, (
            f"Expected litellm.completion to be called exactly 1 time; "
            f"called {len(called)} times. "
            f"description={result.output.get('description')!r}"  # type: ignore[union-attr]
        )

        # Verify image bytes were passed
        expected_b64 = base64.b64encode(img.read_bytes()).decode()
        call_str = str(called[0])
        assert expected_b64 in call_str, (
            "Image bytes were not base64-encoded in the litellm call."
        )

    def test_image_understand_no_longer_returns_not_available_stub(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When adk_tool_context is present, stub text must not appear."""
        from magi_agent.tools.image_tools import image_understand

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("A photo of something."),
        )

        ctx = _ctx(tmp_path, adk_ctx=object())
        result = image_understand({"path": "photo.jpg"}, ctx)

        assert result.status == "ok"
        desc = result.output["description"]  # type: ignore[index]
        assert "[stub]" not in desc, f"Got stub text: {desc!r}"
        assert "[vision model not available" not in desc, (
            f"Got old stub text: {desc!r}. Fix not applied."
        )
        assert desc == "A photo of something.", (
            f"Expected model output; got {desc!r}"
        )

    def test_image_understand_without_adk_context_still_uses_litellm(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Even without adk_tool_context, the fix should call litellm (not return stub).

        After the fix: adk_tool_context is no longer the gate for vision calls.
        litellm is called regardless.
        """
        from magi_agent.tools.image_tools import image_understand

        img = tmp_path / "img.png"
        img.write_bytes(b"\x89PNG\r\n" + b"\x00" * 40)

        called: list[dict[str, Any]] = []

        def fake_litellm_completion(**kwargs: Any) -> object:
            called.append(kwargs)
            return _make_litellm_response("No context result.")

        monkeypatch.setattr("litellm.completion", fake_litellm_completion)

        # No adk_tool_context
        ctx = _ctx(tmp_path, adk_ctx=None)
        result = image_understand({"path": "img.png"}, ctx)

        assert result.status == "ok"
        assert len(called) == 1, (
            f"Expected litellm.completion to be called once even without adk_ctx; "
            f"called {len(called)} times"
        )
        assert result.output["description"] == "No context result."  # type: ignore[index]

    def test_vision_call_failure_returns_error_string_not_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If litellm.completion raises, the tool should return a graceful error string."""
        from magi_agent.tools.image_tools import image_understand

        img = tmp_path / "err.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 40)

        def bad_completion(**kwargs: Any) -> object:
            raise RuntimeError("simulated API failure")

        monkeypatch.setattr("litellm.completion", bad_completion)

        ctx = _ctx(tmp_path, adk_ctx=object())
        result = image_understand({"path": "err.png"}, ctx)

        assert result.status == "ok", (
            "Tool should not propagate exceptions from vision call"
        )
        desc = result.output["description"]  # type: ignore[index]
        assert "vision call failed" in desc or "simulated API failure" in desc, (
            f"Expected graceful error string; got {desc!r}"
        )
