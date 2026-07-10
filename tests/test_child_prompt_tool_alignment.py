"""Child prompt/tool alignment (Fix P): the system prompt must advertise EXACTLY
the child's forwarded tools, never a tool it cannot call.

Root cause: a tool-enabled delegated child got the FULL host tool catalog in its
prompt (via ``build_cli_instruction`` -> ``build_tool_advertisement_block`` with
a fresh all-tools registry) while its function declarations were filtered to a
readonly subset. Weak models followed the prose and called XLSXRead / BrowserTask
/ Bash they lacked, looping on tool_not_found. This restricts every tool-
advertising block to the forwarded allowlist while keeping the top-level agent
(allowlist None) byte-identical.
"""

from __future__ import annotations

import pytest

from magi_agent.cli.tool_runtime import (
    build_cli_instruction,
    build_tool_advertisement_block,
)

_READONLY = ["FileRead", "Glob", "Grep", "GitDiff", "Calculation"]


@pytest.fixture(autouse=True)
def _file_tools_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # The file-tools guidance block is what advertised XLSXRead/DocumentRead/Bash;
    # enable it so the suppression is actually exercised.
    monkeypatch.setenv("MAGI_FILE_TOOLS_ENABLED", "true")


# --- build_tool_advertisement_block --------------------------------------- #


def test_advert_none_is_full_catalog() -> None:
    block = build_tool_advertisement_block(workspace_root="/tmp")
    assert block  # non-empty on the default (all-tools) path
    assert "<available_tools>" in block


def test_advert_allowlist_restricts_to_forwarded_tools() -> None:
    block = build_tool_advertisement_block(
        workspace_root="/tmp", allowed_tool_names=frozenset(_READONLY)
    )
    assert "FileRead" in block
    # Tools NOT in the readonly allowlist must not be advertised.
    assert "XLSXRead" not in block
    assert "BrowserTask" not in block
    assert "DocumentRead" not in block


def test_advert_empty_allowlist_suppresses_block() -> None:
    # A text-only child (no forwarded tools) gets no <available_tools> block.
    assert build_tool_advertisement_block(
        workspace_root="/tmp", allowed_tool_names=frozenset()
    ) == ""


# --- build_cli_instruction ------------------------------------------------- #


def test_instruction_none_keeps_file_tools_block_verbatim() -> None:
    # Byte-identity anchor for the top-level agent path: the None-path prompt
    # must still contain the exact original <file_tools> block.
    expected_file_block = (
        "<file_tools>\n"
        "When the task involves an image, document, spreadsheet, or other "
        "attached file:\n"
        "- Use ImageUnderstand(path=..., prompt=...) for image files "
        "(.png/.jpg/.jpeg/.gif/.webp/.bmp).\n"
        "- Use DocumentRead(path=...) for document files "
        "(.pdf/.docx/.pptx/.xml/.csv/.txt/.md/.rst).\n"
        "- Use XLSXRead(path=...) for spreadsheet files (.xlsx/.xls).\n"
        "- If a tool returns status='blocked' or status='needs_approval', "
        "attempt an alternative approach: read the file with Bash (e.g. "
        "`cat`, `python3`) before concluding the file is inaccessible.\n"
        "- Never conclude 'unable to determine' solely because a tool returned "
        "an error; try at least one alternative access path first.\n"
        "</file_tools>"
    )
    instr = build_cli_instruction(session_id="s", workspace_root="/tmp")
    assert expected_file_block in instr


def test_instruction_readonly_child_suppresses_missing_tool_nudges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the multi_file_join block too (it names XLSXRead/DocumentRead).
    monkeypatch.setenv("MAGI_MULTI_FILE_JOIN_ENABLED", "true")
    instr = build_cli_instruction(
        session_id="s", workspace_root="/tmp", advertised_tool_names=_READONLY
    )
    # The child has FileRead/Glob/Grep/GitDiff/Calculation only, so the prompt
    # must NOT nudge it toward the tools it actually hallucinated in the live
    # incident (XLSXRead / DocumentRead / BrowserTask / Bash) nor SkillLoader.
    assert "XLSXRead" not in instr
    assert "XLSXInfo" not in instr
    assert "DocumentRead" not in instr
    assert "BrowserTask" not in instr
    assert "read the file with Bash" not in instr
    assert "<skills>" not in instr
    assert "<file_tools>" not in instr
    assert "<multi_file_join>" not in instr
    # A tool it DOES have may still appear.
    assert "FileRead" in instr


def test_instruction_empty_allowlist_has_no_tool_blocks() -> None:
    instr = build_cli_instruction(
        session_id="s", workspace_root="/tmp", advertised_tool_names=[]
    )
    assert "<available_tools>" not in instr
    assert "<file_tools>" not in instr
    assert "<skills>" not in instr


# --- wiring-level threading (P2: prove the passthrough, not just the leaf) --- #


def test_advertised_tool_names_threads_through_model_runner(monkeypatch) -> None:
    """A future refactor dropping the passthrough at any middle layer would pass
    the leaf-level tests above; this asserts build_cli_model_runner actually
    forwards advertised_tool_names into build_cli_instruction."""
    import magi_agent.cli.tool_runtime as tr
    import magi_agent.engine.model_runner as mr

    captured: dict[str, object] = {}

    def _fake_build_cli_instruction(**kwargs: object) -> str:
        captured["advertised_tool_names"] = kwargs.get("advertised_tool_names")
        return "STUB INSTRUCTION"

    # Stub the heavy build (imported locally inside build_cli_model_runner from
    # cli.tool_runtime) so the test stays a pure threading assertion.
    monkeypatch.setattr(tr, "build_cli_instruction", _fake_build_cli_instruction)

    from magi_agent.cli.providers import ProviderConfig

    config = ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key="sk-test")

    mr.build_cli_model_runner(
        config,
        tools=[],  # empty forwarded set
        advertised_tool_names=["FileRead", "Glob"],
        instruction=None,
        workspace_root="/tmp",
    )

    # The stubbed build_cli_instruction recorded the kwarg it received, proving
    # build_cli_model_runner forwards advertised_tool_names into the prompt build.
    assert captured["advertised_tool_names"] == ["FileRead", "Glob"]
