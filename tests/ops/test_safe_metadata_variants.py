"""C-2: reconcile two ``safe_metadata`` definitions sharing a name + opposite
fail-modes on the redaction boundary.

Before C-2 there were two ``safe_metadata`` symbols in the tree:

- ``magi_agent.ops.safety.safe_metadata`` — strict allow-list / fail-closed:
  raises ``ValueError`` on any suspicious key or non-digest/non-safe-ref value.
- ``magi_agent.web_acquisition.policy.safe_metadata`` — lenient deny-list /
  fail-open: drops denylisted keys silently, never raises, redacts string
  values. Consumed by the *live* web result projections.

Same name, opposite guarantees, on a redaction boundary — silent-weakening
hazard. C-2 keeps the strict variant's name (``safe_metadata``) and gives the
lenient one an explicit kernel name (``public_diagnostic_metadata``). The web
policy entry is a one-line re-export so existing imports keep working.

This file pins the contract on both symbols and validates byte-equivalence for
clean payloads (the divergence direction is the C-1 superset: kernel catches
strictly *more* secret shapes; finite-only is the documented tightening).
"""

from __future__ import annotations

import math

import pytest

from magi_agent.ops.safety import (
    PUBLIC_DIAGNOSTIC_KEY_MARKERS,
    public_diagnostic_metadata,
    safe_metadata,
)
from magi_agent.web_acquisition.policy import safe_metadata as policy_safe_metadata


# -- Strict variant: behavior unchanged -----------------------------------


def test_strict_safe_metadata_raises_on_suspicious_key() -> None:
    """The strict (allow-list) variant raises on any suspicious key. This is
    the post-C-2 invariant for ``safe_metadata`` (no other safe_metadata symbol
    exists tree-wide; see test_no_forked_safe_metadata.py)."""
    with pytest.raises(ValueError):
        safe_metadata({"token": "x"})


def test_strict_safe_metadata_raises_on_non_safe_value() -> None:
    with pytest.raises(ValueError):
        # whitespace breaks SAFE_REF_RE -> fail-closed.
        safe_metadata({"label": "has spaces here"})


# -- Lenient variant: never raises, drops + redacts -----------------------


def test_public_diagnostic_metadata_drops_suspicious_key_no_raise() -> None:
    """The lenient variant drops marker-matching keys silently."""
    result = public_diagnostic_metadata({"token": "x"})
    assert result == {}


def test_public_diagnostic_metadata_redacts_secret_in_value() -> None:
    """Values containing credential shapes go through the C-1 redaction kernel
    (``redact_private_text``), substituting the credential with ``[redacted]``."""
    out = public_diagnostic_metadata({"host": "Bearer AKIAEXAMPLE12345678 hi"})
    # 'Bearer …' is redacted; the trailing ' hi' survives.
    assert out == {"host": "[redacted] hi"}


def test_public_diagnostic_metadata_redacts_provider_token_in_value() -> None:
    """Short provider tokens (sk-…, AIza…) the legacy
    ``redact_public_text`` denylist missed are caught by the C-1 kernel."""
    out = public_diagnostic_metadata({"host": "sk-abc123"})
    assert out == {"host": "[redacted]"}


def test_public_diagnostic_metadata_keeps_finite_primitives() -> None:
    """int / finite float / bool / None pass through. Non-finite floats are
    dropped (C-2 tightening over the legacy lenient copy)."""
    out = public_diagnostic_metadata(
        {
            "depth": 3,
            "ratio": 0.5,
            "okay": True,
            "extra": None,
            "ceiling": False,
        }
    )
    assert out == {
        "depth": 3,
        "ratio": 0.5,
        "okay": True,
        "extra": None,
        "ceiling": False,
    }


def test_public_diagnostic_metadata_drops_non_finite_floats() -> None:
    """C-2 tightening: ``float('inf')`` / NaN are dropped. The legacy lenient
    copy kept them (it had no isfinite check). The strict variant raises
    instead; the lenient variant must NEVER raise, so we drop."""
    out = public_diagnostic_metadata(
        {"rate": float("inf"), "nan": math.nan, "ok": 1}
    )
    assert out == {"ok": 1}


def test_public_diagnostic_metadata_passes_isinstance_guard() -> None:
    """Non-Mapping input returns ``{}`` (matches legacy policy.safe_metadata)."""
    assert public_diagnostic_metadata("not-a-dict") == {}  # type: ignore[arg-type]
    assert public_diagnostic_metadata(None) == {}  # type: ignore[arg-type]
    assert public_diagnostic_metadata([1, 2, 3]) == {}  # type: ignore[arg-type]


def test_public_diagnostic_metadata_drops_nested_containers() -> None:
    """Lists / dicts / tuples are silently dropped — fail-open (the strict
    variant accepts tuples + lists of safe primitives; the lenient one is more
    conservative and drops them)."""
    out = public_diagnostic_metadata(
        {"nested": {"x": 1}, "items": [1, 2, 3], "count": 3}
    )
    assert out == {"count": 3}


def test_public_diagnostic_metadata_clips_long_strings() -> None:
    """The 512-char clip from legacy ``redact_public_text(…, 512)`` is
    preserved through ``redact_private_text(…, max_chars=512)``."""
    out = public_diagnostic_metadata({"host": "x" * 600})
    assert isinstance(out["host"], str)
    assert len(out["host"]) == 512


