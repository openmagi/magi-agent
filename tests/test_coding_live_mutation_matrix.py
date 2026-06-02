"""Validate the coding live mutation performance tracking matrix.

PR0 of the coding live mutation performance plan. This test suite ensures:
- All required row IDs are present and unique.
- Every row has defaultOff=true and productionWorkspaceMutationAllowed=false.
- Every row has at least one test proof reference.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MATRIX_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "coding_performance"
    / "coding_live_mutation_matrix.json"
)

REQUIRED_ROW_IDS: frozenset[str] = frozenset(
    {
        "gate2_relay",
        "sandbox_rollback",
        "toolhost_mutation_receipt",
        "read_before_edit",
        "stale_edit_reject",
        "diff_evidence",
        "test_evidence",
        "repair_loop",
        "final_projection",
        "benchmark",
    }
)


@pytest.fixture(scope="module")
def matrix_data() -> dict:
    """Load and return the matrix JSON."""
    raw = MATRIX_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert "rows" in data, "Matrix must have a 'rows' key"
    return data


@pytest.fixture(scope="module")
def matrix_rows(matrix_data: dict) -> list[dict]:
    """Return the rows list from the matrix."""
    return matrix_data["rows"]


# ------------------------------------------------------------------
# Structure tests
# ------------------------------------------------------------------


def test_matrix_file_exists() -> None:
    """Matrix fixture file must exist on disk."""
    assert MATRIX_PATH.exists(), f"Missing matrix fixture at {MATRIX_PATH}"


def test_matrix_is_valid_json() -> None:
    """Matrix fixture must parse as valid JSON."""
    raw = MATRIX_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert isinstance(data, dict)


def test_matrix_has_rows(matrix_data: dict) -> None:
    """Matrix must contain a non-empty rows array."""
    assert len(matrix_data["rows"]) > 0


# ------------------------------------------------------------------
# Row ID tests
# ------------------------------------------------------------------


def test_matrix_row_ids_are_unique(matrix_rows: list[dict]) -> None:
    """Every row ID must be unique within the matrix."""
    ids = [row["id"] for row in matrix_rows]
    assert len(ids) == len(set(ids)), f"Duplicate row IDs: {[x for x in ids if ids.count(x) > 1]}"


def test_matrix_contains_all_required_row_ids(matrix_rows: list[dict]) -> None:
    """Every required row ID must be present."""
    actual_ids = {row["id"] for row in matrix_rows}
    missing = REQUIRED_ROW_IDS - actual_ids
    assert not missing, f"Missing required row IDs: {missing}"


# ------------------------------------------------------------------
# Authority field tests
# ------------------------------------------------------------------


@pytest.mark.parametrize("row_id", sorted(REQUIRED_ROW_IDS))
def test_default_off_is_true(matrix_rows: list[dict], row_id: str) -> None:
    """Every required row must have defaultOff=true."""
    row = next((r for r in matrix_rows if r["id"] == row_id), None)
    assert row is not None, f"Row {row_id!r} not found"
    assert row["defaultOff"] is True, f"Row {row_id!r} defaultOff must be true, got {row['defaultOff']!r}"


@pytest.mark.parametrize("row_id", sorted(REQUIRED_ROW_IDS))
def test_production_workspace_mutation_not_allowed(matrix_rows: list[dict], row_id: str) -> None:
    """Every required row must have productionWorkspaceMutationAllowed=false."""
    row = next((r for r in matrix_rows if r["id"] == row_id), None)
    assert row is not None, f"Row {row_id!r} not found"
    assert row["productionWorkspaceMutationAllowed"] is False, (
        f"Row {row_id!r} productionWorkspaceMutationAllowed must be false, "
        f"got {row['productionWorkspaceMutationAllowed']!r}"
    )


# ------------------------------------------------------------------
# Test proof tests
# ------------------------------------------------------------------


@pytest.mark.parametrize("row_id", sorted(REQUIRED_ROW_IDS))
def test_row_has_at_least_one_test_proof(matrix_rows: list[dict], row_id: str) -> None:
    """Every required row must have at least one test proof reference."""
    row = next((r for r in matrix_rows if r["id"] == row_id), None)
    assert row is not None, f"Row {row_id!r} not found"
    proofs = row.get("testProofs", [])
    assert len(proofs) >= 1, f"Row {row_id!r} must have at least one testProof, got {len(proofs)}"


@pytest.mark.parametrize("row_id", sorted(REQUIRED_ROW_IDS))
def test_test_proofs_are_non_empty_strings(matrix_rows: list[dict], row_id: str) -> None:
    """Every test proof must be a non-empty string."""
    row = next((r for r in matrix_rows if r["id"] == row_id), None)
    assert row is not None, f"Row {row_id!r} not found"
    for proof in row.get("testProofs", []):
        assert isinstance(proof, str) and len(proof) > 0, (
            f"Row {row_id!r} has invalid testProof: {proof!r}"
        )


# ------------------------------------------------------------------
# Schema completeness tests
# ------------------------------------------------------------------


def test_every_row_has_required_fields(matrix_rows: list[dict]) -> None:
    """Every row must have id, label, description, defaultOff, productionWorkspaceMutationAllowed, testProofs."""
    required_fields = {"id", "label", "description", "defaultOff", "productionWorkspaceMutationAllowed", "testProofs"}
    for row in matrix_rows:
        missing = required_fields - set(row.keys())
        assert not missing, f"Row {row.get('id', '?')!r} missing fields: {missing}"


def test_no_production_mutation_allowed_anywhere(matrix_rows: list[dict]) -> None:
    """No row in the entire matrix may allow production workspace mutation."""
    violators = [row["id"] for row in matrix_rows if row.get("productionWorkspaceMutationAllowed") is not False]
    assert not violators, f"Rows with productionWorkspaceMutationAllowed != false: {violators}"


def test_no_default_on_anywhere(matrix_rows: list[dict]) -> None:
    """No row in the entire matrix may have defaultOff=false."""
    violators = [row["id"] for row in matrix_rows if row.get("defaultOff") is not True]
    assert not violators, f"Rows with defaultOff != true: {violators}"
