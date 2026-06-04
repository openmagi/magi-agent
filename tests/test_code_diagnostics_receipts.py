from __future__ import annotations

from dataclasses import dataclass

import pytest

from magi_agent.evidence.code_diagnostics_receipts import (
    CODE_DIAGNOSTICS_EVIDENCE_TYPE,
    CodeDiagnosticsBoundary,
)


@dataclass
class _Diag:
    line: int
    column: int
    message: str


_DIGEST = "sha256:" + "1" * 64


def test_boundary_disabled_returns_none() -> None:
    boundary = CodeDiagnosticsBoundary(enabled=False)
    record = boundary.build_record(
        checker="pyright",
        file_digest=_DIGEST,
        errors=[_Diag(1, 1, "boom")],
        cap=20,
    )
    assert record is None


def test_boundary_clean_file_returns_none() -> None:
    boundary = CodeDiagnosticsBoundary(enabled=True)
    record = boundary.build_record(
        checker="pyright",
        file_digest=_DIGEST,
        errors=[],
        cap=20,
    )
    assert record is None


def test_boundary_builds_metadata_only_record() -> None:
    boundary = CodeDiagnosticsBoundary(enabled=True)
    record = boundary.build_record(
        checker="pyright",
        file_digest=_DIGEST,
        errors=[_Diag(4, 9, "name is not defined"), _Diag(7, 1, "bad type")],
        cap=20,
    )
    assert record is not None
    assert record.type == CODE_DIAGNOSTICS_EVIDENCE_TYPE
    assert record.error_count == 2
    assert record.capped is False
    projection = record.public_projection()
    assert projection["type"] == "CodeDiagnostics"
    assert projection["fileDigest"] == _DIGEST
    assert projection["diagnosticsDigest"].startswith("sha256:")
    assert projection["entries"][0]["severity"] == "error"


def test_boundary_redacts_private_paths_in_messages() -> None:
    boundary = CodeDiagnosticsBoundary(enabled=True)
    record = boundary.build_record(
        checker="pyright",
        file_digest=_DIGEST,
        errors=[_Diag(1, 1, "cannot import /Users/kevin/secret token=abc")],
        cap=20,
    )
    assert record is not None
    message = record.public_projection()["entries"][0]["message"]
    assert "/Users/kevin" not in message
    assert "[redacted]" in message


def test_boundary_flags_capped_when_at_limit() -> None:
    boundary = CodeDiagnosticsBoundary(enabled=True)
    errors = [_Diag(i, 1, f"e{i}") for i in range(1, 21)]
    record = boundary.build_record(
        checker="pyright",
        file_digest=_DIGEST,
        errors=errors,
        cap=20,
    )
    assert record is not None
    assert record.capped is True


def test_record_rejects_non_digest_file_ref() -> None:
    boundary = CodeDiagnosticsBoundary(enabled=True)
    with pytest.raises(ValueError):
        boundary.build_record(
            checker="pyright",
            file_digest="/Users/kevin/foo.py",
            errors=[_Diag(1, 1, "x")],
            cap=20,
        )
