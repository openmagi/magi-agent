"""Tests for scripts/check_flag_reads.py (direct flag-read ratchet gate).

``magi_agent/config/flags.py`` is the canonical flag registry + typed reader
(flag_bool / flag_profile_bool / flag_str / flag_int), but ~82 call sites
still read ``MAGI_*``/``CORE_AGENT_*`` straight off ``os.environ``. The gate
budgets those direct reads against a committed baseline
(``scripts/flag_reads_budget.txt``) so the debt can only ratchet down:

* count > baseline -> exit 1 (route the new read through config.flags)
* count < baseline -> exit 1 (ratchet down: lock the migration in)
* count == baseline -> exit 0

Scope: ``magi_agent/**/*.py`` excluding the config allowlist (env.py,
flags.py) and test directories. Injected ``Mapping`` parameters named
``environ`` are out of scope by design — only ``os.environ`` / ``os.getenv``
forms count. Pure file reads — no network, no model, no subprocess.
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
SCRIPT = ROOT_DIR / "scripts" / "check_flag_reads.py"

_spec = importlib.util.spec_from_file_location("check_flag_reads", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_flag_reads"] = gate
_spec.loader.exec_module(gate)


def _make_repo(tmp_path: Path, *, baseline: str | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "magi_agent" / "config" / "tests").mkdir(parents=True)
    (root / "scripts").mkdir()
    if baseline is not None:
        (root / "scripts" / "flag_reads_budget.txt").write_text(
            baseline, encoding="utf-8"
        )
    return root


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Pattern coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'os.environ.get("MAGI_X_ENABLED")',
        "os.environ.get('MAGI_X_ENABLED', '')",
        'os.getenv("MAGI_X_ENABLED")',
        'os.environ["MAGI_X_ENABLED"]',
        "os.environ['CORE_AGENT_MODE']",
        'os.getenv("CORE_AGENT_MODE", "off")',
        'value = os.environ.get(\n    "MAGI_SPLIT_CALL"\n)',
    ],
)
def test_counts_each_direct_read_form(tmp_path: Path, line: str) -> None:
    root = _make_repo(tmp_path)
    _write(root, "magi_agent/engine.py", f"import os\nv = {line}\n")
    assert gate.count_direct_flag_reads(root) == 1


@pytest.mark.parametrize(
    "line",
    [
        'os.environ.get("OTHER_FLAG")',  # non-MAGI prefix
        'environ.get("MAGI_X_ENABLED")',  # injected Mapping param
        'flags.flag_bool("MAGI_X_ENABLED")',  # the canonical reader
        'os.environ.setdefault("MAGI_X_ENABLED", "1")',  # write, not read
    ],
)
def test_ignores_non_direct_read_forms(tmp_path: Path, line: str) -> None:
    root = _make_repo(tmp_path)
    _write(root, "magi_agent/engine.py", f"import os\nv = {line}\n")
    assert gate.count_direct_flag_reads(root) == 0


def test_counts_multiple_reads_in_one_file(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root,
        "magi_agent/channels/live.py",
        'import os\na = os.environ.get("MAGI_A")\nb = os.getenv("CORE_AGENT_B")\n',
    )
    assert gate.count_direct_flag_reads(root) == 2


# ---------------------------------------------------------------------------
# Scope: allowlist + tests excluded
# ---------------------------------------------------------------------------


def test_allowlisted_config_readers_do_not_count(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    read = 'import os\nv = os.environ.get("MAGI_X_ENABLED")\n'
    _write(root, "magi_agent/config/env.py", read)
    _write(root, "magi_agent/config/flags.py", read)
    assert gate.count_direct_flag_reads(root) == 0


def test_test_directories_do_not_count(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    read = 'import os\nv = os.environ.get("MAGI_X_ENABLED")\n'
    _write(root, "magi_agent/config/tests/test_env.py", read)
    _write(root, "magi_agent/runtime/tests/test_runtime.py", read)
    assert gate.count_direct_flag_reads(root) == 0


def test_files_outside_magi_agent_do_not_count(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    read = 'import os\nv = os.environ.get("MAGI_X_ENABLED")\n'
    _write(root, "benchmarks/run.py", read)
    _write(root, "scripts/tool.py", read)
    assert gate.count_direct_flag_reads(root) == 0


# ---------------------------------------------------------------------------
# Ratchet semantics (main exit codes)
# ---------------------------------------------------------------------------


def _seed_reads(root: Path, n: int) -> None:
    body = "import os\n" + "\n".join(
        f'v{i} = os.environ.get("MAGI_FLAG_{i}")' for i in range(n)
    )
    _write(root, "magi_agent/seeded.py", body + "\n")


def test_main_passes_when_count_equals_baseline(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, baseline="3\n")
    _seed_reads(root, 3)
    assert gate.main(["--root", str(root)]) == 0


def test_main_fails_above_baseline_and_names_offenders(tmp_path: Path, capsys) -> None:
    root = _make_repo(tmp_path, baseline="2\n")
    _seed_reads(root, 3)
    assert gate.main(["--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "config.flags" in err
    assert "magi_agent/seeded.py" in err


def test_main_fails_below_baseline_with_ratchet_message(tmp_path: Path, capsys) -> None:
    root = _make_repo(tmp_path, baseline="5\n")
    _seed_reads(root, 4)
    assert gate.main(["--root", str(root)]) == 1
    assert "ratchet down: update the baseline to 4" in capsys.readouterr().err


def test_main_fails_when_baseline_missing(tmp_path: Path, capsys) -> None:
    root = _make_repo(tmp_path)
    _seed_reads(root, 1)
    assert gate.main(["--root", str(root)]) == 1
    assert "missing" in capsys.readouterr().err.lower()


def test_main_update_writes_baseline_and_passes(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, baseline="9\n")
    _seed_reads(root, 2)
    assert gate.main(["--root", str(root), "--update"]) == 0
    baseline = (root / "scripts" / "flag_reads_budget.txt").read_text(encoding="utf-8")
    assert baseline == "2\n"
    assert gate.main(["--root", str(root)]) == 0


# ---------------------------------------------------------------------------
# Real-repo drift guard: the committed baseline must equal the true count.
# ---------------------------------------------------------------------------


def test_committed_baseline_matches_current_repo_count() -> None:
    assert gate.main(["--root", str(ROOT_DIR)]) == 0
