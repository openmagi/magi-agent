"""Tests for the dashboard chunks-resolvable CI gate (PR-Q).

Background: PR #1147 (v0.1.92) shipped a wheel where the committed
``magi_agent/web_dashboard`` bundle had HTML/manifests referencing
``/_next/static/chunks/<hash>.(js|css)`` files that were not in the bundle.
The existing freshness gate (``scripts/check_web_dashboard_freshness.sh``)
only enforces "apps/web/src/ changed implies magi_agent/web_dashboard/
changed" and could not catch the internal-consistency break, so the bug
survived through 0.1.93.

This module pins:

1. The new ``scripts/check_dashboard_chunks_resolvable.sh`` exists and is
   executable.
2. On a synthetic well-formed bundle, the script exits 0.
3. On a synthetic broken bundle (HTML references a chunk that is not on
   disk), the script exits non-zero and names the missing chunk.
4. The CI workflow wires the gate into the lint job alongside the existing
   freshness gate.

Pure subprocess + filesystem reads. No network, no model.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT = ROOT_DIR / "scripts" / "check_dashboard_chunks_resolvable.sh"
CI_WORKFLOW = ROOT_DIR / ".github" / "workflows" / "ci.yml"


# ---------------------------------------------------------------------------
# Surface checks: script exists, is executable, CI wires it.
# ---------------------------------------------------------------------------


def test_script_exists() -> None:
    assert SCRIPT.is_file(), "scripts/check_dashboard_chunks_resolvable.sh must exist (PR-Q)"


def test_script_is_executable() -> None:
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/check_dashboard_chunks_resolvable.sh must be executable"


def test_ci_workflow_wires_chunks_resolvable_gate() -> None:
    """CI must invoke the new gate, not just the freshness gate."""
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/check_dashboard_chunks_resolvable.sh" in ci, (
        "ci.yml must invoke the dashboard chunks-resolvable gate"
    )
    # Must run alongside (and in the same job as) the freshness gate so the
    # two bundle-integrity checks stay coupled.
    assert "scripts/check_web_dashboard_freshness.sh" in ci, "freshness gate wiring must remain"


# ---------------------------------------------------------------------------
# Behavioural tests: drive the script against synthetic bundles.
# ---------------------------------------------------------------------------


def _make_fake_bundle(root: Path, *, drop_chunks: tuple[str, ...] = ()) -> Path:
    """Build a tiny ``magi_agent/web_dashboard`` lookalike under ``root``.

    Layout:
      magi_agent/web_dashboard/
        index.html                       # references chunk-a.js, chunk-b.css
        dashboard/local/customize.txt    # references chunk-a.js, chunk-c.js
        _next/static/chunks/chunk-a.js
        _next/static/chunks/chunk-b.css
        _next/static/chunks/chunk-c.js

    ``drop_chunks`` removes named chunks AFTER the layout is written so the
    references in HTML/txt point at files that no longer exist - exactly the
    failure mode PR #1147 shipped.
    """
    bundle = root / "magi_agent" / "web_dashboard"
    chunks = bundle / "_next" / "static" / "chunks"
    chunks.mkdir(parents=True, exist_ok=True)

    (bundle / "index.html").write_text(
        "<html><head>"
        '<link rel="stylesheet" href="/_next/static/chunks/chunk-b.css">'
        '<script src="/_next/static/chunks/chunk-a.js"></script>'
        "</head><body></body></html>",
        encoding="utf-8",
    )

    sub = bundle / "dashboard" / "local"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "customize.txt").write_text(
        "/_next/static/chunks/chunk-a.js\n/_next/static/chunks/chunk-c.js\n",
        encoding="utf-8",
    )

    for name in ("chunk-a.js", "chunk-b.css", "chunk-c.js"):
        (chunks / name).write_bytes(b"// stub\n")

    for missing in drop_chunks:
        target = chunks / missing
        if target.exists():
            target.unlink()

    return bundle


def _run_script(bundle_root: Path) -> subprocess.CompletedProcess[str]:
    """Run the script with its repo layout rerooted at ``bundle_root``.

    The script computes ROOT as ``$(dirname $0)/..`` and reads
    ``$ROOT/magi_agent/web_dashboard``. We sandbox by copying the script
    into a synthetic scripts/ dir under ``bundle_root``.
    """
    scripts_dir = bundle_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    sandboxed_script = scripts_dir / SCRIPT.name
    shutil.copyfile(SCRIPT, sandboxed_script)
    sandboxed_script.chmod(0o755)
    return subprocess.run(
        ["bash", str(sandboxed_script)],
        capture_output=True,
        text=True,
        cwd=str(bundle_root),
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )


def test_script_passes_on_internally_consistent_bundle(tmp_path: Path) -> None:
    """All referenced chunks present on disk -> exit 0."""
    _make_fake_bundle(tmp_path)
    result = _run_script(tmp_path)
    assert result.returncode == 0, (
        f"well-formed bundle must pass the gate; stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_script_fails_when_referenced_chunk_is_missing(tmp_path: Path) -> None:
    """A chunk referenced from HTML but absent on disk -> non-zero exit."""
    _make_fake_bundle(tmp_path, drop_chunks=("chunk-a.js",))
    result = _run_script(tmp_path)
    assert result.returncode != 0, (
        f"missing chunk must fail the gate; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # The error message must name the missing chunk so the contributor can
    # find the bad reference quickly.
    assert "chunk-a.js" in result.stderr, (
        f"error must name the missing chunk basename; stderr={result.stderr!r}"
    )


def test_script_fails_when_referenced_css_chunk_is_missing(tmp_path: Path) -> None:
    """CSS chunks are covered, not just JS chunks (PR #1147 lost a .css too)."""
    _make_fake_bundle(tmp_path, drop_chunks=("chunk-b.css",))
    result = _run_script(tmp_path)
    assert result.returncode != 0
    assert "chunk-b.css" in result.stderr


def test_script_no_op_when_bundle_dir_missing(tmp_path: Path) -> None:
    """If the bundle dir is absent entirely (clean checkout sans build), the
    gate is a no-op rather than a false-positive failure."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    sandboxed = scripts_dir / SCRIPT.name
    shutil.copyfile(SCRIPT, sandboxed)
    sandboxed.chmod(0o755)
    result = subprocess.run(
        ["bash", str(sandboxed)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, (
        "missing bundle dir should no-op, not fail; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
