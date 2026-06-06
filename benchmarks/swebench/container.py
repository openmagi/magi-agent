from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from benchmarks.swebench.dataset import Instance

# Host path to a python-3.11 venv with magi-agent[cli,providers,anthropic]
# installed, built once. Mounted read-only into each container.
MAGI_VENV_HOST = Path.home() / ".cache" / "magi-bench" / "venv"
BENCH_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class InferenceResult:
    instance_id: str
    patch: str
    log: str


def ensure_magi_venv(repo_root: Path) -> Path:
    """Create (once) an isolated py3.11 venv with the real-runner deps installed."""
    if (MAGI_VENV_HOST / "bin" / "magi").exists():
        return MAGI_VENV_HOST
    MAGI_VENV_HOST.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["python3.11", "-m", "venv", str(MAGI_VENV_HOST)], check=True)
    pip = MAGI_VENV_HOST / "bin" / "pip"
    subprocess.run(
        [str(pip), "install", "-e", f"{repo_root}[cli,providers,anthropic]"],
        check=True,
    )
    return MAGI_VENV_HOST


def instance_image(instance: Instance) -> str:
    slug = instance.instance_id.lower().replace("__", "_1776_")
    return f"swebench/sweb.eval.x86_64.{slug}:latest"


def run_instance(
    instance: Instance,
    *,
    anthropic_api_key: str,
    model: str | None,
    timeout_seconds: int,
) -> InferenceResult:
    issue_fd, issue_path = tempfile.mkstemp(suffix=".txt")
    os.close(issue_fd)
    issue_file = Path(issue_path)
    issue_file.write_text(instance.problem_statement, encoding="utf-8")
    out_dir = Path(tempfile.mkdtemp())
    out_patch = out_dir / "prediction.patch"
    out_log = out_dir / "magi.log"

    try:
        env_args = [
            "-e", f"BASE_COMMIT={instance.base_commit}",
            "-e", "MAGI_BIN=/opt/magi/bin/magi",
            "-e", "ISSUE_FILE=/work/issue.txt",
            "-e", "OUT_PATCH=/work/prediction.patch",
            "-e", "OUT_LOG=/work/magi.log",
            "-e", f"MAGI_TIMEOUT_SECONDS={timeout_seconds}",
            "-e", f"ANTHROPIC_API_KEY={anthropic_api_key}",
        ]
        if model:
            env_args += ["-e", f"MAGI_BENCH_MODEL={model}"]

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{MAGI_VENV_HOST}:/opt/magi:ro",
            "-v", f"{BENCH_DIR / 'run_one.sh'}:/work/run_one.sh:ro",
            "-v", f"{issue_file}:/work/issue.txt:ro",
            "-v", f"{out_dir}:/work",
            *env_args,
            instance_image(instance),
            "bash", "/work/run_one.sh",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_seconds + 300
            )
            stderr = proc.stderr
        except subprocess.TimeoutExpired:
            return InferenceResult(
                instance.instance_id, "", "timeout: docker wall-clock exceeded"
            )
        patch = out_patch.read_text(encoding="utf-8") if out_patch.exists() else ""
        log = (
            out_log.read_text(encoding="utf-8") if out_log.exists() else ""
        ) + stderr
        return InferenceResult(instance.instance_id, patch, log)
    finally:
        issue_file.unlink(missing_ok=True)
        shutil.rmtree(out_dir, ignore_errors=True)
