"""Tests for cli/identity.py — project identity loading + prompt threading."""

from __future__ import annotations

import os

from magi_agent.cli.identity import load_identity
from magi_agent.cli.tool_runtime import build_cli_instruction


def test_load_identity_reads_agents_md(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("repo conventions here\n", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["agents"] == "repo conventions here"


def test_load_identity_maps_all_known_files(tmp_path) -> None:
    (tmp_path / "SOUL.md").write_text("soul body", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude body", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (tmp_path / "TOOLS.md").write_text("tools body", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity == {
        "soul": "soul body",
        "identity": "claude body",
        "agents": "agents body",
        "tools": "tools body",
    }


def test_load_identity_magi_subdir_precedence(tmp_path) -> None:
    # A file in workspace_root is overridden by the same file under .magi/
    # (the .magi directory is searched second, so it wins on the last assignment).
    (tmp_path / "AGENTS.md").write_text("root agents", encoding="utf-8")
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "AGENTS.md").write_text("magi agents", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["agents"] == "magi agents"


def test_load_identity_reads_from_magi_subdir_only(tmp_path) -> None:
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "TOOLS.md").write_text("magi tools", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["tools"] == "magi tools"


def test_load_identity_missing_files_omitted(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("only agents", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert "soul" not in identity
    assert "identity" not in identity
    assert "tools" not in identity
    assert identity["agents"] == "only agents"


def test_load_identity_empty_when_nothing_present(tmp_path) -> None:
    assert load_identity(str(tmp_path)) == {}


def test_load_identity_empty_file_omitted(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("   \n\n  ", encoding="utf-8")
    assert load_identity(str(tmp_path)) == {}


def test_cli_instruction_includes_identity(tmp_path) -> None:
    marker = "UNIQUE-REPO-CONVENTION-XYZ"
    (tmp_path / "AGENTS.md").write_text(marker, encoding="utf-8")
    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
    )
    assert marker in instruction


def test_cli_instruction_without_workspace_root_omits_identity(tmp_path) -> None:
    # When no workspace_root is supplied, the instruction builds without loading
    # any project identity files (back-compat: existing callers pass no root).
    marker = "SHOULD-NOT-APPEAR-1234"
    (tmp_path / "AGENTS.md").write_text(marker, encoding="utf-8")
    # Run from elsewhere; default-arg path must not read tmp_path.
    instruction = build_cli_instruction(session_id="s2", model="claude-sonnet-4-6")
    assert marker not in instruction
