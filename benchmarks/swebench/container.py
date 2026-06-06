from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from benchmarks.swebench.dataset import Instance

# Host cache dir, mounted at the SAME path (/cache) in every container so the
# relocatable uv-managed python + venv resolve identically wherever they run.
#
# A host-built venv CANNOT be mounted and executed inside the Linux instance
# containers: it embeds the host interpreter's path/ABI (doubly broken when the
# host is macOS/arm64 and the container is linux/amd64, and fragile even on
# Linux because a venv references its base interpreter's path). So instead we
# build the runtime ONCE *inside* a linux/amd64 container using uv's relocatable
# standalone CPython, into this cache, and mount the cache read-only at /cache.
MAGI_CACHE_HOST = Path.home() / ".cache" / "magi-bench"
CONTAINER_CACHE = "/cache"
MAGI_BIN_IN_CONTAINER = f"{CONTAINER_CACHE}/venv/bin/magi"
_UV_LINUX_URL = (
    "https://github.com/astral-sh/uv/releases/latest/download/"
    "uv-x86_64-unknown-linux-gnu.tar.gz"
)
BENCH_DIR = Path(__file__).resolve().parent

# Env var carrying the API key per provider (mirrors
# magi_agent.cli.providers._PROVIDER_ENV_KEYS first entry).
_PROVIDER_ENV_KEY = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
}


@dataclass(frozen=True)
class InferenceResult:
    instance_id: str
    patch: str
    log: str


def _ensure_linux_uv(cache: Path) -> Path:
    """Fetch the static linux-x86_64 ``uv`` binary into the cache (once)."""
    uv_bin = cache / "uv"
    if uv_bin.exists():
        return uv_bin
    cache.mkdir(parents=True, exist_ok=True)
    tar = cache / "uv.tar.gz"
    subprocess.run(["curl", "-fsSL", "-o", str(tar), _UV_LINUX_URL], check=True)
    subprocess.run(
        ["tar", "-xzf", str(tar), "-C", str(cache), "--strip-components=1"],
        check=True,
    )
    tar.unlink(missing_ok=True)
    uv_bin.chmod(0o755)
    return uv_bin


def ensure_magi_runtime(repo_root: Path) -> Path:
    """Build (once) a relocatable magi runtime inside a linux/amd64 container.

    Uses uv's standalone CPython + a venv, written under the host cache dir and
    always mounted back at the same ``/cache`` path so the venv resolves. The
    install is non-editable so the venv is self-contained (no source mount at
    run time). `providers` (litellm) covers all four providers; `anthropic` adds
    the SDK ADK needs for Claude.
    """
    cache = MAGI_CACHE_HOST
    if (cache / "venv" / "bin" / "magi").exists():
        return cache
    _ensure_linux_uv(cache)
    # /src is mounted read-only, but setuptools writes egg-info into the source
    # tree at build time. Copy the source (minus heavy/host-specific dirs) into a
    # writable /build first, then install from there.
    build = (
        "set -e; "
        "export UV_PYTHON_INSTALL_DIR=/cache/uv-python UV_CACHE_DIR=/cache/uv-cache; "
        "rm -rf /cache/venv /build; mkdir -p /build; "
        "tar -C /src --exclude=./.venv --exclude=./.git "
        "--exclude=./benchmarks/swebench/results -cf - . | tar -C /build -xf -; "
        "/cache/uv venv /cache/venv --python 3.11; "
        "/cache/uv pip install --python /cache/venv '/build[cli,providers,anthropic]'"
    )
    subprocess.run(
        [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{cache}:{CONTAINER_CACHE}",
            "-v", f"{repo_root}:/src:ro",
            "ubuntu:22.04", "bash", "-lc", build,
        ],
        check=True,
    )
    return cache


def instance_image(instance: Instance) -> str:
    slug = instance.instance_id.lower().replace("__", "_1776_")
    return f"swebench/sweb.eval.x86_64.{slug}:latest"


def _image_present(image: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True
        ).returncode
        == 0
    )


def pull_image(image: str, *, retries: int = 3) -> bool:
    """Pull an image with retries.

    Large instance images (hundreds of MB per layer) occasionally fail with a
    ``short read ... unexpected EOF`` mid-pull. Relying on ``docker run``'s
    implicit pull turns that transient failure into a silent empty patch, so we
    pull explicitly (idempotent) and retry before running.
    """
    if _image_present(image):
        return True
    for _ in range(retries):
        if subprocess.run(["docker", "pull", image]).returncode == 0:
            return True
    return _image_present(image)


def run_instance(
    instance: Instance,
    *,
    provider: str,
    model: str,
    api_key: str,
    timeout_seconds: int,
) -> InferenceResult:
    # Put issue.txt + run_one.sh INSIDE the single writable /work mount. Mounting
    # individual files alongside their parent dir is a nested bind mount that
    # fails on macOS Docker Desktop (virtiofs): "mountpoint is outside of rootfs".
    # One directory mount works on both macOS and Linux.
    out_dir = Path(tempfile.mkdtemp())
    (out_dir / "issue.txt").write_text(instance.problem_statement, encoding="utf-8")
    shutil.copy(BENCH_DIR / "run_one.sh", out_dir / "run_one.sh")
    out_patch = out_dir / "prediction.patch"
    out_log = out_dir / "magi.log"

    env_key = _PROVIDER_ENV_KEY.get(provider, "ANTHROPIC_API_KEY")
    try:
        image = instance_image(instance)
        if not pull_image(image):
            return InferenceResult(
                instance.instance_id, "", f"docker pull failed (retries): {image}"
            )
        env_args = [
            "-e", f"BASE_COMMIT={instance.base_commit}",
            "-e", f"MAGI_BIN={MAGI_BIN_IN_CONTAINER}",
            "-e", "ISSUE_FILE=/work/issue.txt",
            "-e", "OUT_PATCH=/work/prediction.patch",
            "-e", "OUT_LOG=/work/magi.log",
            "-e", f"MAGI_TIMEOUT_SECONDS={timeout_seconds}",
            "-e", f"MAGI_PROVIDER={provider}",
            "-e", f"MAGI_MODEL={model}",
            "-e", f"{env_key}={api_key}",
        ]

        cmd = [
            "docker", "run", "--rm",
            "--platform", "linux/amd64",
            "-v", f"{MAGI_CACHE_HOST}:{CONTAINER_CACHE}:ro",
            "-v", f"{out_dir}:/work",
            *env_args,
            image,
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
        shutil.rmtree(out_dir, ignore_errors=True)
