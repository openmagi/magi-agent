from __future__ import annotations

import os

from magi_agent.cli.identity import load_identity
from magi_agent.runtime.message_builder import build_system_prompt


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_repo_root_claude_md_is_project_context_not_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    _write(str(root / "CLAUDE.md"), "# Project: Acme\nLanguage: TypeScript 5.x")
    identity = load_identity(str(root))
    assert "identity" not in identity
    assert "project_context" in identity
    assert "TypeScript" in identity["project_context"]


def test_project_context_renders_under_project_header_not_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    _write(str(root / "CLAUDE.md"), "Telegram bot project")
    identity = load_identity(str(root))
    prompt = build_system_prompt(
        session_key="s", turn_id="t", identity=identity, coding_agent=True
    )
    assert "# PROJECT CONTEXT" in prompt
    assert "# IDENTITY" not in prompt
    assert prompt.index("You are Magi Agent") < prompt.index("Telegram bot project")


def test_self_identity_read_from_project_magi_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    _write(str(root / ".magi" / "IDENTITY.md"), "Speak tersely and precisely.")
    identity = load_identity(str(root))
    assert identity.get("identity") == "Speak tersely and precisely."


def test_self_identity_read_from_global_magi_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _write(str(home / ".magi" / "IDENTITY.md"), "Global persona tweak.")
    root = tmp_path / "repo"
    os.makedirs(str(root), exist_ok=True)
    identity = load_identity(str(root))
    assert identity.get("identity") == "Global persona tweak."


def test_project_magi_identity_overrides_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _write(str(home / ".magi" / "IDENTITY.md"), "GLOBAL")
    root = tmp_path / "repo"
    _write(str(root / ".magi" / "IDENTITY.md"), "PROJECT")
    identity = load_identity(str(root))
    assert identity.get("identity") == "PROJECT"


def test_self_identity_renders_under_identity_header(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    _write(str(root / ".magi" / "IDENTITY.md"), "I value precision.")
    identity = load_identity(str(root))
    prompt = build_system_prompt(
        session_key="s", turn_id="t", identity=identity, coding_agent=True
    )
    assert "# IDENTITY" in prompt
    assert "I value precision." in prompt
    # Self identity renders after the fixed base persona, never before it.
    assert prompt.index("You are Magi Agent") < prompt.index("# IDENTITY")
