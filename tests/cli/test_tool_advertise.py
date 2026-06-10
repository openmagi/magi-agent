"""TDD tests for registry-driven tool advertising in build_cli_instruction (Principle P2).

Merged tools stayed dormant because the system prompt never named them.
``build_cli_instruction`` must now auto-generate an <available_tools> section
from the set of *attached* (enabled) tools so the model can discover them.

Tests:
  - Core tools (FileRead, Bash) appear in the section.
  - File tools (ImageUnderstand, DocumentRead) appear only when
    MAGI_FILE_TOOLS_ENABLED is set, absent otherwise.
  - A tool that is NOT attached never appears in the section.
  - The section is a proper XML-tagged block (<available_tools>...</available_tools>).
  - The description from the manifest is included for each tool.
  - No duplicate section when called twice (idempotent structure check is an
    assembly regression, not a double-call check — the block is built fresh each
    call, so this test verifies the header appears exactly once per call).
"""

from __future__ import annotations

import pytest

from pathlib import Path

from magi_agent.cli.tool_runtime import build_cli_instruction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _instruction(
    monkeypatch: pytest.MonkeyPatch,
    *,
    file_tools: bool = False,
    browser_tool: bool = False,
    self_introspection: bool = False,
    workspace_root: Path | None = None,
) -> str:
    if file_tools:
        monkeypatch.setenv("MAGI_FILE_TOOLS_ENABLED", "true")
    else:
        # Explicitly set to "false" so file_tools_enabled() returns False
        # regardless of the runtime profile default.
        monkeypatch.setenv("MAGI_FILE_TOOLS_ENABLED", "false")
    monkeypatch.setenv("MAGI_BROWSER_TOOL_ENABLED", "true" if browser_tool else "false")
    monkeypatch.setenv(
        "MAGI_SELF_INTROSPECTION_ENABLED",
        "true" if self_introspection else "false",
    )
    return build_cli_instruction(
        session_id="test-session",
        workspace_root=str(workspace_root) if workspace_root is not None else None,
    )


# ---------------------------------------------------------------------------
# Core tools always appear
# ---------------------------------------------------------------------------


def test_file_read_appears_in_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch)
    assert "FileRead" in instruction


def test_bash_appears_in_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch)
    assert "Bash" in instruction


def test_core_tool_description_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """Manifest description text is present in the instruction."""
    instruction = _instruction(monkeypatch)
    # Description from catalog.py: "Read workspace file contents."
    assert "Read workspace file contents" in instruction


def test_bash_description_included(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch)
    # Description from catalog.py: "Run a shell command in the workspace."
    assert "Run a shell command in the workspace" in instruction


# ---------------------------------------------------------------------------
# Available-tools block structure
# ---------------------------------------------------------------------------


def test_available_tools_xml_block_present(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch)
    assert "<available_tools>" in instruction
    assert "</available_tools>" in instruction


def test_available_tools_block_appears_once(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch)
    assert instruction.count("<available_tools>") == 1


# ---------------------------------------------------------------------------
# File tools are conditional on MAGI_FILE_TOOLS_ENABLED
# ---------------------------------------------------------------------------


def test_image_understand_absent_without_file_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch, file_tools=False)
    assert "ImageUnderstand" not in instruction


def test_document_read_absent_without_file_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch, file_tools=False)
    assert "DocumentRead" not in instruction


def test_image_understand_present_with_file_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch, file_tools=True)
    assert "ImageUnderstand" in instruction


def test_document_read_present_with_file_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch, file_tools=True)
    assert "DocumentRead" in instruction


def test_file_tool_description_present_with_file_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction(monkeypatch, file_tools=True)
    # Description from file_tool_manifests.py
    assert "Describe or answer a question about an image file" in instruction


# ---------------------------------------------------------------------------
# Tools not attached must not appear in the advertising block
# ---------------------------------------------------------------------------


def test_non_attached_tool_not_advertised(monkeypatch: pytest.MonkeyPatch) -> None:
    """BrowserTask is never registered unless MAGI_BROWSER_TOOL_ENABLED; absent."""
    instruction = _instruction(monkeypatch, file_tools=False)
    assert "BrowserTask" not in instruction


def test_xlsx_info_absent_without_file_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """XLSXInfo is a file tool — must not leak into the prompt when gate is off."""
    instruction = _instruction(monkeypatch, file_tools=False)
    assert "XLSXInfo" not in instruction


def test_browser_task_present_when_runtime_binds_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    instruction = _instruction(
        monkeypatch,
        browser_tool=True,
        workspace_root=tmp_path,
    )
    assert "BrowserTask" in instruction


def test_inspect_self_evidence_present_when_runtime_binds_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    instruction = _instruction(
        monkeypatch,
        self_introspection=True,
        workspace_root=tmp_path,
    )
    assert "InspectSelfEvidence" in instruction
