"""C-10 regression: the two pre-existing ``= 200`` public-text-clip literals
share the kernel's :data:`MAX_PUBLIC_TEXT_CHARS` constant.

Prior to C-10, ``harness/verifier_bus.py:_MAX_PUBLIC_TEXT_CHARS = 200`` and
``evidence/ledger.py:_PUBLIC_SUMMARY_MAX_STRING_LENGTH = 200`` were two
independent magic literals encoding the same concept (max public text clip).
This test pins the kernel constant as the single source of truth and asserts
the two re-pointed module-level rebinds carry the kernel value, so future
drift between the two sites is impossible.

Also pins :data:`PUBLIC_REF_RECURSION_DEPTH` as the single home for the
previously-inline ``depth > 8`` literal in
``harness/verifier_bus._collect_public_refs``.
"""

from __future__ import annotations

from magi_agent.ops.safety import (
    MAX_PUBLIC_TEXT_CHARS,
    PUBLIC_REF_RECURSION_DEPTH,
)


def test_kernel_max_public_text_chars_pinned_at_200() -> None:
    # The kernel value preserves the legacy ``= 200`` literal byte-for-byte.
    assert MAX_PUBLIC_TEXT_CHARS == 200


def test_kernel_public_ref_recursion_depth_pinned_at_8() -> None:
    # The kernel value preserves the legacy ``depth > 8`` literal byte-for-byte.
    assert PUBLIC_REF_RECURSION_DEPTH == 8


def test_verifier_bus_rebinds_kernel_clip_constant() -> None:
    """``harness/verifier_bus._MAX_PUBLIC_TEXT_CHARS`` is the kernel constant."""

    from magi_agent.harness import verifier_bus as _vb

    assert _vb._MAX_PUBLIC_TEXT_CHARS is MAX_PUBLIC_TEXT_CHARS
    assert _vb._MAX_PUBLIC_TEXT_CHARS == MAX_PUBLIC_TEXT_CHARS == 200


def test_evidence_ledger_rebinds_kernel_clip_constant() -> None:
    """``evidence/ledger._PUBLIC_SUMMARY_MAX_STRING_LENGTH`` is the kernel constant."""

    from magi_agent.evidence import ledger as _ldg

    assert _ldg._PUBLIC_SUMMARY_MAX_STRING_LENGTH is MAX_PUBLIC_TEXT_CHARS
    assert _ldg._PUBLIC_SUMMARY_MAX_STRING_LENGTH == MAX_PUBLIC_TEXT_CHARS == 200


def test_verifier_bus_rebinds_kernel_recursion_depth() -> None:
    """``harness/verifier_bus._PUBLIC_REF_RECURSION_DEPTH`` is the kernel constant."""

    from magi_agent.harness import verifier_bus as _vb

    assert _vb._PUBLIC_REF_RECURSION_DEPTH is PUBLIC_REF_RECURSION_DEPTH
    assert _vb._PUBLIC_REF_RECURSION_DEPTH == PUBLIC_REF_RECURSION_DEPTH == 8


def test_verifier_bus_sanitize_public_text_uses_kernel_clip() -> None:
    """End-to-end: the verifier-bus public-text sanitizer clips at the kernel
    constant, not at any independent literal."""

    from magi_agent.harness import verifier_bus as _vb

    sample = "a" * (MAX_PUBLIC_TEXT_CHARS + 500)
    clipped = _vb._sanitize_public_text(sample)
    assert clipped is not None
    assert len(clipped) == MAX_PUBLIC_TEXT_CHARS


def test_evidence_ledger_truncate_uses_kernel_clip() -> None:
    """End-to-end: the evidence-ledger public-summary truncator clips at the
    kernel constant, not at any independent literal."""

    from magi_agent.evidence import ledger as _ldg

    sample = "x" * (MAX_PUBLIC_TEXT_CHARS + 500)
    truncated = _ldg._truncate_public_strings(sample)
    assert isinstance(truncated, str)
    assert len(truncated) == MAX_PUBLIC_TEXT_CHARS
