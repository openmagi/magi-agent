"""C10 — default-OFF ``<coding_context>`` injector.

Spec: clawy ``docs/plans/2026-06-19-c10-coding-context-injector-spec.md`` §F.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from magi_agent.runtime.coding_context import coding_context_block


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Minimal repo-like tmp directory with one entry point + one source tree."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("print('hi')\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_demo.py").write_text("def test_x(): pass\n")
    return tmp_path


def _init_git_repo(path: Path) -> bool:
    """Init a git repo + one commit so ``git log`` returns a result.

    Returns ``False`` when git is unavailable on the runner so tests can skip.
    """
    try:
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(path)],
            check=True, capture_output=True, timeout=5.0,
        )
        subprocess.run(
            ["git", "-C", str(path), "config", "user.email", "t@t.t"],
            check=True, capture_output=True, timeout=5.0,
        )
        subprocess.run(
            ["git", "-C", str(path), "config", "user.name", "t"],
            check=True, capture_output=True, timeout=5.0,
        )
        subprocess.run(
            ["git", "-C", str(path), "add", "."],
            check=True, capture_output=True, timeout=5.0,
        )
        subprocess.run(
            ["git", "-C", str(path), "commit", "-qm", "init"],
            check=True, capture_output=True, timeout=5.0,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return True


# ---------- §F.1 — default OFF returns "" ----------

def test_default_off_returns_empty(workspace: Path) -> None:
    assert coding_context_block(workspace_root=workspace, env={}) == ""


def test_explicit_off_returns_empty(workspace: Path) -> None:
    assert coding_context_block(
        workspace_root=workspace, env={"MAGI_CODING_CONTEXT_ENABLED": "0"}
    ) == ""


# ---------- §F.2 — ENABLED=1 + workspace returns the block ----------

def test_enabled_returns_block(workspace: Path) -> None:
    block = coding_context_block(
        workspace_root=workspace, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    )
    assert block.startswith("<coding_context>")
    assert block.endswith("</coding_context>")
    assert "Workspace:" in block
    assert "pyproject.toml" in block  # entry point
    assert "src/" in block  # top-level dir stat


# ---------- §F.3 — workspace_root=None returns "" ----------

def test_no_workspace_returns_empty() -> None:
    assert coding_context_block(
        workspace_root=None, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    ) == ""


def test_nonexistent_workspace_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert coding_context_block(
        workspace_root=missing, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    ) == ""


# ---------- §F.4 — empty workspace = block w/ workspace + tip only ----------

def test_empty_workspace_returns_minimal_block(tmp_path: Path) -> None:
    block = coding_context_block(
        workspace_root=tmp_path, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    )
    assert "<coding_context>" in block
    assert "Workspace:" in block
    assert "Recently modified:" not in block
    assert "Entry points:" not in block
    assert "Top-level directories:" not in block


# ---------- §F.5 — token budget truncation ----------

def test_token_budget_truncates_lower_priority_sections(workspace: Path) -> None:
    # Pad with many top-level dirs to make Top-level directories the largest section.
    for i in range(40):
        (workspace / f"dir_{i:03d}").mkdir()
        (workspace / f"dir_{i:03d}" / "file.py").write_text("x")
    # Tiny budget — should drop content sections until only workspace+tip remain.
    block = coding_context_block(
        workspace_root=workspace,
        env={"MAGI_CODING_CONTEXT_ENABLED": "1", "MAGI_CODING_CONTEXT_TOKEN_BUDGET": "30"},
    )
    assert block.startswith("<coding_context>")
    assert "Top-level directories:" not in block
    assert "Workspace:" in block


# ---------- §F.6 — noise dirs excluded ----------

def test_noise_dirs_are_excluded(workspace: Path) -> None:
    for noise in (".git", "node_modules", "__pycache__", ".venv", "dist"):
        d = workspace / noise
        d.mkdir(exist_ok=True)
        (d / "junk").write_text("x")
    block = coding_context_block(
        workspace_root=workspace, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    )
    for noise in ("node_modules", "__pycache__", ".venv/", "dist/"):
        assert noise not in block, f"noise dir leaked: {noise}"


# ---------- §F.7 — binary files don't crash ----------

def test_binary_files_do_not_crash(workspace: Path) -> None:
    (workspace / "blob.bin").write_bytes(bytes(range(256)) * 4)
    block = coding_context_block(
        workspace_root=workspace, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    )
    assert "<coding_context>" in block


# ---------- §F.8 — git missing → recent section skipped, others kept ----------

def test_git_missing_skips_recent_keeps_rest(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise FileNotFoundError("simulated: no git on PATH")
    monkeypatch.setattr(subprocess, "run", boom)
    block = coding_context_block(
        workspace_root=workspace, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    )
    assert "Recently modified:" not in block
    assert "Entry points:" in block  # still rendered without git


# ---------- §F.9 — workspace outside repo still works (no git section) ----------

def test_non_git_workspace_renders_block(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("x = 1")
    block = coding_context_block(
        workspace_root=tmp_path, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    )
    assert "<coding_context>" in block
    assert "Recently modified:" not in block  # no git → no recent section
    assert "README.md" in block


# ---------- §F.10 — permission errors are skipped, not raised ----------

def test_permission_errors_fail_safe(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_iterdir = Path.iterdir

    def maybe_deny(self: Path):  # noqa: ANN001
        # Top-level enumeration must succeed; nested ones simulate permission denial.
        if self == workspace:
            return real_iterdir(self)
        raise PermissionError("simulated")

    monkeypatch.setattr(Path, "iterdir", maybe_deny)
    block = coding_context_block(
        workspace_root=workspace, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    )
    assert "<coding_context>" in block  # block still produced, fail-safe


# ---------- §F.11 — integration: build_cli_instruction injects when ON ----------

def test_build_cli_instruction_injects_when_on(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CODING_CONTEXT_ENABLED", "1")
    monkeypatch.chdir(workspace)
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s")
    assert "<coding_context>" in prompt


# ---------- §F.12 — integration: NOT included when OFF (default) ----------

def test_build_cli_instruction_omits_when_off(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAGI_CODING_CONTEXT_ENABLED", raising=False)
    monkeypatch.chdir(workspace)
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s")
    assert "<coding_context>" not in prompt


# ---------- bonus: real git repo path (recent section actually rendered) ----------

def test_real_git_repo_renders_recent_section(workspace: Path) -> None:
    if not _init_git_repo(workspace):
        pytest.skip("git unavailable on this runner")
    block = coding_context_block(
        workspace_root=workspace, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    )
    assert "Recently modified:" in block
    assert "pyproject.toml" in block


# ---------- fail-safe: helper that raises is swallowed ----------

def test_helper_raise_returns_empty(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_env=None):  # noqa: ANN001
        raise RuntimeError("synthetic")
    monkeypatch.setattr("magi_agent.config.env.is_coding_context_enabled", boom)
    assert coding_context_block(
        workspace_root=workspace, env={"MAGI_CODING_CONTEXT_ENABLED": "1"}
    ) == ""
