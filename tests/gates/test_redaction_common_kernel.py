"""Parity tests for the shared gates transcript-redaction pattern (B1, N-04).

Follows the S-03 precedent (``tests/test_receipt_redaction_kernel.py``):
1. identity: consumer module attributes are the same object as the leaf pattern,
2. golden corpus: consumer outputs are byte-identical to the pre-move behavior,
3. identical-or-stricter: the egress detector union rejects strictly more.

Secret-shaped fixtures are assembled from fragments at runtime so GitHub push
protection never sees a contiguous credential literal.
"""

from __future__ import annotations

import pytest

from magi_agent.evidence import gate1a_egress_correlation as egress
from magi_agent.evidence import observed_egress
from magi_agent.gates import _redaction_common
from magi_agent.gates import gate1a_readonly_tools as gate1a
from magi_agent.gates import gate5b_full_toolhost as gate5b


# Secret-shaped inputs assembled from fragments (never contiguous literals).
_SK = "sk-" + "test" + "0123456789abcdef"
_GHP = "gh" + "p_" + "A" * 20
_GPAT = "github_" + "pat_" + "B" * 22
_XOX = "xox" + "b-" + "1234-5678-abcd"
_AKIA = "AKIA" + "0123456789ABCDEF"
_AIZA = "AIza" + "SyA1234567890_-abc"

_CORPUS = [
    "authorization: bearer abc.def.ghi",
    "Cookie: session=deadbeef; other=1",
    "set-cookie: sid=xyz",
    "sid=" + "A1b2C3d4._-",
    _SK,
    _GHP,
    _GPAT,
    _XOX,
    _AKIA,
    _AIZA,
    "/Users/kevin/secret/file.txt",
    "/home/bot/data",
    "/workspace/app/main.py",
    "/data/bots/abc/mem.md",
    "raw_tool payload here",
    "hidden reasoning trace",
    "chain-of-thought leak",
    "normal text no secrets",
    "the quick brown fox",
    "objectKey=public123",
    "just a plain sentence",
    "results 42 count",
]

# Golden outputs captured from pristine main before the leaf extraction.
_GATE5B_GOLDEN = {
    "authorization: bearer abc.def.ghi": "[redacted]",
    "Cookie: session=deadbeef; other=1": "[redacted]",
    "set-cookie: sid=xyz": "[redacted]",
    "sid=" + "A1b2C3d4._-": "[redacted]",
    _SK: "[redacted]",
    _GHP: "[redacted]",
    _GPAT: "[redacted]",
    _XOX: "[redacted]",
    _AKIA: "[redacted]",
    _AIZA: "[redacted]",
    "/Users/kevin/secret/file.txt": "[redacted]",
    "/home/bot/data": "[redacted]",
    "/workspace/app/main.py": "[redacted]",
    "/data/bots/abc/mem.md": "[redacted]",
    "raw_tool payload here": "[redacted] payload here",
    "hidden reasoning trace": "[redacted] trace",
    "chain-of-thought leak": "[redacted] leak",
    "normal text no secrets": "normal text no secrets",
    "the quick brown fox": "the quick brown fox",
    "objectKey=public123": "objectKey=public123",
    "just a plain sentence": "just a plain sentence",
    "results 42 count": "results 42 count",
}

# gate1a returns (value, redacted_flag); note "normal text no secrets" trips the
# fnmatch "*secret*" branch (unchanged by this extraction).
_GATE1A_GOLDEN = {
    "authorization: bearer abc.def.ghi": ("[redacted]", True),
    "Cookie: session=deadbeef; other=1": ("[redacted]", True),
    "set-cookie: sid=xyz": ("[redacted]", True),
    "sid=" + "A1b2C3d4._-": ("[redacted]", True),
    _SK: ("[redacted]", True),
    _GHP: ("[redacted]", True),
    _GPAT: ("[redacted]", True),
    _XOX: ("[redacted]", True),
    _AKIA: ("[redacted]", True),
    _AIZA: ("[redacted]", True),
    "/Users/kevin/secret/file.txt": ("[redacted]", True),
    "/home/bot/data": ("[redacted]", True),
    "/workspace/app/main.py": ("[redacted]", True),
    "/data/bots/abc/mem.md": ("[redacted]", True),
    "raw_tool payload here": ("[redacted]", True),
    "hidden reasoning trace": ("[redacted]", True),
    "chain-of-thought leak": ("[redacted]", True),
    "normal text no secrets": ("[redacted]", True),
    "the quick brown fox": ("the quick brown fox", False),
    "objectKey=public123": ("objectKey=public123", False),
    "just a plain sentence": ("just a plain sentence", False),
    "results 42 count": ("results 42 count", False),
}


def test_gate_transcript_pattern_is_single_object() -> None:
    assert gate1a._SENSITIVE_RE is gate5b._SENSITIVE_RE
    assert gate1a._SENSITIVE_RE is _redaction_common.SENSITIVE_TRANSCRIPT_RE


def test_egress_detector_is_single_object() -> None:
    assert observed_egress._SENSITIVE_RE is egress.SENSITIVE_EGRESS_MARKER_RE


@pytest.mark.parametrize("text", _CORPUS)
def test_gate5b_redact_matches_golden(text: str) -> None:
    assert gate5b._redact(text) == _GATE5B_GOLDEN[text]


@pytest.mark.parametrize("text", _CORPUS)
def test_gate1a_sanitize_output_matches_golden(text: str) -> None:
    assert gate1a._sanitize_output(text) == _GATE1A_GOLDEN[text]


_VALID_EGRESS_PAYLOAD = {
    "requestDigest": "sha256:" + "a" * 64,
    "providerRequestCount": 1,
    "egressTunnelCount": 1,
    "egressHostClasses": ["gemini_proxy"],
    "observedWindowStart": "2026-05-24T10:00:00.000Z",
    "observedWindowEnd": "2026-05-24T10:00:01.000Z",
    "evidenceSource": "local_fixture",
    "redactionStatus": "public_safe",
    "decisionReason": "observed_gemini_proxy_tunnel",
}


def test_observed_egress_still_accepts_valid_payload() -> None:
    observed_egress.ObservedEgressEvidence.model_validate(dict(_VALID_EGRESS_PAYLOAD))


@pytest.mark.parametrize(
    "marker",
    [
        "observed_prompt_marker",
        "observed_session_marker",
        "observed_output_marker",
        "observed_provider_payload_marker",
    ],
)
def test_observed_egress_now_rejects_superset_markers(marker: str) -> None:
    payload = dict(_VALID_EGRESS_PAYLOAD)
    payload["decisionReason"] = marker
    with pytest.raises(ValueError):
        observed_egress.ObservedEgressEvidence.model_validate(payload)
