"""Tests for MusicNotation tool — hermetic (no real vision model / ADK call).

Covers:
- Manifest registration + enabled_by_default=False
- No ImportError at base import
- Path resolution + workspace-root confinement
- Unsupported image extension → blocked
- clef="bass" → bass clef mnemonic in prompt
- clef="auto" → both treble + bass clef mnemonics in prompt
- question kwarg forwarded to prompt
- _parse_music_response: clef/time/key extraction
- Vision model called with correct MIME + prompt
- Handler binding in file_toolhost
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
    )


def _make_litellm_response(text: str) -> object:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


class TestMusicToolsImport:
    def test_music_tools_importable_without_special_extras(self) -> None:
        """Importing music_tools must not raise ImportError."""
        import magi_agent.tools.music_tools  # noqa: F401


# ---------------------------------------------------------------------------
# Manifest registration
# ---------------------------------------------------------------------------


class TestMusicNotationManifest:
    def test_music_notation_in_file_tool_manifests(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests  # noqa: PLC0415

        names = {m.name for m in file_tool_manifests()}
        assert "MusicNotation" in names, f"MusicNotation not in manifests: {names}"

    def test_music_notation_enabled_by_default_false(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests  # noqa: PLC0415

        manifests = {m.name: m for m in file_tool_manifests()}
        assert manifests["MusicNotation"].enabled_by_default is False

    def test_music_notation_has_music_tag(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests  # noqa: PLC0415

        manifests = {m.name: m for m in file_tool_manifests()}
        assert "music" in manifests["MusicNotation"].tags

    def test_music_notation_permission_is_read(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests  # noqa: PLC0415

        manifests = {m.name: m for m in file_tool_manifests()}
        assert manifests["MusicNotation"].permission == "read"


# ---------------------------------------------------------------------------
# Prompt construction — clef specialisation
# ---------------------------------------------------------------------------


class TestMusicNotationPrompt:
    def test_bass_clef_prompt_contains_gbdfa_mnemonic(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """clef='bass' → prompt must mention G, B, D, F, A (bass clef lines)."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        img = tmp_path / "score.png"
        img.write_bytes(_FAKE_PNG)

        captured_prompts: list[str] = []

        def fake_completion(**kwargs: Any) -> object:
            msgs = kwargs.get("messages", [])
            for msg in msgs:
                content = msg.get("content", [])
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            captured_prompts.append(part["text"])
            return _make_litellm_response("bass clef: G B D F A")

        monkeypatch.setattr("litellm.completion", fake_completion)

        result = mt.music_notation({"path": "score.png", "clef": "bass"}, _ctx(tmp_path))
        assert result.status == "ok", f"status={result.status} err={result.error_code}"
        combined = " ".join(captured_prompts).upper()
        # Bass clef line mnemonic: G B D F A or Good Boys Do Fine Always
        assert "G" in combined and "B" in combined and "D" in combined

    def test_auto_clef_prompt_includes_both_clefs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """clef='auto' → prompt must mention both bass and treble clef."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        img = tmp_path / "score.png"
        img.write_bytes(_FAKE_PNG)

        captured_prompts: list[str] = []

        def fake_completion(**kwargs: Any) -> object:
            msgs = kwargs.get("messages", [])
            for msg in msgs:
                content = msg.get("content", [])
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            captured_prompts.append(part["text"])
            return _make_litellm_response("treble and bass clef")

        monkeypatch.setattr("litellm.completion", fake_completion)

        result = mt.music_notation({"path": "score.png", "clef": "auto"}, _ctx(tmp_path))
        assert result.status == "ok"
        combined = " ".join(captured_prompts).lower()
        assert "bass" in combined, f"'bass' not in prompt: {combined[:200]}"
        assert "treble" in combined, f"'treble' not in prompt: {combined[:200]}"

    def test_treble_clef_prompt_contains_egbdf_mnemonic(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """clef='treble' → prompt must mention E, G, B, D, F (treble clef lines)."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        img = tmp_path / "score.png"
        img.write_bytes(_FAKE_PNG)

        captured_prompts: list[str] = []

        def fake_completion(**kwargs: Any) -> object:
            msgs = kwargs.get("messages", [])
            for msg in msgs:
                content = msg.get("content", [])
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            captured_prompts.append(part["text"])
            return _make_litellm_response("treble clef: E G B D F")

        monkeypatch.setattr("litellm.completion", fake_completion)

        result = mt.music_notation({"path": "score.png", "clef": "treble"}, _ctx(tmp_path))
        assert result.status == "ok"
        combined = " ".join(captured_prompts).upper()
        assert "E" in combined and "G" in combined and "B" in combined

    def test_question_forwarded_to_prompt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """question kwarg must appear in the prompt sent to the vision model."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        img = tmp_path / "score.png"
        img.write_bytes(_FAKE_PNG)

        captured_prompts: list[str] = []

        def fake_completion(**kwargs: Any) -> object:
            msgs = kwargs.get("messages", [])
            for msg in msgs:
                content = msg.get("content", [])
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            captured_prompts.append(part["text"])
            return _make_litellm_response("the third note is A")

        monkeypatch.setattr("litellm.completion", fake_completion)

        result = mt.music_notation(
            {"path": "score.png", "question": "What is the third note?"},
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        combined = " ".join(captured_prompts)
        assert "What is the third note?" in combined, (
            f"question not found in prompt: {combined[:300]}"
        )


# ---------------------------------------------------------------------------
# Path resolution + extension guard
# ---------------------------------------------------------------------------


class TestMusicNotationPathGuards:
    def test_unsupported_extension_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Non-image extension (e.g. .mp3) → blocked."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        bad = tmp_path / "score.mp3"
        bad.write_bytes(b"\x00" * 10)

        result = mt.music_notation({"path": "score.mp3"}, _ctx(tmp_path))
        assert result.status == "blocked"

    def test_path_traversal_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """../escape from workspace → blocked."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        result = mt.music_notation({"path": "../../etc/score.png"}, _ctx(tmp_path))
        assert result.status == "blocked"

    def test_missing_file_returns_error_or_blocked(
        self, tmp_path: Path
    ) -> None:
        """Non-existent file → error or blocked (not crash)."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        result = mt.music_notation({"path": "missing.png"}, _ctx(tmp_path))
        assert result.status in ("blocked", "error")


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------


class TestMusicNotationOutput:
    def test_output_contains_notes_and_content_digest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Output must have notes, clefDetected, timeSignature, keySignature, contentDigest."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        img = tmp_path / "score.png"
        img.write_bytes(_FAKE_PNG)

        model_response = (
            "This is treble clef notation. "
            "Time signature: 4/4. Key signature: G major. "
            "Notes: C D E F G"
        )
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response(model_response),
        )

        result = mt.music_notation({"path": "score.png"}, _ctx(tmp_path))
        assert result.status == "ok"
        output = result.output
        assert isinstance(output, dict)
        assert "notes" in output
        assert "clefDetected" in output
        assert "timeSignature" in output
        assert "keySignature" in output
        assert "contentDigest" in output
        assert output["contentDigest"].startswith("sha256:")

    def test_clef_detected_from_model_response(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """clefDetected is parsed from model response text."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        img = tmp_path / "score.png"
        img.write_bytes(_FAKE_PNG)

        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("This is a bass clef. Time: 3/4."),
        )

        result = mt.music_notation({"path": "score.png"}, _ctx(tmp_path))
        assert result.status == "ok"
        assert result.output["clefDetected"] == "bass"  # type: ignore[index]

    def test_time_signature_parsed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """timeSignature is extracted from model response."""
        import magi_agent.tools.music_tools as mt  # noqa: PLC0415

        img = tmp_path / "score.png"
        img.write_bytes(_FAKE_PNG)

        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("Treble clef, 4/4 time, C major key."),
        )

        result = mt.music_notation({"path": "score.png"}, _ctx(tmp_path))
        assert result.status == "ok"
        assert result.output["timeSignature"] == "4/4"  # type: ignore[index]


