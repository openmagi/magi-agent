import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs ANTHROPIC_API_KEY (operator-gated, costs a real API call)",
)


def test_magi_edits_a_file_via_real_runner(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    target = repo / "greet.py"
    target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )

    env = {
        **os.environ,
        "MAGI_PROVIDER": "anthropic",
        "MAGI_MODEL": os.environ.get("MAGI_MODEL", "claude-sonnet-4-6"),
    }
    proc = subprocess.run(
        [
            "magi",
            "-p",
            "Edit greet.py so greet() returns the string 'goodbye' instead of 'hello'.",
            "--output",
            "json",
            "--permission-mode",
            "bypassPermissions",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert "goodbye" in target.read_text(encoding="utf-8")
    diff = subprocess.run(
        ["git", "diff"], cwd=repo, capture_output=True, text=True, check=True
    )
    assert "goodbye" in diff.stdout
