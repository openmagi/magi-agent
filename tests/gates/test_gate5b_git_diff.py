from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolHost,
    Gate5BFullToolHostConfig,
)


def _host(workspace: Path) -> Gate5BFullToolHost:
    config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "maxToolCallsPerTurn": 8,
        }
    )
    return Gate5BFullToolHost(
        config=config,
        workspace_root=workspace,
        exposed_tool_names=("GitDiff",),
        now_ms=lambda: 0,
    )


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(workspace: Path) -> None:
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    (workspace / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    _git(workspace, "commit", "-q", "-m", "base")


async def _diff(host: Gate5BFullToolHost) -> dict:
    return await host._handle("GitDiff", {}, tool_call_id="test-gitdiff")


@pytest.mark.asyncio
async def test_git_diff_returns_status_and_numstat(tmp_path):
    _init_repo(tmp_path)
    # Modify a tracked file and add an untracked file.
    (tmp_path / "tracked.txt").write_text("base\nmore\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("brand new\n", encoding="utf-8")

    host = _host(tmp_path)
    out = await _diff(host)

    assert out["isGitRepo"] is True
    # Porcelain status carries both the tracked modification and the untracked file.
    status = out["status"]
    assert any("tracked.txt" in line for line in status)
    assert any("new.txt" in line for line in status)
    # numstat carries the tracked modification (added lines = 1).
    numstat = out["numstat"]
    assert any(entry["path"] == "tracked.txt" for entry in numstat)
    entry = next(entry for entry in numstat if entry["path"] == "tracked.txt")
    assert entry["added"] == 1
    assert entry["deleted"] == 0


@pytest.mark.asyncio
async def test_git_diff_clean_repo_is_empty(tmp_path):
    _init_repo(tmp_path)
    host = _host(tmp_path)
    out = await _diff(host)
    assert out["isGitRepo"] is True
    assert out["status"] == []
    assert out["numstat"] == []


@pytest.mark.asyncio
async def test_git_diff_non_git_directory_is_explicit_not_fabricated(tmp_path):
    # No git init: directory is not a repository.
    host = _host(tmp_path)
    out = await _diff(host)
    assert out["isGitRepo"] is False
    assert out["status"] == []
    assert out["numstat"] == []


@pytest.mark.asyncio
async def test_git_diff_dispatch_allowlisted(tmp_path):
    _init_repo(tmp_path)
    host = _host(tmp_path)
    outcome = await host.dispatch(
        "GitDiff",
        {},
        request_digest="req",
        tool_call_id="call-1",
    )
    assert outcome.status == "ok"


@pytest.mark.asyncio
async def test_git_diff_redacts_secrets(tmp_path):
    _init_repo(tmp_path)
    # A filename containing a secret-shaped token would be redacted in output.
    secret = "ghp_" + "A" * 36
    (tmp_path / f"file-{secret}.txt").write_text("x\n", encoding="utf-8")
    host = _host(tmp_path)
    out = await _diff(host)
    rendered = "\n".join(out["status"])
    assert secret not in rendered