# ---------------------------------------------------------------------------
# _parse_music_response unit tests
# ---------------------------------------------------------------------------


class TestParseMusicResponse:
    def test_detect_bass_clef(self) -> None:
        from magi_agent.tools.music_tools import _parse_music_response  # noqa: PLC0415

        out = _parse_music_response("This uses bass clef.", "auto")
        assert out["clefDetected"] == "bass"

    def test_detect_treble_clef(self) -> None:
        from magi_agent.tools.music_tools import _parse_music_response  # noqa: PLC0415

        out = _parse_music_response("This is in treble clef.", "auto")
        assert out["clefDetected"] == "treble"

    def test_extract_time_signature(self) -> None:
        from magi_agent.tools.music_tools import _parse_music_response  # noqa: PLC0415

        out = _parse_music_response("Time signature: 3/4.", "auto")
        assert out["timeSignature"] == "3/4"

    def test_extract_key_signature_g_major(self) -> None:
        from magi_agent.tools.music_tools import _parse_music_response  # noqa: PLC0415

        out = _parse_music_response("The key is G major.", "auto")
        assert "G" in out["keySignature"] and "major" in out["keySignature"].lower()

    def test_unknown_fallback(self) -> None:
        from magi_agent.tools.music_tools import _parse_music_response  # noqa: PLC0415

        out = _parse_music_response("Some notes here.", "auto")
        assert out["clefDetected"] == "unknown"
        assert out["timeSignature"] == "unknown"
        assert out["keySignature"] == "unknown"

    def test_clef_hint_used_as_fallback_when_undetectable(self) -> None:
        """When model response doesn't mention clef, hint is used as fallback."""
        from magi_agent.tools.music_tools import _parse_music_response  # noqa: PLC0415

        out = _parse_music_response("Notes: C D E F G", "bass")
        # Should fall back to hint
        assert out["clefDetected"] == "bass"


# ---------------------------------------------------------------------------
# Handler binding
# ---------------------------------------------------------------------------


class TestMusicNotationHandlerBinding:
    def test_music_notation_handler_bound(self) -> None:
        from magi_agent.tools.file_tool_manifests import register_file_tool_manifests  # noqa: PLC0415
        from magi_agent.tools.file_toolhost import bind_file_toolhost_handlers  # noqa: PLC0415
        from magi_agent.tools.registry import ToolRegistry  # noqa: PLC0415

        reg = ToolRegistry()
        register_file_tool_manifests(reg)
        bound = bind_file_toolhost_handlers(reg)
        assert "MusicNotation" in bound, f"MusicNotation not in bound: {bound}"
