from __future__ import annotations

import json
from pathlib import Path

from magi_agent.shadow.research_runner_capture import (
    RESEARCH_CAPTURE_DEFAULT_ENABLED,
    build_local_sample_capture,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/e2e_harness_parity_matrix.json"


def _pr22_row() -> dict[str, object]:
    matrix = json.loads(MATRIX_PATH.read_text())
    for row in matrix["rows"]:
        if row["id"] == "research_benchmark_eval_capture":
            return row
    raise AssertionError("research_benchmark_eval_capture row missing")


def test_pr22_research_benchmark_eval_capture_is_activation_blocked_local_only(
    tmp_path: Path,
) -> None:
    capture = build_local_sample_capture(tmp_path)
    row = _pr22_row()

    assert RESEARCH_CAPTURE_DEFAULT_ENABLED is False
    assert capture.run_path == tmp_path / "python-adk-research-run.json"
    assert capture.artifacts_path == tmp_path / "python-adk-research-artifacts.jsonl"
    assert row["status"] == "activation_blocked"
    assert row["missingImplementation"] == []
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False


def test_pr22_matrix_row_references_capture_module_and_tests() -> None:
    row = _pr22_row()
    refs = [ref["path"] for ref in row["latestMainCoveredRefs"]]

    assert "magi_agent/shadow/research_runner_capture.py" in refs
    assert "tests/test_research_runner_capture.py" in refs
    assert "tests/test_e2e_harness_pr22_research_benchmark_eval_capture.py" in refs
    assert "research-artifact-v1" in row["notes"]
    assert "local-only" in row["notes"]
