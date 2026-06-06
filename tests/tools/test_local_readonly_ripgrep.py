from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from magi_agent.tools import local_readonly as lro
from magi_agent.tools.context import ToolContext
from magi_agent.tools.local_readonly import LocalReadOnlyToolHost


_HAS_RG = shutil.which("rg") is not None


def _context(workspace: Path) -> ToolContext:
    return ToolContext(
        botId="bot-rg",
        userId="user-rg",
        sessionId="session-rg",
        sessionKey="ctx-rg",
        turnId="turn-rg",
        workspaceRoot=str(workspace),
    )


def _seed(workspace: Path) -> None:
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "alpha.py").write_text(
        "def alpha():\n    return TODO_marker\n", encoding="utf-8"
    )
    (workspace / "src" / "beta.py").write_text("x = 1\n", encoding="utf-8")
    (workspace / "src" / "gamma.txt").write_text(
        "TODO_marker here\n", encoding="utf-8"
    )
    (workspace / ".git").mkdir(exist_ok=True)
    (workspace / ".git" / "config").write_text("TODO_marker\n", encoding="utf-8")
    (workspace / ".env").write_text("SECRET=TODO_marker\n", encoding="utf-8")
    (workspace / "secret.txt").write_text("TODO_marker secret\n", encoding="utf-8")


def _grep(host, workspace, pattern, glob="**/*"):
    result = host.execute_tool(
        tool_name="Grep",
        arguments={"pattern": pattern, "glob": glob},
        context=_context(workspace),
    )
    return result


def _glob(host, workspace, pattern):
    return host.execute_tool(
        tool_name="Glob",
        arguments={"pattern": pattern},
        context=_context(workspace),
    )


@pytest.fixture
def host() -> LocalReadOnlyToolHost:
    return LocalReadOnlyToolHost()


def test_explicit_flag_off_uses_python_regex_grep(host, tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_RIPGREP_ENABLED", "0")
    _seed(tmp_path)
    result = _grep(host, tmp_path, r"def (alpha|beta)")
    paths = {m["path"] for m in result.output["matches"]}
    assert "src/alpha.py" in paths
    assert ".git/config" not in paths
    assert ".env" not in paths
    assert "secret.txt" not in paths


def test_flag_on_rg_missing_falls_back(host, tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_RIPGREP_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.coding.ripgrep.rg_available", lambda bin_path=None: False
    )
    _seed(tmp_path)
    assert lro._ripgrep_active() is False
    result = _grep(host, tmp_path, "TODO_marker")
    paths = {m["path"] for m in result.output["matches"]}
    assert "src/alpha.py" in paths
    assert ".env" not in paths
    assert "secret.txt" not in paths
    assert ".git/config" not in paths


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_flag_on_rg_present_excludes_policy_files(host, tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_RIPGREP_ENABLED", "1")
    _seed(tmp_path)
    assert lro._ripgrep_active() is True
    result = _grep(host, tmp_path, "TODO_marker")
    paths = {m["path"] for m in result.output["matches"]}
    assert "src/alpha.py" in paths
    assert "src/gamma.txt" in paths
    assert ".git/config" not in paths
    assert ".env" not in paths
    assert "secret.txt" not in paths
    # regex alternation works through rg-selected files
    regex = _grep(host, tmp_path, r"def (alpha|beta)")
    assert "src/alpha.py" in {m["path"] for m in regex.output["matches"]}


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_flag_on_rg_glob_mtime_descending(host, tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_RIPGREP_ENABLED", "1")
    _seed(tmp_path)
    now = time.time()
    os.utime(tmp_path / "src" / "alpha.py", (now - 100, now - 100))
    os.utime(tmp_path / "src" / "gamma.txt", (now - 50, now - 50))
    os.utime(tmp_path / "src" / "beta.py", (now, now))
    result = _glob(host, tmp_path, "src/*.py")
    paths = [m["path"] for m in result.output["matches"]]
    assert paths[0] == "src/beta.py"
    assert ".git/config" not in paths
