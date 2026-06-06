import os
import subprocess
from pathlib import Path

import pytest


def _provider_configured() -> bool:
    try:
        from magi_agent.cli.providers import resolve_provider_config
    except Exception:
        return False
    return resolve_provider_config() is not None


pytestmark = pytest.mark.skipif(
    not _provider_configured(),
    reason="needs a configured model provider (~/.magi/config.toml or a "
    "provider env key) — operator-gated, costs a real API call",
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

    # Provider/model come from whatever the operator configured (config.toml or
    # env) — do NOT force a provider here.
    env = {**os.environ}
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
