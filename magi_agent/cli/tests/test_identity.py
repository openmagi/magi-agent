"""Tests for cli/identity.py — self identity + project context loading.

Self identity (``soul``) is read only from the magi-owned ``.magi`` namespace
(``~/.magi`` global + ``<cwd>/.magi`` project). Repo-root ``AGENTS.md`` /
``CLAUDE.md`` are read as ``project_context`` — they describe the working repo,
not who the agent is.
"""

from __future__ import annotations

import os

from magi_agent.cli.identity import load_identity
from magi_agent.cli.tool_runtime import build_cli_instruction


def test_load_identity_reads_agents_md_as_project_context(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("repo conventions here\n", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert "agents" not in identity
    assert "repo conventions here" in identity["project_context"]


def test_load_identity_combines_repo_root_convention_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude body", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    # Repo-root files are project context, not self identity.
    assert "identity" not in identity
    assert "agents" not in identity
    context = identity["project_context"]
    assert "## AGENTS.md" in context
    assert "agents body" in context
    assert "## CLAUDE.md" in context
    assert "claude body" in context


def test_load_identity_project_context_orders_agents_before_claude(
    tmp_path, monkeypatch
) -> None:
    # Render order under PROJECT CONTEXT follows _PROJECT_CONTEXT_FILES:
    # AGENTS.md before CLAUDE.md, regardless of which content is longer.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude body", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    ctx = identity["project_context"]
    assert ctx.index("## AGENTS.md") < ctx.index("## CLAUDE.md")


def test_load_identity_soul_from_project_magi_namespace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "SOUL.md").write_text("magi soul", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["soul"] == "magi soul"


def test_load_identity_project_magi_soul_overrides_global(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    global_magi = home / ".magi"
    global_magi.mkdir(parents=True)
    (global_magi / "SOUL.md").write_text("global soul", encoding="utf-8")
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "SOUL.md").write_text("project soul", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["soul"] == "project soul"


def test_load_identity_repo_root_soul_is_not_self_identity(tmp_path, monkeypatch) -> None:
    # A SOUL.md at the repo root (outside .magi) must NOT become self identity.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "SOUL.md").write_text("repo soul", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert "soul" not in identity


def test_load_identity_missing_files_omitted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("only agents", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert "soul" not in identity
    assert "identity" not in identity
    assert "only agents" in identity["project_context"]


def test_load_identity_empty_when_nothing_present(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert load_identity(str(tmp_path)) == {}


def test_load_identity_empty_file_omitted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("   \n\n  ", encoding="utf-8")
    assert load_identity(str(tmp_path)) == {}


def test_cli_instruction_includes_project_context(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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
    # any project context files (back-compat: existing callers pass no root).
    marker = "SHOULD-NOT-APPEAR-1234"
    (tmp_path / "AGENTS.md").write_text(marker, encoding="utf-8")
    # Run from elsewhere; default-arg path must not read tmp_path.
    instruction = build_cli_instruction(session_id="s2", model="claude-sonnet-4-6")
    assert marker not in instruction
