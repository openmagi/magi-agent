"""Public-link redaction for the run-share path.

The canonical kernel scrub (:func:`magi_agent.ops.safety.redact_private_text`,
backed by ``UNSAFE_TEXT_RE``) is comprehensive and LINEAR: it covers provider
token shapes (GitHub / OpenAI / Stripe / AWS / Google / Slack / Telegram), JWTs,
PEM private keys, storage URIs, and private filesystem paths. We build on it
rather than re-approximating a denylist.

It leaves three gaps open on a PUBLIC surface, which this module closes (each
pattern LINEAR by construction, no nested-quantifier backtracking):

  - quoted credential values (``password="..."`` / ``{"secret": "..."}``) whose
    value has no token shape: the kernel redacts the key but not the quoted value;
  - ``scheme://user:pass@host`` URL credentials (basic-auth userinfo);
  - public-only PII: internal cluster hostnames, RFC1918 IPs, emails. These are
    deliberately NOT in the shared kernel (too false-positive prone for its many
    callers); they apply only on the public share surface.

``build_public_run_view`` is the allowlist fail-closed projection: only known
keys survive and every free-text value is scrubbed, so the per-run view from
``run_view.build_run_view`` becomes safe to render on a public link.

Known residuals (defense-in-depth, NOT fully closed here): a bare high-entropy
token with no key prefix and no provider brand, an opaque value containing ``/``
under a non-credential key, and private IPv6 (ULA) addresses can still pass.
These are inherent to a denylist over free text. The public-link UX should pair
this scrub with an explicit "review before making public" confirmation rather
than treat it as a sole guarantee.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from magi_agent.ops.safety import MAX_PUBLIC_TEXT_CHARS, redact_private_text

__all__ = [
    "redact_public_text",
    "build_public_run_view",
]

_REDACTED = "[redacted]"
# Hard cap on how much of a single value we scan/scrub (latency backstop).
_MAX_SCAN_CHARS = 16_384

# Credential-ish key names (no ``[A-Za-z0-9_-]*`` wrappers -> linear, no
# quadratic rescanning). A leading ``(?<![A-Za-z0-9])`` boundary stops short
# tokens like ``pat`` matching inside ``compat``. Matched only before ``[:=]``.
_CRED_BOUNDARY = r"(?<![A-Za-z0-9])"
_CRED_KEY = (
    r"(?:pass(?:word|phrase|wd)?|pwd"
    r"|secret[_-]?key|secret"
    r"|service[_-]?role[_-]?key"
    r"|api[_-]?key|x-api-key|auth[_-]?key|access[_-]?key"
    r"|access[_-]?token|client[_-]?secret|private[_-]?key"
    r"|credentials?|token|pat)"
)
_CRED_PREFIX = rf"({_CRED_BOUNDARY}(?i:{_CRED_KEY})[\"']?\s*[:=]\s*)"
# Quoted value, JSON style supported. The value body tolerates the OPPOSITE
# quote and backslash escapes (``password="a\"b"`` / ``secret="he said 'x'"``)
# and is length-BOUNDED so it stays linear. ``\2`` ties the close to the opener.
_QUOTED_CRED_RE = re.compile(
    rf"{_CRED_PREFIX}([\"'])(?:\\.|(?!\2).){{0,2048}}\2"
)
# Unquoted credential value: the legacy ledger redactor caught these via wildcard
# key names; the kernel's fixed list misses service_role_key/passphrase/etc.
_UNQUOTED_CRED_RE = re.compile(rf"{_CRED_PREFIX}([^\s,;}}\"']{{1,2048}})")
# Generic opaque-token assignment: ``anyKey = <24+ token chars>`` where the value
# has NO ``/`` or ``.`` (so URLs and filesystem paths are NOT eaten). Catches
# Azure ``AccountKey=``, ``auth=<hex>``, vendor keys the brand list misses. The
# value charset excludes ``=`` in the INTERIOR (only trailing base64 padding) so
# ``A=A=A=...`` cannot drive quadratic backtracking. Common non-secret id keys
# are excluded to avoid eating commit SHAs / request ids / digests.
_OPAQUE_SAFE_KEYS = (
    r"(?:commit|sha|hash|digest|checksum|request[_-]?id|trace[_-]?id|run[_-]?id"
    r"|build|etag|version|revision|ref|id)"
)
_OPAQUE_TOKEN_ASSIGN_RE = re.compile(
    rf"((?<![A-Za-z0-9])(?!{_OPAQUE_SAFE_KEYS}\s*[:=])"
    r"[A-Za-z0-9_-]{1,64}\s*[:=]\s*[\"']?)"
    r"([A-Za-z0-9+_-]{24,1024}={0,2})"
    r"(?=[\"']?(?:[\s,;}]|$))",
    re.IGNORECASE,
)
# scheme://user:pass@  ->  strip the userinfo. Scheme + userinfo lengths are
# BOUNDED so an unterminated alnum run cannot backtrack quadratically. A single
# leading slash is tolerated (the kernel's private-path rule can eat one slash
# of ``://`` before this runs).
_URL_USERINFO_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]{0,31}:/{1,2})[^/\s:@]{1,256}:[^/\s@]{1,256}@"
)
# internal kubernetes service hostnames. Label lengths are BOUNDED ({1,63}) and
# the label repeat is bounded ({1,10}) so a long non-matching alnum run cannot
# drive quadratic backtracking (the legacy ledger redactor's failure mode).
_CLUSTER_HOST_RE = re.compile(
    r"(?:[A-Za-z0-9-]{1,63}\.){1,10}svc\.cluster\.local\b", re.IGNORECASE
)
# RFC1918 private ranges only (public IPs must survive so the trace stays useful).
_RFC1918_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
# Bounded local/domain lengths (RFC limits) keep this linear on long inputs.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b"
)


def redact_public_text(
    value: str, *, max_chars: int | None = MAX_PUBLIC_TEXT_CHARS
) -> str:
    """Scrub a free-text string for a PUBLIC share surface. Fail-open, linear.

    Quoted-credential scrubbing runs BEFORE the kernel so the key name is still
    present (the kernel would otherwise consume the key and strand the quoted
    value). ``max_chars`` clips the result (default
    :data:`~magi_agent.ops.safety.MAX_PUBLIC_TEXT_CHARS`); pass ``None`` to skip.
    """
    if not isinstance(value, str) or not value:
        return ""
    # Hard scan cap: bound ALL regex work regardless of the (post-scrub)
    # ``max_chars`` clip, so an oversized attacker-controlled value cannot blow
    # the latency budget. No legitimate secret-bearing field needs more.
    if len(value) > _MAX_SCAN_CHARS:
        value = value[:_MAX_SCAN_CHARS]
    # Credential assignments and URL userinfo run BEFORE the kernel so the key
    # name / ``://`` are still intact (the kernel would otherwise consume the key
    # or eat a slash and strand the secret).
    scrubbed = _QUOTED_CRED_RE.sub(rf"\1\2{_REDACTED}\2", value)
    scrubbed = _UNQUOTED_CRED_RE.sub(rf"\1{_REDACTED}", scrubbed)
    scrubbed = _OPAQUE_TOKEN_ASSIGN_RE.sub(rf"\1{_REDACTED}", scrubbed)
    scrubbed = _URL_USERINFO_RE.sub(rf"\1{_REDACTED}@", scrubbed)
    scrubbed = redact_private_text(scrubbed, max_chars=None)
    scrubbed = _CLUSTER_HOST_RE.sub(_REDACTED, scrubbed)
    scrubbed = _RFC1918_RE.sub(_REDACTED, scrubbed)
    scrubbed = _EMAIL_RE.sub(_REDACTED, scrubbed)
    if max_chars is not None and len(scrubbed) > max_chars:
        scrubbed = scrubbed[:max_chars]
    return scrubbed


def _redact_nested(value: object) -> object:
    """Recursively scrub free text inside a small summary structure.

    Strings are redacted; numbers/bools pass through; mappings/sequences recurse.
    Mapping keys are coerced to str (they are field names, not secrets).
    """
    if isinstance(value, str):
        return redact_public_text(value, max_chars=None)
    if isinstance(value, Mapping):
        # Scrub KEYS too: a tool can surface an env/header dump where the secret
        # IS the key (e.g. ``{"ghp_...": "1"}``).
        return {
            redact_public_text(str(k), max_chars=None): _redact_nested(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_nested(v) for v in value]
    return value


_SUMMARY_KEYS = ("goal", "result", "status", "model", "usage", "costUsd")
_STEP_KEYS = (
    "turnId",
    "toolCallId",
    "activityType",
    "name",
    "status",
    "reason",
    "durationMs",
    "actor",
    "spawnDepth",
    "argsSummary",
    "resultSummary",
)
_STEP_FREE_TEXT = frozenset({"argsSummary", "resultSummary", "name", "reason"})
_GOV_KEYS = ("turnId", "name", "status", "reason", "kind")
_GOV_FREE_TEXT = frozenset({"name", "reason"})
_COUNT_KEYS = ("stepCount", "turnCount", "receiptCount", "governanceCount")


def _scrub_opt(value: object) -> object:
    """Scrub a string value, pass through non-strings (incl. None) unchanged."""
    return redact_public_text(value, max_chars=None) if isinstance(value, str) else value


def _public_summary(summary: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key in _SUMMARY_KEYS:
        if key not in summary:
            continue
        value = summary[key]
        if key in ("goal", "result"):
            out[key] = redact_public_text(str(value), max_chars=None)
        elif key == "model" and isinstance(value, Mapping):
            # Structured today (config model ids), but scrub defensively so a
            # future free-text label cannot bypass redaction.
            out[key] = {
                "label": _scrub_opt(value.get("label")),
                "provider": _scrub_opt(value.get("provider")),
            }
        elif key == "usage" and isinstance(value, Mapping):
            out[key] = {
                "inputTokens": value.get("inputTokens"),
                "outputTokens": value.get("outputTokens"),
            }
        else:
            out[key] = value
    return out


def _public_step(step: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key in _STEP_KEYS:
        if key not in step:
            continue
        out[key] = _redact_nested(step[key]) if key in _STEP_FREE_TEXT else step[key]
    return out


def _public_gov(entry: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key in _GOV_KEYS:
        if key not in entry:
            continue
        value = entry[key]
        out[key] = redact_public_text(str(value), max_chars=None) if key in _GOV_FREE_TEXT else value
    return out


def build_public_run_view(view: Mapping[str, object]) -> dict:
    """Allowlist fail-closed projection of a run view for a PUBLIC link.

    Only known keys survive; every free-text value is scrubbed via
    :func:`redact_public_text`. Numeric/enum fields (status, counts, tokens,
    cost, durations, ids) pass through. Unknown keys are dropped.
    """
    summary = view.get("summary")
    trace = view.get("trace")
    governance = view.get("governance")
    counts = view.get("counts")

    out_counts: dict[str, object] = {}
    if isinstance(counts, Mapping):
        out_counts = {k: counts[k] for k in _COUNT_KEYS if k in counts}

    return {
        "schemaVersion": view.get("schemaVersion"),
        "sessionId": view.get("sessionId"),
        "summary": _public_summary(summary) if isinstance(summary, Mapping) else None,
        "trace": [
            _public_step(s)
            for s in (trace if isinstance(trace, Sequence) else [])
            if isinstance(s, Mapping)
        ],
        "governance": [
            _public_gov(g)
            for g in (governance if isinstance(governance, Sequence) else [])
            if isinstance(g, Mapping)
        ],
        "counts": out_counts,
    }
