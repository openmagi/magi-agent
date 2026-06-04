from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolHost,
    Gate5BFullToolHostConfig,
)


_HAS_RG = shutil.which("rg") is not None


def _host(workspace: Path, *, ripgrep_enabled: bool) -> Gate5BFullToolHost:
    config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "ripgrepEnabled": ripgrep_enabled,
        }
    )
    return Gate5BFullToolHost(
        config=config,
        workspace_root=workspace,
        exposed_tool_names=("Glob", "Grep"),
        now_ms=lambda: 0,
    )


def _seed(workspace: Path) -> None:
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "alpha.py").write_text(
        "def alpha():\n    return TODO_marker\n", encoding="utf-8"
    )
    (workspace / "src" / "beta.py").write_text(
        "x = 1  # nothing here\n", encoding="utf-8"
    )
    (workspace / "src" / "gamma.txt").write_text(
        "TODO_marker in text\n", encoding="utf-8"
    )
    # .git should never be returned
    (workspace / ".git").mkdir(exist_ok=True)
    (workspace / ".git" / "config").write_text("TODO_marker\n", encoding="utf-8")
    # sealed/secret file must never be returned even though it matches
    (workspace / ".env").write_text("SECRET=TODO_marker\n", encoding="utf-8")
    (workspace / "secret.txt").write_text("TODO_marker secret\n", encoding="utf-8")


async def _grep(host: Gate5BFullToolHost, pattern: str, glob: str = "**/*"):
    out = await host._handle(
        "Grep",
        {"pattern": pattern, "glob": glob},
        tool_call_id="test-grep",
    )
    return out["matches"]


async def _glob(host: Gate5BFullToolHost, pattern: str):
    out = await host._handle("Glob", {"pattern": pattern}, tool_call_id="test-glob")
    return out["matches"]


@pytest.mark.asyncio
async def test_flag_off_uses_python_substring_behavior(tmp_path):
    _seed(tmp_path)
    host = _host(tmp_path, ripgrep_enabled=False)
    # Python path is plain substring: a regex metachar searches literally.
    matches = await _grep(host, "TODO_marker")
    paths = {m["path"] for m in matches}
    assert "src/alpha.py" in paths
    assert ".git/config" not in paths
    assert ".env" not in paths
    assert "secret.txt" not in paths
    # substring path: regex alternation is treated literally -> no match
    assert await _grep(host, "alpha|beta") == []


@pytest.mark.asyncio
async def test_flag_on_but_rg_missing_falls_back_to_python(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(
        "magi_agent.coding.ripgrep.rg_available", lambda bin_path=None: False
    )
    host = _host(tmp_path, ripgrep_enabled=True)
    assert host._ripgrep_active() is False
    matches = await _grep(host, "TODO_marker")
    assert {m["path"] for m in matches} >= {"src/alpha.py"}
    # fallback is substring, so regex alternation does not match
    assert await _grep(host, "alpha|beta") == []


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
@pytest.mark.asyncio
async def test_flag_on_with_rg_uses_regex_and_excludes_policy_files(tmp_path):
    _seed(tmp_path)
    host = _host(tmp_path, ripgrep_enabled=True)
    assert host._ripgrep_active() is True
    matches = await _grep(host, "TODO_marker")
    paths = {m["path"] for m in matches}
    assert "src/alpha.py" in paths
    assert "src/gamma.txt" in paths
    # .git, sealed/secret never returned even though rg can see them
    assert ".git/config" not in paths
    assert ".env" not in paths
    assert "secret.txt" not in paths
    # real regex now works (alternation), proving not substring
    regex_paths = {m["path"] for m in await _grep(host, r"def (alpha|beta)")}
    assert "src/alpha.py" in regex_paths


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
@pytest.mark.asyncio
async def test_flag_on_with_rg_glob_mtime_descending(tmp_path):
    _seed(tmp_path)
    host = _host(tmp_path, ripgrep_enabled=True)
    # touch beta last so it should sort first by mtime desc
    now = time.time()
    import os

    os.utime(tmp_path / "src" / "alpha.py", (now - 100, now - 100))
    os.utime(tmp_path / "src" / "gamma.txt", (now - 50, now - 50))
    os.utime(tmp_path / "src" / "beta.py", (now, now))
    matches = await _glob(host, "src/*.py")
    assert matches[0] == "src/beta.py"
    assert ".git/config" not in matches
