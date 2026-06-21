"""Tests for cli/identity.py — self identity + project context loading.

Self-identity files are read only from the magi-owned ``.magi`` namespace
(``~/.magi`` global + ``<cwd>/.magi`` project): BOOTSTRAP/IDENTITY/USER/LEARNING/
AGENTS.md -> bootstrap/identity/user/learning/agents slots. Repo-root
``AGENTS.md`` / ``CLAUDE.md`` are read as ``project_context`` — they describe the
working repo, not who the agent is. ``.magi/SOUL.md`` is legacy and no longer
read into the prompt.
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


def test_load_identity_identity_from_project_magi_namespace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "IDENTITY.md").write_text("magi identity", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["identity"] == "magi identity"


def test_load_identity_all_self_slots_from_magi_namespace(tmp_path, monkeypatch) -> None:
    # All five self-identity files map to their slot when present under .magi/.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "BOOTSTRAP.md").write_text("boot body", encoding="utf-8")
    (magi_dir / "IDENTITY.md").write_text("id body", encoding="utf-8")
    (magi_dir / "USER.md").write_text("user body", encoding="utf-8")
    (magi_dir / "LEARNING.md").write_text("learn body", encoding="utf-8")
    (magi_dir / "AGENTS.md").write_text("roster body", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["bootstrap"] == "boot body"
    assert identity["identity"] == "id body"
    assert identity["user"] == "user body"
    assert identity["learning"] == "learn body"
    assert identity["agents"] == "roster body"


def test_load_identity_project_magi_identity_overrides_global(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    global_magi = home / ".magi"
    global_magi.mkdir(parents=True)
    (global_magi / "IDENTITY.md").write_text("global identity", encoding="utf-8")
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "IDENTITY.md").write_text("project identity", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["identity"] == "project identity"


def test_load_identity_magi_agents_is_self_repo_root_agents_is_project(
    tmp_path, monkeypatch
) -> None:
    # ``.magi/AGENTS.md`` is the agent's OWN roster (self identity, ``agents``
    # slot); a repo-root ``AGENTS.md`` is the project's convention file. Same
    # basename, different namespace — both coexist without conflation.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "AGENTS.md").write_text("self roster", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("project conventions", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert identity["agents"] == "self roster"
    assert "project conventions" in identity["project_context"]
    assert "self roster" not in identity["project_context"]


def test_load_identity_repo_root_identity_is_not_self_identity(tmp_path, monkeypatch) -> None:
    # An IDENTITY.md at the repo root (outside .magi) must NOT become self
    # identity, and must NOT leak into project context either.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "IDENTITY.md").write_text("repo identity", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert "identity" not in identity
    assert "project_context" not in identity


def test_load_identity_legacy_soul_md_is_not_read(tmp_path, monkeypatch) -> None:
    # Legacy ``.magi/SOUL.md`` is decoupled from prompt assembly — no slot.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir()
    (magi_dir / "SOUL.md").write_text("legacy soul", encoding="utf-8")
    identity = load_identity(str(tmp_path))
    assert "soul" not in identity
    assert "legacy soul" not in repr(identity)


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
