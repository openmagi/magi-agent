"""Task 2: TDD tests for result_digest public accessor in public_events.

RED / GREEN test.  The byte-identity assertion against gate5b4c3._digest locks
that result_digest cannot silently drift from the serving module's digest
function — that parity is the entire purpose of the T2 accessor.
"""
from __future__ import annotations


def test_result_digest_startswith_sha256() -> None:
    """result_digest returns a sha256:<hex> string."""
    from magi_agent.runtime.public_events import result_digest  # type: ignore[attr-defined]

    assert result_digest({"a": 1}).startswith("sha256:"), (
        f"expected sha256: prefix, got {result_digest({'a': 1})!r}"
    )


def test_result_digest_equals_te_digest() -> None:
    """result_digest is byte-identical to _te_digest (same module, same logic)."""
    from magi_agent.runtime.public_events import (  # type: ignore[attr-defined]
        _te_digest,
        result_digest,
    )

    assert result_digest({"a": 1}) == _te_digest({"a": 1})


def test_result_digest_byte_identity_with_wire_format() -> None:
    """result_digest must be byte-identical to the retired gate5b4c3._digest.

    The retired boundary computed the digest as
    ``"sha256:" + sha256(canonical-json).hexdigest()``. That engine is gone
    (P5-M1b), so this locks the wire-shape against a recomputed reference of
    that exact format rather than the deleted private symbol. If result_digest
    ever drifts from the canonical-JSON sha256 form the hosted wire consumers
    depend on, this test fails.
    """
    import hashlib
    import json

    from magi_agent.runtime.public_events import result_digest  # type: ignore[attr-defined]

    def _reference_digest(value: object) -> str:
        # Mirrors the retired gate5b4c3._digest / _json_dumps byte-for-byte.
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=repr,
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    cases: list[object] = [
        {"a": 1},
        {"tool": "Read", "output": "hello"},
        {"nested": {"x": [1, 2, 3]}},
        "a plain string",
        42,
        None,
        [],
    ]
    for value in cases:
        public = result_digest(value)
        reference = _reference_digest(value)
        assert public == reference, (
            f"byte-identity FAILED for {value!r}: "
            f"result_digest={public!r}, wire-format reference={reference!r}"
        )


def test_result_digest_in_dunder_all() -> None:
    """result_digest must appear in public_events.__all__."""
    import magi_agent.runtime.public_events as pe  # type: ignore[attr-defined]

    assert hasattr(pe, "__all__"), "public_events must define __all__"
    assert "result_digest" in pe.__all__, (
        f"result_digest missing from __all__; current __all__={pe.__all__!r}"
    )
