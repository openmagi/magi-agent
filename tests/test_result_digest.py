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


def test_result_digest_byte_identity_with_gate5b4c3() -> None:
    """result_digest must be byte-identical to gate5b4c3_live_runner_boundary._digest.

    This is the whole point of T2: a future bridge (T3) imports result_digest
    from the public API instead of reaching into the private _digest.  If the
    two ever diverge, this test fails.
    """
    from magi_agent.runtime.public_events import result_digest  # type: ignore[attr-defined]
    from magi_agent.shadow.gate5b4c3_live_runner_boundary import _digest  # type: ignore[attr-defined]

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
        private = _digest(value)
        assert public == private, (
            f"byte-identity FAILED for {value!r}: "
            f"result_digest={public!r}, gate5b4c3._digest={private!r}"
        )


def test_result_digest_in_dunder_all() -> None:
    """result_digest must appear in public_events.__all__."""
    import magi_agent.runtime.public_events as pe  # type: ignore[attr-defined]

    assert hasattr(pe, "__all__"), "public_events must define __all__"
    assert "result_digest" in pe.__all__, (
        f"result_digest missing from __all__; current __all__={pe.__all__!r}"
    )