def test_public_diagnostic_key_markers_includes_legacy_set() -> None:
    """Sanity: the kernel marker set still has all the substrings the legacy
    policy.py marker tuple had. (Defends against accidental marker loss.)"""
    legacy = frozenset(
        {
            "raw",
            "secret",
            "token",
            "key",
            "cookie",
            "auth",
            "credential",
            "authoritative",
            "trust",
            "trusted",
            "verified",
            "valid",
            "path",
            "log",
            "debug",
            "trace",
            "provider",
            "request",
            "response",
            "production",
            "attached",
            "enabled",
            "allowed",
            "performed",
            "authority",
            "route",
            "called",
            "fetched",
            "executed",
            "injected",
            "network",
        }
    )
    assert legacy <= PUBLIC_DIAGNOSTIC_KEY_MARKERS


# -- Web policy re-export shim: byte-equivalent for clean inputs ----------

# Golden equivalence anchor. The values below were captured by running the
# pre-C-2 ``web_acquisition.policy.safe_metadata`` against the same input
# (see commit body for the capture procedure). They are byte-identical to
# the post-C-2 re-export. Case-13 line-drop semantic is preserved via a
# kernel-side ``_RAW_PRIVATE_LINE_RE`` pass; the kernel is now strictly more
# redactive than the legacy on all sampled shapes (no relabel required).
# Documented divergences (non-finite float drop, short-provider-token kernel
# coverage) do not appear in this sample.

_CLEAN_GOLDEN_SAMPLE: dict[str, object] = {
    "host": "example.com",
    "depth": 3,
    "okay": True,
    "ratio": 0.5,
    "flag": None,
    "ceiling": False,
    "note": "Bearer AKIAEXAMPLE12345678 hi",  # secret redacted in both
    "nested": {"x": 1},  # dropped in both
    "items": [1, 2, 3],  # dropped in both
    "token": "x",  # key dropped in both
    "rawPrompt": "anything",  # key dropped in both
    "tlsHost": "a.b",
}

_CLEAN_GOLDEN_EXPECTED: dict[str, object] = {
    "host": "example.com",
    "depth": 3,
    "okay": True,
    "ratio": 0.5,
    "flag": None,
    "ceiling": False,
    "note": "[redacted] hi",
    "tlsHost": "a.b",
}


def test_policy_safe_metadata_is_re_export_of_public_diagnostic_metadata() -> None:
    """The web policy entry is now a one-line re-export. Both produce the
    same output for the clean golden sample."""
    via_policy = policy_safe_metadata(_CLEAN_GOLDEN_SAMPLE)
    via_kernel = public_diagnostic_metadata(_CLEAN_GOLDEN_SAMPLE)
    assert via_policy == via_kernel == _CLEAN_GOLDEN_EXPECTED


def test_policy_safe_metadata_returns_empty_for_non_mapping() -> None:
    """Matches the legacy ``isinstance(metadata, dict)`` guard semantics."""
    assert policy_safe_metadata("not-a-dict") == {}
    assert policy_safe_metadata(None) == {}
    assert policy_safe_metadata(42) == {}


# -- Defense-in-depth line-drop pass (verifier-caught regression fix) ------
#
# The legacy ``redact_public_text`` ran a ``_RAW_PRIVATE_LINE_RE`` line-drop
# pass BEFORE its regex-sub redactor. That pass dropped any line containing
# ``raw_*`` / ``hidden_reasoning`` / ``chain_of_thought`` / etc. markers
# wholesale. The C-1 kernel ``UNSAFE_TEXT_RE`` only matches the marker
# substring (e.g. ``raw_tool``) and leaves the line tail intact — so without
# a kernel-side line-drop pass the post-C-2 lenient path leaked everything
# after the marker on the same line. This is the "strictly more redactive"
# invariant violation the verifier flagged.
#
# These tests pin the line-drop semantic on ``public_diagnostic_metadata``
# (and therefore on the ``policy.safe_metadata`` re-export shim).


def test_public_diagnostic_metadata_drops_raw_tool_output_line() -> None:
    """The verifier's exact bad input case. Pre-fix this leaked
    ``_output: hidden``; post-fix the whole marker-bearing line must be
    dropped (byte-equivalent to the legacy ``redact_public_text`` behavior)."""
    bad_input = {"summary": "first line\nraw_tool_output: hidden\nthird line"}
    out = public_diagnostic_metadata(bad_input)
    # Strong: no fragment of the marker-bearing line survives.
    assert "_output: hidden" not in out["summary"]
    assert "raw_tool" not in out["summary"]
    assert "hidden" not in out["summary"]
    # Byte-equivalence with the legacy redact_public_text line-drop semantic.
    assert out == {"summary": "first line\nthird line"}


def test_public_diagnostic_metadata_drops_hidden_reasoning_line() -> None:
    """Other defense-in-depth marker shape: hidden_reasoning. Whole line goes."""
    bad_input = {
        "summary": "prefix\nhidden_reasoning: secret thoughts\nsuffix"
    }
    out = public_diagnostic_metadata(bad_input)
    assert "secret thoughts" not in out["summary"]
    assert "hidden_reasoning" not in out["summary"]
    assert out == {"summary": "prefix\nsuffix"}


def test_public_diagnostic_metadata_drops_chain_of_thought_line() -> None:
    """Other defense-in-depth marker shape: chain_of_thought. Whole line goes."""
    bad_input = {
        "summary": "prefix\nchain_of_thought: step 1 step 2\nsuffix"
    }
    out = public_diagnostic_metadata(bad_input)
    assert "step 1 step 2" not in out["summary"]
    assert "chain_of_thought" not in out["summary"]
    assert out == {"summary": "prefix\nsuffix"}


def test_public_diagnostic_metadata_drops_value_when_only_marker_lines() -> None:
    """When every line carries a marker, the surviving string is empty.
    Matches legacy ``redact_public_text`` behavior (returns empty string)."""
    bad_input = {"summary": "raw_tool_output: x\nhidden_reasoning: y"}
    out = public_diagnostic_metadata(bad_input)
    assert out == {"summary": ""}
