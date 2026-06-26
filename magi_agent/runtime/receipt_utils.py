from __future__ import annotations

import re

from magi_agent.runtime.receipt_redaction import (
    _SAFE_ID_RE,
    _SAFE_REDACTION_TOKEN_RE,
    _SECRET_TEXT_RE,
    _PRIVATE_PATH_RE,
    _RAW_PRIVATE_LINE_RE,
    _UNSAFE_REF_MARKER_RE,
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_text,
    sha256_ref,
    strict_sha256_ref,
    string_tuple,
)
from magi_agent.runtime import receipt_redaction as _kernel


# Runtime-activity ref namespace. The secret/path scrubbing lives in the shared
# kernel; only this allowlist of pass-through ref prefixes is domain-specific.
_SAFE_REF_RE = re.compile(
    r"^(?:activity|activity-receipt|approval|artifact|evidence|idempotency|"
    r"policy|policy-snapshot|receipt|ref|run|scope|sha256|task|turn):"
    r"[A-Za-z0-9_.:/=-]{1,191}$"
)

# Re-export the shared scrubbing primitives so existing
# ``receipt_utils._SECRET_TEXT_RE`` / ``sanitize_public_text`` references keep
# resolving to the single kernel authority (asserted by the site-parity test).
__all__ = [
    "canonical_digest",
    "has_unsafe_marker",
    "sanitize_public_ref",
    "sanitize_public_text",
    "sanitize_reason_code",
    "sha256_ref",
    "strict_sha256_ref",
    "string_tuple",
]

# Silence "imported but unused" for the re-exported regex names; they exist to
# keep the public surface and the kernel-identity invariant intact.
_REEXPORTED = (
    _SAFE_ID_RE,
    _SAFE_REDACTION_TOKEN_RE,
    _SECRET_TEXT_RE,
    _PRIVATE_PATH_RE,
    _RAW_PRIVATE_LINE_RE,
    _UNSAFE_REF_MARKER_RE,
)


def sanitize_public_ref(value: str) -> str:
    return _kernel.sanitize_public_ref(value, safe_ref_re=_SAFE_REF_RE)


def sanitize_reason_code(value: str) -> str:
    return _kernel.sanitize_reason_code(value, default="runtime_receipt_reason")
