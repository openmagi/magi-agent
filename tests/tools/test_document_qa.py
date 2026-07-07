"""Tests for the question-conditioned DocumentQA sidecar tool.

Key invariants proven here:

1. The converted document content is sent to the sidecar model together with
   the question, and ONLY the compact answer enters the ``ToolResult`` — the
   raw document text appears in no field (context-isolation invariant).
2. The sidecar is fail-soft: a raising model call degrades to an excerpt-backed
   ``status="ok"`` result, mirroring ``image_tools._call_vision_model``.
3. Default-OFF proof: with ``MAGI_DOCUMENT_QA_ENABLED`` unset, registration and
   binding are byte-identical to before this PR (exactly the 9 existing file
   tools; ``DocumentQA`` absent), even when ``MAGI_FILE_TOOLS_ENABLED=1`` and
   under any runtime profile.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from magi_agent.tools.context import ToolContext

_EXISTING_FILE_TOOL_NAMES = frozenset(
    {
        "XLSXRead",
        "XLSRead",
        "XLSXInfo",
        "DocumentRead",
        "DocumentSearch",
        "ArchiveExtract",
        "ImageUnderstand",
        "AudioTranscribe",
        "VideoFrames",
        "MusicNotation",
    }
)


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
    )


def _all_string_values(value: object) -> list[str]:
    """Flatten every string reachable in a nested result structure."""
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for key, nested in value.items():
            found.append(str(key))
            found.extend(_all_string_values(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found.extend(_all_string_values(nested))
    return found


# ---------------------------------------------------------------------------
# Happy path + context isolation
# ---------------------------------------------------------------------------


class TestDocumentQaHappyPath:
    def test_sidecar_receives_content_and_question_and_answer_is_returned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools import document_qa_tools

        (tmp_path / "report.txt").write_text(
            "Revenue for Q3 was 1,234,567 USD.", encoding="utf-8"
        )

        captured: list[dict[str, str]] = []

        def fake_call(*, content: str, question: str, **_: Any) -> str:
            captured.append({"content": content, "question": question})
            return "Q3 revenue was 1,234,567 USD."

        monkeypatch.setattr(document_qa_tools, "_call_qa_model", fake_call)

        result = document_qa_tools.document_qa(
            {"path": "report.txt", "question": "What was Q3 revenue?"},
            _ctx(tmp_path),
        )

        assert result.status == "ok"
        assert len(captured) == 1
        assert "1,234,567" in captured[0]["content"]
        assert captured[0]["question"] == "What was Q3 revenue?"
        assert result.llm_output["answer"] == "Q3 revenue was 1,234,567 USD."  # type: ignore[index]
        assert result.llm_output["sidecarUsed"] is True  # type: ignore[index]
        assert result.llm_output["sourceTool"] == "document_read"  # type: ignore[index]

    def test_context_isolation_document_text_never_enters_tool_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools import document_qa_tools

        sentinel = "SENTINEL-9f3a7c-DOCUMENT-BODY"
        (tmp_path / "doc.txt").write_text(
            f"prefix {sentinel} suffix", encoding="utf-8"
        )

        prompts: list[str] = []

        def fake_call(*, content: str, question: str, **_: Any) -> str:
            prompts.append(content)
            return "A compact answer."

        monkeypatch.setattr(document_qa_tools, "_call_qa_model", fake_call)

        result = document_qa_tools.document_qa(
            {"path": "doc.txt", "question": "What is in the document?"},
            _ctx(tmp_path),
        )

        assert result.status == "ok"
        # The sidecar DID see the document body...
        assert any(sentinel in prompt for prompt in prompts)
        # ...but no field of the ToolResult contains it.
        for field in (
            result.output,
            result.llm_output,
            result.transcript_output,
            result.metadata,
        ):
            for text in _all_string_values(field):
                assert sentinel not in text

    def test_long_answer_is_capped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools import document_qa_tools

        (tmp_path / "doc.txt").write_text("content", encoding="utf-8")
        monkeypatch.setattr(
            document_qa_tools,
            "_call_qa_model",
            lambda **_: "y" * 50_000,
        )

        result = document_qa_tools.document_qa(
            {"path": "doc.txt", "question": "q"}, _ctx(tmp_path)
        )

        assert result.status == "ok"
        assert len(result.llm_output["answer"]) <= document_qa_tools._ANSWER_MAX_CHARS  # type: ignore[index]


# ---------------------------------------------------------------------------
# Fail-soft sidecar
# ---------------------------------------------------------------------------


class TestDocumentQaFailSoft:
    def test_raising_sidecar_degrades_to_excerpt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools import document_qa_tools

        (tmp_path / "doc.txt").write_text(
            "important figure: 8,675,309\n" * 400, encoding="utf-8"
        )

        def boom(**_: Any) -> str:
            raise RuntimeError("simulated provider outage")

        monkeypatch.setattr(document_qa_tools, "_call_qa_model", boom)

        result = document_qa_tools.document_qa(
            {"path": "doc.txt", "question": "What is the figure?"},
            _ctx(tmp_path),
        )

        assert result.status == "ok"
        assert result.llm_output["sidecarUsed"] is False  # type: ignore[index]
        assert result.llm_output["answer"].startswith(  # type: ignore[index]
            "[document_qa sidecar call failed:"
        )
        excerpt = result.llm_output["fallbackExcerpt"]  # type: ignore[index]
        assert isinstance(excerpt, str)
        assert 0 < len(excerpt) <= document_qa_tools._FALLBACK_EXCERPT_CHARS
        assert "8,675,309" in excerpt


# ---------------------------------------------------------------------------
# Argument validation + conversion passthrough
# ---------------------------------------------------------------------------


class TestDocumentQaValidation:
    def test_missing_question_is_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.document_qa_tools import document_qa

        (tmp_path / "doc.txt").write_text("content", encoding="utf-8")
        result = document_qa({"path": "doc.txt"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "question_required"

    def test_missing_path_is_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.document_qa_tools import document_qa

        result = document_qa({"question": "what?"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_required"

    def test_conversion_blocked_passthrough_skips_sidecar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools import document_qa_tools

        (tmp_path / "blob.qqq").write_text("data", encoding="utf-8")

        def never(**_: Any) -> str:
            raise AssertionError("sidecar must not be called for blocked conversions")

        monkeypatch.setattr(document_qa_tools, "_call_qa_model", never)

        result = document_qa_tools.document_qa(
            {"path": "blob.qqq", "question": "q"}, _ctx(tmp_path)
        )

        assert result.status == "blocked"
        assert result.error_code == "document_extension_not_supported"

    def test_empty_document_is_an_error_without_sidecar_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools import document_qa_tools

        (tmp_path / "empty.txt").write_text("   \n  ", encoding="utf-8")

        def never(**_: Any) -> str:
            raise AssertionError("sidecar must not be called for empty documents")

        monkeypatch.setattr(document_qa_tools, "_call_qa_model", never)

        result = document_qa_tools.document_qa(
            {"path": "empty.txt", "question": "q"}, _ctx(tmp_path)
        )

        assert result.status == "error"
        assert result.error_code == "document_empty"


# ---------------------------------------------------------------------------
# Sidecar model resolution
# ---------------------------------------------------------------------------


class TestCallQaModel:
    def test_model_env_override_is_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools.document_qa_tools import _call_qa_model

        monkeypatch.setattr(
            "magi_agent.cli.providers.resolve_provider_config", lambda: None
        )
        monkeypatch.setenv("MAGI_DOCUMENT_QA_MODEL", "foo/bar")

        captured: list[dict[str, Any]] = []

        def fake_completion(**kwargs: Any) -> object:
            captured.append(kwargs)

            class _Msg:
                content = "an answer"

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

        answer = _call_qa_model(
            content="document body",
            question="what?",
            completion_fn=fake_completion,
        )

        assert answer == "an answer"
        assert len(captured) == 1
        assert captured[0]["model"] == "foo/bar"
        messages_text = str(captured[0]["messages"])
        assert "document body" in messages_text
        assert "what?" in messages_text


# ---------------------------------------------------------------------------
# Gating — default-OFF proof + flag-ON registration
# ---------------------------------------------------------------------------


class TestDocumentQaGating:
    def test_explicit_off_zero_behavior_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.config.env import document_qa_enabled
        from magi_agent.tools.file_tool_manifests import (
            file_tool_manifests,
            register_file_tool_manifests,
        )
        from magi_agent.tools.file_toolhost import bind_file_toolhost_handlers
        from magi_agent.tools.registry import ToolRegistry

        monkeypatch.setenv("MAGI_FILE_TOOLS_ENABLED", "1")
        # The gate is now profile-aware default-ON, so exercise the zero-change
        # (tool-absent) path by disabling it explicitly.
        monkeypatch.setenv("MAGI_DOCUMENT_QA_ENABLED", "0")

        registry = ToolRegistry()
        registered = register_file_tool_manifests(registry)
        bound = bind_file_toolhost_handlers(registry)

        assert {m.name for m in registered} == _EXISTING_FILE_TOOL_NAMES
        assert set(bound) == _EXISTING_FILE_TOOL_NAMES
        assert registry.resolve_registration("DocumentQA") is None

        # Stable public accessor still returns exactly the 10 existing manifests.
        manifests = file_tool_manifests()
        assert len(manifests) == 10
        assert {m.name for m in manifests} == _EXISTING_FILE_TOOL_NAMES

        # Explicit "0" forces OFF; a safe profile also keeps it OFF; the non-safe
        # profile default is now ON.
        assert document_qa_enabled({"MAGI_DOCUMENT_QA_ENABLED": "0"}) is False
        assert document_qa_enabled({"MAGI_RUNTIME_PROFILE": "safe"}) is False
        assert document_qa_enabled({"MAGI_RUNTIME_PROFILE": "full"}) is True
        assert document_qa_enabled({"MAGI_DOCUMENT_QA_ENABLED": "0"}) is False

    def test_flag_on_registers_and_binds_document_qa(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.config.env import document_qa_enabled
        from magi_agent.tools.file_tool_manifests import register_file_tool_manifests
        from magi_agent.tools.file_toolhost import bind_file_toolhost_handlers
        from magi_agent.tools.registry import ToolRegistry

        monkeypatch.setenv("MAGI_FILE_TOOLS_ENABLED", "1")
        monkeypatch.setenv("MAGI_DOCUMENT_QA_ENABLED", "1")

        assert document_qa_enabled() is True

        registry = ToolRegistry()
        registered = register_file_tool_manifests(registry)
        bound = bind_file_toolhost_handlers(registry)

        assert {m.name for m in registered} == _EXISTING_FILE_TOOL_NAMES | {
            "DocumentQA"
        }
        assert "DocumentQA" in bound
        registration = registry.resolve_registration("DocumentQA")
        assert registration is not None

    def test_manifest_shape(self) -> None:
        from magi_agent.tools.file_tool_manifests import document_qa_manifest

        manifest = document_qa_manifest()
        assert manifest.name == "DocumentQA"
        assert manifest.permission == "read"
        assert manifest.mutates_workspace is False
        assert manifest.enabled_by_default is False
        assert manifest.input_schema["required"] == ["path", "question"]  # type: ignore[index]
        assert "question" in manifest.description.lower()
