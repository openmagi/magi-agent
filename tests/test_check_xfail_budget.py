"""Tests for scripts/check_xfail_budget.py (xfail budget ratchet gate).

The ratchet counts quarantined tests — inline ``xfail`` markers (the
``pytest.mark`` form; never spelled contiguously in this file so the scanner
does not count its own tests) under ``tests/`` and ``magi_agent/**/tests/``
plus nodeid entries in
``tests/ci_quarantine.txt`` (how the CI-introduction baseline is actually
expressed; see the root ``conftest.py``) — and compares the total against the
committed baseline in ``scripts/xfail_budget.txt``:

* count > baseline -> exit 1 (fix an xfail instead of adding one)
* count < baseline -> exit 1 (ratchet down: lock the win into the baseline)
* count == baseline -> exit 0

Pure file reads — no network, no model, no subprocess.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the gate module by path (it lives under scripts/, not on the package
# import path) — same pattern as tests/test_generate_env_reference.py.
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT = ROOT_DIR / "scripts" / "check_xfail_budget.py"

_spec = importlib.util.spec_from_file_location("check_xfail_budget", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_xfail_budget"] = gate
_spec.loader.exec_module(gate)

# The scanner counts occurrences of the inline marker substring in real test
# files — including THIS one. Assemble fixture content at runtime so the
# literal never appears contiguously in this file and the repo count stays
# honest (same trick as the GH013 fixture-secrets convention).
INLINE_MARKER = "pytest." + "mark." + "xfail"


def _make_repo(
    tmp_path: Path,
    *,
    inline_tests: int = 0,
    inline_pkg: int = 0,
    quarantine_ids: int = 0,
    baseline: str | None = None,
) -> Path:
    """Build a minimal repo tree for the scanner."""
    root = tmp_path / "repo"
    tests_dir = root / "tests"
    pkg_tests_dir = root / "magi_agent" / "config" / "tests"
    tests_dir.mkdir(parents=True)
    pkg_tests_dir.mkdir(parents=True)
    (root / "scripts").mkdir()

    if inline_tests:
        body = "\n".join(
            f"@{INLINE_MARKER}(reason='q{i}')\ndef test_q{i}(): ..."
            for i in range(inline_tests)
        )
        (tests_dir / "test_inline.py").write_text(body, encoding="utf-8")
    if inline_pkg:
        body = "\n".join(
            f"m{i} = {INLINE_MARKER}(reason='p{i}')" for i in range(inline_pkg)
        )
        (pkg_tests_dir / "test_pkg_inline.py").write_text(body, encoding="utf-8")

    manifest_lines = [
        "# CI baseline quarantine manifest.",
        "",
        "#reason: stale assumption. Tracked in #407",
    ]
    manifest_lines += [
        f"tests/test_quarantined.py::test_case_{i}" for i in range(quarantine_ids)
    ]
    (tests_dir / "ci_quarantine.txt").write_text(
        "\n".join(manifest_lines) + "\n", encoding="utf-8"
    )

    if baseline is not None:
        (root / "scripts" / "xfail_budget.txt").write_text(baseline, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def test_counts_inline_markers_under_both_test_roots(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, inline_tests=3, inline_pkg=2)
    assert gate.count_inline_xfails(root) == 5


def test_inline_count_includes_decorator_and_bare_marker_forms(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    mixed = (
        f"@{INLINE_MARKER}(reason='a')\ndef test_a(): ...\n"
        f"marker = {INLINE_MARKER}(reason='b')\n"
    )
    (root / "tests" / "test_mixed.py").write_text(mixed, encoding="utf-8")
    assert gate.count_inline_xfails(root) == 2


def test_inline_count_ignores_files_outside_test_roots(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, inline_tests=1)
    # Root conftest and non-tests package code must not count.
    (root / "conftest.py").write_text(
        f"m = {INLINE_MARKER}(reason='applied dynamically')", encoding="utf-8"
    )
    (root / "magi_agent" / "engine.py").write_text(
        f"# mentions {INLINE_MARKER}", encoding="utf-8"
    )
    assert gate.count_inline_xfails(root) == 1


def test_counts_quarantine_manifest_entries_skipping_comments(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, quarantine_ids=4)
    assert gate.count_quarantine_entries(root / "tests" / "ci_quarantine.txt") == 4


def test_missing_quarantine_manifest_counts_zero(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    (root / "tests" / "ci_quarantine.txt").unlink()
    assert gate.count_quarantine_entries(root / "tests" / "ci_quarantine.txt") == 0


def test_total_count_is_inline_plus_manifest(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, inline_tests=2, inline_pkg=1, quarantine_ids=4)
    assert gate.count_xfails(root) == 7


# ---------------------------------------------------------------------------
# Baseline parsing
# ---------------------------------------------------------------------------


def test_read_baseline_parses_single_integer(tmp_path: Path) -> None:
    path = tmp_path / "xfail_budget.txt"
    path.write_text("171\n", encoding="utf-8")
    assert gate.read_baseline(path) == 171


@pytest.mark.parametrize("content", ["", "abc\n", "17 1\n", "12\n34\n"])
def test_read_baseline_rejects_garbage(tmp_path: Path, content: str) -> None:
    path = tmp_path / "xfail_budget.txt"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        gate.read_baseline(path)


# ---------------------------------------------------------------------------
# Ratchet semantics (main exit codes)
# ---------------------------------------------------------------------------


def test_main_passes_when_count_equals_baseline(tmp_path: Path, capsys) -> None:
    root = _make_repo(tmp_path, inline_tests=2, quarantine_ids=3, baseline="5\n")
    assert gate.main(["--root", str(root)]) == 0


def test_main_fails_when_count_exceeds_baseline(tmp_path: Path, capsys) -> None:
    root = _make_repo(tmp_path, inline_tests=3, quarantine_ids=3, baseline="5\n")
    assert gate.main(["--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "fix an existing xfail" in err


def test_main_fails_when_count_drops_below_baseline(tmp_path: Path, capsys) -> None:
    root = _make_repo(tmp_path, inline_tests=1, quarantine_ids=3, baseline="5\n")
    assert gate.main(["--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "ratchet down: update the baseline to 4" in err


def test_main_fails_when_baseline_missing(tmp_path: Path, capsys) -> None:
    root = _make_repo(tmp_path, inline_tests=1)
    assert gate.main(["--root", str(root)]) == 1
    assert "missing" in capsys.readouterr().err.lower()


def test_main_update_writes_baseline_and_passes(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, inline_tests=2, quarantine_ids=2, baseline="9\n")
    assert gate.main(["--root", str(root), "--update"]) == 0
    baseline = (root / "scripts" / "xfail_budget.txt").read_text(encoding="utf-8")
    assert baseline == "4\n"
    # Gate is green immediately after an update.
    assert gate.main(["--root", str(root)]) == 0


# ---------------------------------------------------------------------------
# Real-repo drift guard: the committed baseline must equal the true count.
# ---------------------------------------------------------------------------


def test_committed_baseline_matches_current_repo_count() -> None:
    assert gate.main(["--root", str(ROOT_DIR)]) == 0
