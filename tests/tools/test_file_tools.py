"""Tests for the optional file & multimodal tool suite.

Covers PR-F0 (manifests/registration), PR-F1 (XLSXRead), PR-F2 (DocumentRead),
PR-F3 (ImageUnderstand), PR-F4 (AudioTranscribe).

All tests are hermetic:
- No network calls.
- No live ASR / vision model calls (audio uses a mock provider; image uses stub
  path since adk_tool_context is None in tests).
- Fixtures are tiny files in tests/tools/fixtures/ created at repo bootstrap.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult

# ---------------------------------------------------------------------------
# Fixture directory
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _context(workspace_root: Path) -> ToolContext:
    return ToolContext(
        botId="bot-file-tools-test",
        sessionId="session-file-tools-test",
        turnId="turn-file-tools-test",
        workspaceRoot=str(workspace_root),
    )


def _context_no_workspace() -> ToolContext:
    return ToolContext(
        botId="bot-file-tools-test",
        sessionId="session-file-tools-test",
        turnId="turn-file-tools-test",
    )


# ===========================================================================
# PR-F0 — Manifests and registration
# ===========================================================================


class TestFileToolManifests:
    def test_four_manifests_declared(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        manifests = file_tool_manifests()
        names = {m.name for m in manifests}
        assert names == {"XLSXRead", "DocumentRead", "ImageUnderstand", "AudioTranscribe"}

    def test_all_manifests_disabled_by_default(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        for m in file_tool_manifests():
            assert not m.enabled_by_default, f"{m.name} should be disabled by default"

    def test_all_manifests_are_read_permission(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        for m in file_tool_manifests():
            assert m.permission == "read", f"{m.name} should have read permission"

    def test_all_manifests_not_dangerous(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        for m in file_tool_manifests():
            assert not m.dangerous, f"{m.name} should not be dangerous"

    def test_register_file_tool_manifests_adds_to_registry(self) -> None:
        from magi_agent.tools.file_tool_manifests import register_file_tool_manifests

        registry = ToolRegistry()
        manifests = register_file_tool_manifests(registry)
        assert len(manifests) == 4
        for m in manifests:
            assert registry.resolve(m.name) is not None

    def test_registered_tools_are_not_enabled_until_handler_bound(self) -> None:
        from magi_agent.tools.file_tool_manifests import register_file_tool_manifests

        registry = ToolRegistry()
        register_file_tool_manifests(registry)
        for name in ("XLSXRead", "DocumentRead", "ImageUnderstand", "AudioTranscribe"):
            assert not registry.is_enabled(name), f"{name} should be disabled before handler bound"

    def test_audio_transcribe_is_long_running_tool(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        manifests = {m.name: m for m in file_tool_manifests()}
        assert manifests["AudioTranscribe"].adk_tool_type == "LongRunningFunctionTool"
        assert manifests["AudioTranscribe"].should_defer

    def test_image_understand_act_mode_only(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        manifests = {m.name: m for m in file_tool_manifests()}
        assert manifests["ImageUnderstand"].available_in_modes == ("act",)

    def test_xlsx_read_plan_and_act_modes(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        manifests = {m.name: m for m in file_tool_manifests()}
        assert set(manifests["XLSXRead"].available_in_modes) == {"plan", "act"}


class TestFileToolsGating:
    def test_file_tools_full_profile_default_on_and_explicit_off(self) -> None:
        """Full local profile registers file tools unless explicitly disabled."""
        from magi_agent.config.env import file_tools_enabled

        assert not file_tools_enabled({"MAGI_FILE_TOOLS_ENABLED": "0"})
        assert not file_tools_enabled({"MAGI_FILE_TOOLS_ENABLED": "false"})
        assert not file_tools_enabled({"MAGI_RUNTIME_PROFILE": "safe"})
        assert file_tools_enabled({})

    def test_file_tools_enabled_flag_on(self) -> None:
        from magi_agent.config.env import file_tools_enabled

        assert file_tools_enabled({"MAGI_FILE_TOOLS_ENABLED": "1"})
        assert file_tools_enabled({"MAGI_FILE_TOOLS_ENABLED": "true"})
        assert file_tools_enabled({"MAGI_FILE_TOOLS_ENABLED": "yes"})

    def test_build_cli_tool_runtime_registers_file_tools_when_enabled(self) -> None:
        from magi_agent.cli.tool_runtime import build_cli_tool_runtime

        with (
            patch.dict(os.environ, {"MAGI_FILE_TOOLS_ENABLED": "true"}),
            pytest.MonkeyPatch().context() as mp,
        ):
            mp.setenv("MAGI_FILE_TOOLS_ENABLED", "true")
            runtime = build_cli_tool_runtime(workspace_root="/tmp")
            # All four file tools should be registered and enabled
            for name in ("XLSXRead", "DocumentRead", "ImageUnderstand", "AudioTranscribe"):
                assert runtime.registry.resolve(name) is not None, f"{name} not registered"
                assert runtime.registry.is_enabled(name), f"{name} not enabled"

    def test_build_cli_tool_runtime_does_not_register_file_tools_when_disabled(self) -> None:
        from magi_agent.cli.tool_runtime import build_cli_tool_runtime

        with patch.dict(os.environ, {"MAGI_FILE_TOOLS_ENABLED": "false"}):
            runtime = build_cli_tool_runtime(workspace_root="/tmp")
            for name in ("XLSXRead", "DocumentRead", "ImageUnderstand", "AudioTranscribe"):
                assert runtime.registry.resolve(name) is None, f"{name} should not be registered"


# ===========================================================================
# PR-F1 — XLSXRead
# ===========================================================================


class TestXlsxRead:
    def test_basic_read_returns_rows(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.xlsx", tmp_path / "sample.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "sample.xlsx"}, _context(tmp_path))

        assert result.status == "ok"
        assert isinstance(result.output, dict)
        rows = result.output["rows"]  # type: ignore[index]
        assert isinstance(rows, list)
        assert len(rows) > 0
        # First row should be the header row
        assert rows[0][0] == "Name"

    def test_reads_named_sheet(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.xlsx", tmp_path / "sample.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "sample.xlsx", "sheetName": "Summary"}, _context(tmp_path))

        assert result.status == "ok"
        rows = result.output["rows"]  # type: ignore[index]
        assert rows[0][0] == "Total"

    def test_nonexistent_sheet_returns_blocked(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.xlsx", tmp_path / "sample.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read(
            {"path": "sample.xlsx", "sheetName": "DoesNotExist"}, _context(tmp_path)
        )
        assert result.status == "blocked"
        assert result.error_code == "xlsx_sheet_not_found"

    def test_sensitive_column_redacted(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.xlsx", tmp_path / "sample.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "sample.xlsx"}, _context(tmp_path))

        assert result.status == "ok"
        rows = result.output["rows"]  # type: ignore[index]
        # Column header "token" is sensitive — data rows should be redacted
        header = rows[0]
        token_col = header.index("token")
        for row in rows[1:]:
            if len(row) > token_col:
                assert row[token_col] == "[redacted]", (
                    f"Expected redaction in token column, got {row[token_col]!r}"
                )
        assert result.metadata["redactionStatus"] == "redacted"

    def test_max_rows_truncation(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.xlsx", tmp_path / "sample.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "sample.xlsx", "maxRows": 2}, _context(tmp_path))

        assert result.status == "ok"
        assert result.output["rowCount"] <= 2  # type: ignore[index]
        assert result.output["truncated"] is True  # type: ignore[index]

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "../outside.xlsx"}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_escapes_workspace"

    def test_wrong_extension_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "data.csv"}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "xlsx_extension_required"

    def test_missing_path_arg_returns_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_required"

    def test_no_openpyxl_returns_blocked(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.xlsx", tmp_path / "sample.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        with patch.dict("sys.modules", {"openpyxl": None}):
            result = xlsx_read({"path": "sample.xlsx"}, _context(tmp_path))

        assert result.status == "blocked"
        assert result.error_code == "xlsx_dependency_not_installed"

    def test_content_digest_in_output(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.xlsx", tmp_path / "sample.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "sample.xlsx"}, _context(tmp_path))

        assert result.status == "ok"
        digest = result.output["contentDigest"]  # type: ignore[index]
        assert isinstance(digest, str)
        assert digest.startswith("sha256:")

    def test_workspace_root_required(self) -> None:
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "sample.xlsx"}, _context_no_workspace())
        assert result.status == "blocked"
        assert result.error_code == "workspace_root_required"


# ===========================================================================
# PR-F2 — DocumentRead
# ===========================================================================


class TestDocumentRead:
    def test_pdf_basic_read(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.pdf", tmp_path / "sample.pdf")
        from magi_agent.tools.document_tools import document_read

        result = document_read({"path": "sample.pdf"}, _context(tmp_path))

        assert result.status == "ok"
        assert isinstance(result.output, dict)
        assert "text" in result.output
        assert "contentDigest" in result.output
        digest = result.output["contentDigest"]
        assert isinstance(digest, str) and digest.startswith("sha256:")

    def test_pdf_page_range(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.pdf", tmp_path / "sample.pdf")
        from magi_agent.tools.document_tools import document_read

        result = document_read({"path": "sample.pdf", "pageRange": "1"}, _context(tmp_path))

        assert result.status == "ok"
        # pageCount should reflect full document
        assert result.output.get("pageCount") == 2  # type: ignore[union-attr]

    def test_docx_basic_read(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.docx", tmp_path / "sample.docx")
        from magi_agent.tools.document_tools import document_read

        result = document_read({"path": "sample.docx"}, _context(tmp_path))

        assert result.status == "ok"
        text = result.output["text"]  # type: ignore[index]
        assert "Hello from paragraph one." in text

    def test_docx_table_rendered_as_markdown(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.docx", tmp_path / "sample.docx")
        from magi_agent.tools.document_tools import document_read

        result = document_read({"path": "sample.docx"}, _context(tmp_path))

        assert result.status == "ok"
        text = result.output["text"]  # type: ignore[index]
        # Table headers should appear in markdown pipe format
        assert "Header A" in text
        assert "|" in text  # markdown table delimiter

    def test_unsupported_extension_blocked(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
        from magi_agent.tools.document_tools import document_read

        result = document_read({"path": "file.txt"}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "document_extension_not_supported"

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        from magi_agent.tools.document_tools import document_read

        result = document_read({"path": "../outside.pdf"}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_escapes_workspace"

    def test_missing_path_arg_returns_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.document_tools import document_read

        result = document_read({}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_required"

    def test_max_chars_truncation(self, tmp_path: Path) -> None:
        # Write a DOCX with enough text to exceed the cap.
        # Schema minimum for maxChars is 100; we use 100 as cap and 300 as content.
        from docx import Document  # type: ignore[import]

        doc = Document()
        doc.add_paragraph("A" * 300)
        doc.save(str(tmp_path / "big.docx"))

        from magi_agent.tools.document_tools import document_read

        result = document_read({"path": "big.docx", "maxChars": 100}, _context(tmp_path))
        assert result.status == "ok"
        assert len(result.output["text"]) <= 100  # type: ignore[index]
        assert result.output["truncated"] is True  # type: ignore[index]

    def test_no_pypdf_returns_blocked_for_pdf(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.pdf", tmp_path / "sample.pdf")
        from magi_agent.tools.document_tools import document_read

        with patch.dict("sys.modules", {"pypdf": None}):
            result = document_read({"path": "sample.pdf"}, _context(tmp_path))

        assert result.status == "blocked"
        assert result.error_code == "document_dependency_not_installed"

    def test_no_docx_returns_blocked_for_docx(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.docx", tmp_path / "sample.docx")
        from magi_agent.tools.document_tools import document_read

        with patch.dict("sys.modules", {"docx": None}):
            result = document_read({"path": "sample.docx"}, _context(tmp_path))

        assert result.status == "blocked"
        assert result.error_code == "document_dependency_not_installed"

    def test_workspace_root_required(self) -> None:
        from magi_agent.tools.document_tools import document_read

        result = document_read({"path": "sample.pdf"}, _context_no_workspace())
        assert result.status == "blocked"
        assert result.error_code == "workspace_root_required"


# ===========================================================================
# PR-F3 — ImageUnderstand
# ===========================================================================


class TestImageUnderstand:
    def test_png_returns_stub_description_without_adk_context(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.png", tmp_path / "sample.png")
        from magi_agent.tools.image_tools import image_understand

        result = image_understand({"path": "sample.png"}, _context(tmp_path))

        assert result.status == "ok"
        assert "description" in result.output  # type: ignore[operator]
        assert isinstance(result.output["description"], str)  # type: ignore[index]
        assert "contentDigest" in result.output  # type: ignore[operator]

    def test_unsupported_extension_blocked(self, tmp_path: Path) -> None:
        (tmp_path / "file.tiff").write_bytes(b"\x00" * 10)
        from magi_agent.tools.image_tools import image_understand

        result = image_understand({"path": "file.tiff"}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "image_extension_not_supported"

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        from magi_agent.tools.image_tools import image_understand

        result = image_understand({"path": "../outside.png"}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_escapes_workspace"

    def test_size_cap_enforced(self, tmp_path: Path) -> None:
        """Size cap is enforced by patching os.stat at the module level."""
        import shutil

        shutil.copy(_FIXTURES / "sample.png", tmp_path / "big.png")
        from magi_agent.tools.image_tools import image_understand, _MAX_IMAGE_BYTES

        _real_stat = Path.stat

        def _fake_stat(self: Path, **kwargs: object) -> object:
            result = _real_stat(self, **kwargs)
            if self.name == "big.png":
                from unittest.mock import MagicMock
                m = MagicMock(spec=result)
                m.st_size = _MAX_IMAGE_BYTES + 1
                m.st_mode = result.st_mode
                return m
            return result

        with patch.object(Path, "stat", _fake_stat):
            result = image_understand({"path": "big.png"}, _context(tmp_path))

        assert result.status == "error"
        assert result.error_code == "image_input_too_large"

    def test_missing_path_arg_returns_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.image_tools import image_understand

        result = image_understand({}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_required"

    def test_custom_prompt_accepted(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.png", tmp_path / "sample.png")
        from magi_agent.tools.image_tools import image_understand

        result = image_understand(
            {"path": "sample.png", "prompt": "What color is the pixel?"},
            _context(tmp_path),
        )
        assert result.status == "ok"
        # stub includes the prompt in the description
        assert "What color is the pixel?" in result.output["description"]  # type: ignore[index]

    def test_content_digest_present(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.png", tmp_path / "sample.png")
        from magi_agent.tools.image_tools import image_understand

        result = image_understand({"path": "sample.png"}, _context(tmp_path))
        assert result.status == "ok"
        assert result.output["contentDigest"].startswith("sha256:")  # type: ignore[index]

    def test_workspace_root_required(self) -> None:
        from magi_agent.tools.image_tools import image_understand

        result = image_understand({"path": "sample.png"}, _context_no_workspace())
        assert result.status == "blocked"
        assert result.error_code == "workspace_root_required"


# ===========================================================================
# PR-F4 — AudioTranscribe
# ===========================================================================


class TestAudioTranscribe:
    @pytest.fixture(autouse=True)
    def clear_provider_override(self) -> Generator[None, None, None]:
        """Ensure _PROVIDER_OVERRIDE is reset after each test."""
        import magi_agent.tools.audio_tools as audio_module

        original = audio_module._PROVIDER_OVERRIDE
        yield
        audio_module._PROVIDER_OVERRIDE = original

    def _set_mock_provider(self, transcript: str) -> None:
        import magi_agent.tools.audio_tools as audio_module

        class MockProvider(audio_module.AudioTranscribeProviderPort):
            def transcribe(
                self,
                audio_bytes: bytes,
                *,
                mime_type: str,
                language: str | None,
            ) -> str:
                return transcript

        audio_module._PROVIDER_OVERRIDE = MockProvider()

    def test_provider_called_returns_transcript(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.wav", tmp_path / "sample.wav")
        self._set_mock_provider("Hello world transcript.")
        from magi_agent.tools.audio_tools import audio_transcribe

        result = audio_transcribe({"path": "sample.wav"}, _context(tmp_path))

        assert result.status == "ok"
        assert result.output["transcript"] == "Hello world transcript."  # type: ignore[index]

    def test_content_digest_in_output(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.wav", tmp_path / "sample.wav")
        self._set_mock_provider("some text")
        from magi_agent.tools.audio_tools import audio_transcribe

        result = audio_transcribe({"path": "sample.wav"}, _context(tmp_path))
        assert result.status == "ok"
        assert result.output["contentDigest"].startswith("sha256:")  # type: ignore[index]

    def test_no_provider_configured_returns_blocked(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.wav", tmp_path / "sample.wav")
        from magi_agent.tools.audio_tools import audio_transcribe

        # MAGI_ASR_PROVIDER set to unknown provider, no override
        with patch.dict(os.environ, {"MAGI_ASR_PROVIDER": "unknown_provider"}):
            result = audio_transcribe({"path": "sample.wav"}, _context(tmp_path))

        assert result.status == "blocked"
        assert result.error_code == "audio_asr_provider_not_configured"

    def test_unsupported_extension_blocked(self, tmp_path: Path) -> None:
        (tmp_path / "file.avi").write_bytes(b"\x00" * 10)
        from magi_agent.tools.audio_tools import audio_transcribe

        result = audio_transcribe({"path": "file.avi"}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "audio_extension_not_supported"

    def test_size_cap_enforced(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.wav", tmp_path / "big.wav")
        from magi_agent.tools.audio_tools import audio_transcribe, _MAX_AUDIO_BYTES

        self._set_mock_provider("irrelevant")
        _real_stat = Path.stat

        def _fake_stat(self: Path, **kwargs: object) -> object:
            result = _real_stat(self, **kwargs)
            if self.name == "big.wav":
                from unittest.mock import MagicMock
                m = MagicMock(spec=result)
                m.st_size = _MAX_AUDIO_BYTES + 1
                m.st_mode = result.st_mode
                return m
            return result

        with patch.object(Path, "stat", _fake_stat):
            result = audio_transcribe({"path": "big.wav"}, _context(tmp_path))

        assert result.status == "error"
        assert result.error_code == "audio_input_too_large"

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        from magi_agent.tools.audio_tools import audio_transcribe

        result = audio_transcribe({"path": "../outside.wav"}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_escapes_workspace"

    def test_missing_path_arg_returns_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.audio_tools import audio_transcribe

        result = audio_transcribe({}, _context(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_required"

    def test_language_arg_passed_to_provider(self, tmp_path: Path) -> None:
        import shutil

        shutil.copy(_FIXTURES / "sample.wav", tmp_path / "sample.wav")
        import magi_agent.tools.audio_tools as audio_module

        received_language: list[str | None] = []

        class LangCapture(audio_module.AudioTranscribeProviderPort):
            def transcribe(
                self,
                audio_bytes: bytes,
                *,
                mime_type: str,
                language: str | None,
            ) -> str:
                received_language.append(language)
                return "bonjour"

        audio_module._PROVIDER_OVERRIDE = LangCapture()
        from magi_agent.tools.audio_tools import audio_transcribe

        result = audio_transcribe(
            {"path": "sample.wav", "language": "fr"}, _context(tmp_path)
        )
        assert result.status == "ok"
        assert received_language == ["fr"]

    def test_workspace_root_required(self) -> None:
        from magi_agent.tools.audio_tools import audio_transcribe

        result = audio_transcribe({"path": "sample.wav"}, _context_no_workspace())
        assert result.status == "blocked"
        assert result.error_code == "workspace_root_required"
