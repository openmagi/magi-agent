"""S-03: one secret-scrubbing kernel for public receipt sanitization.

``runtime.receipt_utils`` and ``missions.receipts`` each carried a verbatim copy
of the same credential / private-path / unsafe-marker denylist plus the
``sanitize_public_text`` scrubber. A copy that is strengthened in one module but
not the other silently leaks a secret shape through the un-updated path. This
test pins the consolidation: a single ``runtime.receipt_redaction`` leaf owns the
scrubbing primitives, both consumers reference the SAME compiled regex objects,
and the public behaviour is byte-identical to the pre-consolidation goldens.
"""

from __future__ import annotations

import re

import pytest

from magi_agent.runtime import receipt_redaction as kernel
from magi_agent.runtime import receipt_utils
from magi_agent.missions import receipts as mission_receipts


# Secret / private-path / private-line / unsafe-marker scrubbing primitives must
# live in exactly one place. Both consumers reference the kernel objects, not
# their own re-compiled copies.
_SHARED_REGEX_NAMES = (
    "_SECRET_TEXT_RE",
    "_PRIVATE_PATH_RE",
    "_RAW_PRIVATE_LINE_RE",
    "_UNSAFE_REF_MARKER_RE",
    "_SAFE_REDACTION_TOKEN_RE",
    "_SAFE_ID_RE",
)
_SHARED_CALLABLES = (
    "sha256_ref",
    "canonical_digest",
    "sanitize_public_text",
    "has_unsafe_marker",
    "string_tuple",
)


@pytest.mark.parametrize("name", _SHARED_REGEX_NAMES)
def test_consumers_share_kernel_regex_objects(name: str) -> None:
    kernel_obj = getattr(kernel, name)
    assert isinstance(kernel_obj, re.Pattern)
    assert getattr(receipt_utils, name) is kernel_obj, (
        f"runtime.receipt_utils.{name} must be the kernel object, not a fork"
    )
    assert getattr(mission_receipts, name) is kernel_obj, (
        f"missions.receipts.{name} must be the kernel object, not a fork"
    )


@pytest.mark.parametrize("name", _SHARED_CALLABLES)
def test_consumers_share_kernel_callables(name: str) -> None:
    kernel_fn = getattr(kernel, name)
    assert getattr(receipt_utils, name) is kernel_fn
    assert getattr(mission_receipts, name) is kernel_fn


# Behavioural goldens captured from the pre-consolidation implementation. The
# refactor must keep these byte-for-byte.
_SECRET_SAMPLES = [
    "Authorization: Bearer abcdef0123456789",
    "MY_API_KEY=sk-live-abcdef0123456789",
    "token ghp_abcdefABCDEF0123456789",
    "AKIAABCDEFGH01234567 leaked",
    "path /Users/kevin/secret/file and /workspace/bot/data",
    "raw_transcript line should drop\nkept line stays",
    "set-cookie: session=deadbeef\nvisible text",
]


@pytest.mark.parametrize("sample", _SECRET_SAMPLES)
def test_sanitize_public_text_parity_and_scrubs(sample: str) -> None:
    out = kernel.sanitize_public_text(sample)
    # Both consumer entry points delegate to the kernel: identical output.
    assert receipt_utils.sanitize_public_text(sample) == out
    assert mission_receipts.sanitize_public_text(sample) == out
    # And the scrub actually removed the obvious secret/path tokens.
    assert "Bearer abcdef0123456789" not in out
    assert "sk-live-abcdef0123456789" not in out
    assert "ghp_abcdefABCDEF0123456789" not in out
    assert "/Users/kevin/secret/file" not in out


def test_sanitize_public_ref_keeps_domain_namespaces() -> None:
    # runtime namespace (activity/task/turn) and mission namespace differ on
    # which ref prefixes survive as-is; both still scrub and hash-fallback.
    assert receipt_utils.sanitize_public_ref("task:build-123") == "task:build-123"
    assert mission_receipts.sanitize_public_ref("mission:launch-1") == "mission:launch-1"
    # A ref outside both the namespace allowlist and the bare-id shape (spaces,
    # punctuation) falls back to a sha256 ref, never passing through raw.
    disallowed = "Some Random Phrase!"
    assert receipt_utils.sanitize_public_ref(disallowed).startswith("ref:")
    assert mission_receipts.sanitize_public_ref(disallowed).startswith("ref:")


def test_sanitize_reason_code_defaults_differ_by_domain() -> None:
    # An unsafe reason collapses to each module's own default string.
    bad = "raw_tool_log dump"
    assert receipt_utils.sanitize_reason_code(bad) == "runtime_receipt_reason"
    assert mission_receipts.sanitize_reason_code(bad) == "mission_lifecycle_reason"
    # A clean reason is preserved identically by both.
    assert receipt_utils.sanitize_reason_code("Build Passed") == "build_passed"
    assert mission_receipts.sanitize_reason_code("Build Passed") == "build_passed"
    # missions keeps its lifecycle allowlist short-circuit.
    assert mission_receipts.sanitize_reason_code("mission_transition_denied") == (
        "mission_transition_denied"
    )


def test_canonical_digest_and_sha256_ref_shared() -> None:
    payload = {"b": 2, "a": 1}
    digest = kernel.canonical_digest(payload)
    assert digest.startswith("sha256:")
    assert receipt_utils.canonical_digest(payload) == digest
    assert mission_receipts.canonical_digest(payload) == digest
