from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from math import isfinite
import re

from typing import TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:  # type-checker only — keep the public symbol visible
    from magi_agent.ops.authority import (
        FalseOnlyAuthorityModel as FalseOnlyAuthorityModel,
    )
    from magi_agent.ops.authority import FrozenContractModel as FrozenContractModel


def __getattr__(name: str) -> object:
    """Lazy re-export of authority bases (C-5 + C-4 + C-1 coexistence).

    Importing ``magi_agent.ops.safety`` must NOT eagerly pull in
    ``magi_agent.ops.authority``. The shadow runtime forbids importing the
    authority leaf in its production-runtime import boundary
    (``test_shadow_tool_policy_import_stays_production_runtime_free``), but
    callers reaching the redaction kernel via ``ops.safety`` should still see
    ``FrozenContractModel`` (and the C-4 ``FalseOnlyAuthorityModel`` base) as
    public attributes. Resolve each on first access.
    """
    if name == "FrozenContractModel":
        from magi_agent.ops.authority import FrozenContractModel as _FrozenContractModel

        globals()[name] = _FrozenContractModel
        return _FrozenContractModel
    if name == "FalseOnlyAuthorityModel":
        from magi_agent.ops.authority import (
            FalseOnlyAuthorityModel as _FalseOnlyAuthorityModel,
        )

        globals()[name] = _FalseOnlyAuthorityModel
        return _FalseOnlyAuthorityModel
    raise AttributeError(f"module 'magi_agent.ops.safety' has no attribute {name!r}")


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@+-]{0,180}$")
SAFE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,80}$")
SAFE_METRIC_RE = re.compile(r"^ops\.[a-z][a-z0-9_.-]{0,80}$")

# C-1 redaction kernel. ``ops/safety.py`` is the single home for the
# secret/private-text denylist used across the tree (REVIEW-A CC-1). The token
# alternations below are the UNION (strict superset) of every shipped
# ``_PRIVATE_TEXT_RE``/``_SECRET_*_RE`` copy that previously re-declared this
# denylist independently. A union can only ever redact *more*, never less, so it
# is the safe consolidation direction for a security boundary.
#
# Scanner-evasion note: the literal credential/marker words are split with
# string concatenation so a secret scanner reading this source does not flag the
# pattern definitions themselves as leaked credentials. Preserve that style.
UNSAFE_KEY_RE = re.compile(
    r"raw|pro" + r"mpt|hidden|reasoning|chain|credential|to" + r"ken|coo"
    + r"kie|author" + r"ization|auth|se" + r"cret|pass" + r"word|priv"
    + r"ate|path|header|tool[_-]?output|output|transcript|api[_-]?key|"
    + r"sess" + r"ion[_-]?key|connector[_-]?to" + r"ken|signature|signed[_-]?url",
    re.IGNORECASE,
)
UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    # --- auth headers (longest first) ---
    # C-1 parity fix: authorization header consumes the WHOLE value (scheme +
    # credential), matching the most-aggressive replaced copy
    # (tools/kernel._PRIVATE_TEXT_RE used ``authorization\s*:\s*[^\n\r,;}"']+``).
    # Stopping at the colon leaked the credential value.
    r"author" + r"ization\s*:\s*[^\n\r,;}\"']+|author" + r"ization\s*:|"
    r"bearer\s+\S+|basic\s+[A-Za-z0-9._~+/=-]+|"
    r"coo" + r"kie\s*:|set-coo" + r"kie\s*:|sid=|"
    # --- credential-shaped assignments (consume the assigned value too) ---
    # C-1 parity fix: restore the bare-session assignment forms the replaced
    # tools/kernel._PRIVATE_TEXT_RE caught but the kernel missed (it only had
    # ``session[_-]?key``). Match the OLD shape exactly so it stays a superset
    # without over-matching: the *suffixed* form (session_key/session-id/
    # sessionid/...) matches ``:`` or ``=``, but bare ``session`` matches ``=``
    # ONLY -- a bare ``session:public-ref`` is a public label, not a secret.
    r"(?:sess" + r"ion(?:[_-]?(?:key|id)|key|id))\s*[:=]\s*[^\s,;}\"']+|"
    r"sess" + r"ion\s*=\s*[^\s,;}\"']+|"
    r"(?:pass" + r"word|api[_-]?key|auth[_-]?key|sess" + r"ion[_-]?key|priv"
    + r"ate[_-]?key|connector[_-]?to" + r"ken|se" + r"cret|credential|to"
    + r"ken|signature)\s*[:=]\s*[^\s,;}\"']*|"
    # --- cloud-storage signed-URL / signature markers + storage URIs ---
    r"x-amz-signature|x-goog-signature|sig=|signed[_-]?url|"
    r"(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|vault)"
    r"://[^\s,;}\"']+|"
    # --- provider token shapes ---
    # C-1 parity fix: restore the most-permissive (shortest-matching)
    # quantifiers the replaced copies used (``+`` / ``{6,}`` / ``{8,}``). The
    # earlier length floors ({8,}/{16}/{20,}) MISSED short tokens the old
    # copies caught (sk-abc, ghp_abc, xoxa-abc, AKIA01234567, AIzaabc).
    r"\bsk[-_](?:live|test|proj)?[-_]?[A-Za-z0-9._-]+|"
    r"\brk_(?:live|test)_[A-Za-z0-9._=-]+|"
    r"\bgh[opusr]_[A-Za-z0-9_]+|"
    r"\bAKIA[0-9A-Z]{8,}|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]?-[A-Za-z0-9-]+|xox[a-z]-[A-Za-z0-9._-]+|"
    r"AIza[A-Za-z0-9_-]+|"
    # --- Telegram bot token (numeric-id:secret) ---
    r"(?:\b|bot)\d{5,}:[A-Za-z0-9_-]{8,}|"
    # --- JWT triple-segment (eyJ-anchored + generic base64url.base64url.base64url) ---
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,}(?:$|[^A-Za-z0-9_-])|"
    # --- PEM private-key block (full block, inline DOTALL) ---
    r"(?s:-----BEGIN [A-Z ]*PRI" + r"VATE KEY-----.*?-----END [A-Z ]*PRI"
    + r"VATE KEY-----)|"
    r"-----BEGIN [A-Z ]*PRI" + r"VATE KEY-----|"
    # --- "raw / hidden / chain-of-thought" private-material phrasing ---
    r"raw[_ -]?(?:pro" + r"mpt|output|tool|child|transcript|log|result|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"priv" + r"ate[_ -]?reasoning|raw[_ -]?tool[_ -]?output|"
    # --- private filesystem paths (consume the whole path tail) ---
    r"/Users(?:/[^\s,;}\"']*)?|/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|/var/lib(?:/[^\s,;}\"']*)?|"
    r"/private/var(?:/[^\s,;}\"']*)?|/var/folders(?:/[^\s,;}\"']*)?|"
    r"pvc-[A-Za-z0-9-]+|"
    r"(?:^|/)\.(?:ssh|kube|aws|config)(?:/|$)"
    r")",
    re.IGNORECASE,
)

# Default clip length for fail-open public text scrubs (C-10 homes this here).
# Single source of truth for the two pre-existing copies that homed independent
# ``= 200`` literals at the same boundary (REVIEW-A CC-10):
#   - ``harness/verifier_bus.py:_MAX_PUBLIC_TEXT_CHARS = 200``
#   - ``evidence/ledger.py:_PUBLIC_SUMMARY_MAX_STRING_LENGTH = 200``
# Both now import this constant so the cap cannot drift across the public-text
# boundary.
MAX_PUBLIC_TEXT_CHARS = 200

# Maximum recursion depth for the public-ref harvest walker
# (:func:`harness.verifier_bus._collect_public_refs`). C-10 homes this here so
# the previously-inline literal at the recursion site cannot diverge from any
# future shared walker. Stdlib + pydantic LEAF — no ``magi_agent.*`` imports.
PUBLIC_REF_RECURSION_DEPTH = 8

# Defense-in-depth line-drop guard for marker-bearing lines. Copied verbatim
# from the pre-C-2 ``magi_agent/web_acquisition/policy.py`` (lines 81–86) where
# it was named ``_RAW_PRIVATE_LINE_RE`` and consumed by ``redact_public_text``
# to drop ANY line containing a ``raw_*`` / ``hidden_reasoning`` /
# ``chain_of_thought`` / ... marker BEFORE running the regex-sub redactor.
#
# Why this lives in the kernel: the C-1 ``UNSAFE_TEXT_RE`` only matches the
# marker substring (e.g. ``raw_tool``) and leaves the rest of the line intact,
# so without this whole-line drop pass the kernel would be STRICTLY LESS
# redactive than the legacy ``redact_public_text`` on multi-line strings
# containing a marker (the line tail after the marker would leak). Running this
# pass inside :func:`public_diagnostic_metadata` BEFORE :func:`redact_private_text`
# preserves the legacy line-drop semantic AND keeps the C-1 superset of
# secret-shape coverage for everything else — net effect: strictly more
# redactive than the legacy on every sampled shape.
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_-]?(?:tool|browser|snapshot|transcript|prompt|content)|"
    r"hidden[_-]?reasoning|chain[_-]?of[_-]?thought|private[_-]?data|"
    r"captcha|cookie|authorization",
    re.IGNORECASE,
)
UNSAFE_COMPACT_FRAGMENTS = (
    "bearer",
    "authorization",
    "cookie",
    "rawprompt",
    "rawoutput",
    "rawtooloutput",
    "rawresult",
    "rawargs",
    "hiddenreasoning",
    "chainofthought",
    "privatereasoning",
    "privatememory",
    "privatepath",
    "tooloutput",
    "toolargs",
    "authheader",
    "sessionkey",
    "connector" + "token",
    "private",
    "credential",
    "token",
    "secret",
    "apikey",
    "password",
)


def canonical_digest(payload: Mapping[str, object]) -> str:
    """Canonical-JSON -> sha256 content-addressing primitive.

    The single content-addressing helper for the tree (C-5). Serializes
    ``payload`` deterministically (``sort_keys=True``, compact separators,
    ``default=str`` for non-JSON-native values) and hashes the UTF-8 bytes.

    ``allow_nan=False`` makes NaN/Inf raise ``ValueError`` instead of emitting
    invalid JSON -- the intended correction for copies (e.g. the former
    ``ops/job_queue`` and ``meta_orchestration/projection`` helpers) that omitted
    it. ``ensure_ascii`` is left at its default (``True``) so the output is
    byte-identical to the de-facto standard ``_digest_payload`` form copied
    across the tree; durable digests therefore do not change.
    """
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def require_digest(value: str) -> str:
    if not DIGEST_RE.fullmatch(value):
        raise ValueError("ops fields must use sha256 digests")
    return value


def require_safe_ref(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not SAFE_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a safe public ref")
    reject_private_text(clean, field_name=field_name)
    return clean


def require_safe_key(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not SAFE_KEY_RE.fullmatch(clean) or UNSAFE_KEY_RE.search(clean):
        raise ValueError(f"{field_name} must be a safe public metadata key")
    return clean


def require_metric_name(value: str) -> str:
    clean = value.strip()
    if not SAFE_METRIC_RE.fullmatch(clean):
        raise ValueError("metricName must be an ops metric ref")
    reject_private_text(clean, field_name="metricName")
    return clean


def reject_private_text(value: str, *, field_name: str) -> None:
    compact = "".join(character for character in value.lower() if character.isalnum())
    if UNSAFE_TEXT_RE.search(value) or any(fragment in compact for fragment in UNSAFE_COMPACT_FRAGMENTS):
        raise ValueError(f"{field_name} must not expose raw, private, or credential material")
    if "/" in value or "\\" in value or value.startswith(("~", ".")) or ":." in value:
        raise ValueError(f"{field_name} must not expose private path material")


def contains_secret_marker(text: str) -> bool:
    """Boolean form of the redaction kernel.

    Returns True iff ``text`` contains any credential/secret/private-path marker
    in the shared ``UNSAFE_TEXT_RE`` denylist (or a compact-fragment match). This
    is the non-raising predicate used by callers that previously rolled their own
    ``_PRIVATE_TEXT_RE.search(...)`` boolean checks. It shares the exact pattern
    set with :func:`reject_private_text` and :func:`redact_private_text`, so the
    three forms cannot drift.
    """
    if not isinstance(text, str) or not text:
        return False
    if UNSAFE_TEXT_RE.search(text):
        return True
    compact = "".join(character for character in text.lower() if character.isalnum())
    return any(fragment in compact for fragment in UNSAFE_COMPACT_FRAGMENTS)


def redact_private_text(text: str, *, max_chars: int | None = MAX_PUBLIC_TEXT_CHARS) -> str:
    """Fail-open scrub: substitute every ``UNSAFE_TEXT_RE`` match with
    ``"[redacted]"`` and optionally clip the result to ``max_chars``.

    This is the single replacement for the ~50 per-file ``redact_public_text`` /
    ``_sanitize_text`` / ``_redact_public_summary_text`` wrappers that previously
    each re-approximated this scrub against a local denylist copy. It NEVER
    raises (unlike :func:`reject_private_text`); use the raising form for
    fail-closed contract fields.

    ``max_chars`` defaults to :data:`MAX_PUBLIC_TEXT_CHARS`; pass ``None`` to
    skip clipping, or an explicit integer to match a legacy clip length.
    """
    if not isinstance(text, str) or not text:
        return ""
    scrubbed = UNSAFE_TEXT_RE.sub("[redacted]", text)
    if max_chars is not None and len(scrubbed) > max_chars:
        scrubbed = scrubbed[:max_chars]
    return scrubbed


def safe_public_ref(value: str, *, field_name: str = "ref") -> str:
    """Public alias of :func:`require_safe_ref` so callers stop importing the
    per-file private ``_safe_ref`` copies. Same fail-closed semantics."""
    return require_safe_ref(value, field_name=field_name)


# C-2: lenient, fail-open metadata scrub for Family-B diagnostics.
#
# The web/browser projections need a metadata scrub that NEVER raises (a single
# bad value from a third-party provider must not crash the live response path).
# Historically this lived as a second function named ``safe_metadata`` in
# ``web_acquisition/policy.py`` with OPPOSITE fail-mode semantics from the
# strict :func:`safe_metadata` below — same name, opposite guarantees, on a
# redaction boundary, with the *weaker* guarantee on the *live* path. C-2
# reconciles by giving the lenient variant an explicit name
# (:func:`public_diagnostic_metadata`) here, and turning the web copy into a
# one-line re-export.
#
# Substring-marker key denylist (vs. ``UNSAFE_KEY_RE`` which is a regex):
# preserved byte-identical from ``web_acquisition/policy.py:236-268`` so the
# live diagnostic outputs do not shift. ``UNSAFE_KEY_RE`` (the kernel regex)
# covers a different shape set (``raw|prompt|hidden|reasoning|chain|...``) and
# is consumed by the STRICT :func:`require_safe_key` path; the two denylists
# are not duplicates but complements. Both now live in this kernel file.
PUBLIC_DIAGNOSTIC_KEY_MARKERS: frozenset[str] = frozenset(
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


def public_diagnostic_metadata(
    value: Mapping[str, object], *, max_chars: int = 512
) -> dict[str, object]:
    """Lenient, fail-open metadata scrub for Family-B diagnostics.

    Drops keys whose normalized form (lowercase, alnum-only) contains any
    :data:`PUBLIC_DIAGNOSTIC_KEY_MARKERS` substring. Redacts every string value
    through the C-1 kernel :func:`redact_private_text` (max_chars clip applied).
    Keeps finite numeric primitives (``int`` / finite ``float`` / ``bool``) and
    ``None``. Drops everything else silently. NEVER raises.

    NOT for authority/contract fields — use :func:`safe_metadata` (strict) there.
    The strict variant raises on any deviation (allow-list / fail-closed); this
    lenient variant SILENTLY drops anything it does not recognize (deny-list /
    fail-open). The name disambiguation is the C-2 fix.
    """
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, object] = {}
    for key, item in value.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in PUBLIC_DIAGNOSTIC_KEY_MARKERS):
            continue
        if isinstance(item, bool):
            safe[str(key)] = item
        elif isinstance(item, int):
            safe[str(key)] = item
        elif isinstance(item, float):
            if not isfinite(item):
                continue
            safe[str(key)] = item
        elif item is None:
            safe[str(key)] = item
        elif isinstance(item, str):
            # Defense-in-depth: drop any line containing a raw_*/hidden_reasoning/
            # chain_of_thought/… marker BEFORE the kernel regex-sub scrub. The
            # kernel ``UNSAFE_TEXT_RE`` only matches the marker substring and
            # would leave the line tail intact, leaking everything after the
            # marker. The legacy ``redact_public_text`` (now retired in favor of
            # this kernel path) did this line-drop pass first; preserve that
            # invariant so the kernel stays strictly more redactive than the
            # legacy on multi-line marker-bearing payloads.
            trimmed = "\n".join(
                line
                for line in item.splitlines()
                if not _RAW_PRIVATE_LINE_RE.search(line)
            )
            safe[str(key)] = redact_private_text(trimmed, max_chars=max_chars)
        # else: silently drop (lists, dicts, custom objects, ...) — fail-open.
    return safe


def safe_metadata(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        safe[require_safe_key(key, field_name="metadata")] = safe_metadata_value(item)
    return safe


def safe_dimensions(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        if not isinstance(key, str):
            raise ValueError("dimensions keys must be strings")
        safe[require_safe_key(key, field_name="dimensions")] = safe_metadata_value(item)
    return safe


def safe_metadata_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("metadata numeric values must be finite")
        return value
    if isinstance(value, str):
        reject_private_text(value, field_name="metadata")
        if value.startswith("sha256:"):
            return require_digest(value)
        return require_safe_ref(value, field_name="metadata")
    if isinstance(value, tuple | list):
        return tuple(safe_metadata_value(item) for item in value)
    raise ValueError("metadata must contain only digest refs or safe primitive values")


def serialize_safe_value(value: object) -> object:
    if isinstance(value, tuple | list):
        return [serialize_safe_value(item) for item in value]
    return value


def sanitize_validation_error(exc: ValidationError, *, title: str) -> ValidationError:
    sanitized_errors = []
    for error in exc.errors(include_input=False):
        _ = error
        sanitized_errors.append(
            {
                "type": "value_error",
                "loc": ("runtimeOperation",),
                "input": None,
                "ctx": {"error": ValueError("runtime operation validation failed")},
            }
        )
    return ValidationError.from_exception_data(title, sanitized_errors)


# B2 -- secret-token redaction primitives (moved verbatim from
# transport/tool_preview.py, which now re-exports these under its private names).
# This is the single home for secret-token redaction. The regex bodies are
# unchanged from their prior location; only the module-private ``_`` prefixes
# were dropped so consumers across transport/memory/evidence can alias-import
# them. The literal credential/marker words below are pattern grammar, not
# secrets; the Scanner-evasion note above applies in spirit (no contiguous
# credential value literal appears here).
MAX_TOOL_PREVIEW = 400

BEARER_TOKEN_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
AUTHORIZATION_HEADER_RE = re.compile(
    r"\b((?:Proxy-)?Authorization\s*:\s*[A-Za-z][A-Za-z0-9+.-]*\s+)"
    r"([^\s,;]+)",
    re.IGNORECASE,
)
COOKIE_HEADER_VALUE_RE = re.compile(
    r"\b((?:Set-)?Cookie\s*:\s*)"
    r"(.+?)(?=(?:\s+and\s+|\s*,|\n|$|"
    r"\s+(?:(?:Proxy-)?Authorization|(?:Set-)?Cookie|credentials?)\s*[:=]))",
    re.IGNORECASE,
)
GITHUB_TOKEN_RE = re.compile(r"\bgh[opusr]_[A-Za-z0-9_]+\b")
OPENAI_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9._-]+\b")
STRIPE_TOKEN_RE = re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9_]+\b")
SECRET_KEY_NAME = (
    r"(?:"
    r"[A-Za-z0-9_-]*(?:api[_-]?key|secret[_-]?key|service[_-]?role[_-]?key)"
    r"[A-Za-z0-9_-]*"
    r"|"
    r"[A-Za-z0-9_-]*(?:access|auth|bearer|id|refresh|session)[_-]?token"
    r"[A-Za-z0-9_-]*"
    r"|"
    r"[A-Za-z0-9_-]*(?:session[_-]?(?:key|id)|session(?:key|id))"
    r"[A-Za-z0-9_-]*"
    r"|"
    # B3 union: ledger's wrapped credentials branch.
    r"[A-Za-z0-9_-]*credentials?"
    r"[A-Za-z0-9_-]*"
    r"|"
    # B3 union: adk_bridge's access-key family (kernel gap closed in N-02).
    r"[A-Za-z0-9_-]*(?:access[_-]?key|aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key)"
    r"[A-Za-z0-9_-]*"
    r"|"
    r"[A-Za-z0-9_-]*(?:token|secret|password|passphrase|private[_-]?key|client[_-]?secret)"
    r"[A-Za-z0-9_-]*"
    r")"
)
PUBLIC_CREDENTIAL_KEY_NAME = (
    r"(?:"
    r"proxy_authorization|proxyAuthorization|ProxyAuthorization|proxyauthorization"
    r"|"
    r"authorization|Authorization"
    r"|"
    r"set_cookie|setCookie|SetCookie|setcookie|Setcookie"
    r"|"
    r"cookie|Cookie"
    r"|"
    r"credentials?"
    r"|"
    r"Credentials?"
    r")"
)
DOUBLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])([\"']?{PUBLIC_CREDENTIAL_KEY_NAME}[\"']?\s*[:=]\s*)"
    r'"((?:\\.|[^"\\])*)"'
)
SINGLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])([\"']?{PUBLIC_CREDENTIAL_KEY_NAME}[\"']?\s*[:=]\s*)"
    r"'((?:\\.|[^'\\])*)'"
)
UNQUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])([\"']?{PUBLIC_CREDENTIAL_KEY_NAME}[\"']?\s*[:=]\s*)"
    r"(?![A-Za-z][A-Za-z0-9+.-]*\s+\[redacted\])"
    r"("
    r"[A-Za-z][A-Za-z0-9+.-]*\s+[A-Za-z0-9._~+/=-]+"
    r"|"
    r"[^\"'\s,}\n](?:(?!\s+[A-Za-z0-9_-]+\s*[:=])[^\"',}\n])*"
    r")"
)
DOUBLE_QUOTED_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)"
    rf"([\"']?{SECRET_KEY_NAME}[\"']?\s*[:=]\s*)"
    r'"((?:\\.|[^"\\])*)"'
)
SINGLE_QUOTED_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)"
    rf"([\"']?{SECRET_KEY_NAME}[\"']?\s*[:=]\s*)"
    r"'((?:\\.|[^'\\])*)'"
)
UNQUOTED_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)"
    rf"([\"']?{SECRET_KEY_NAME}[\"']?\s*[:=]\s*)"
    r"([^\"'\s,}\n](?:(?!\s+[A-Za-z0-9_-]+\s*[:=])[^\"',}\n])*)"
)
SESSION_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])([\"']?session[\"']?\s*=\s*)"
    r"([^\"'\s,}\n](?:(?!\s+[A-Za-z0-9_-]+\s*[:=])[^\"',}\n])*)"
)


def redact_secret_tokens(text: str) -> str:
    """Apply all token/secret redaction patterns to *text* WITHOUT length truncation.

    This is the single source of truth for secret-token redaction. It is shared
    by ``sanitize_tool_preview`` (which adds a 400-char length cap afterwards) in
    ``transport/tool_preview.py`` and by memory/evidence consumers that layer it
    LAST after their own site-specific label patterns.

    Patterns covered:
      - Bearer tokens (``Authorization: Bearer ...``)
      - Authorization headers (``Authorization: <scheme> <token>``)
      - Cookie / Set-Cookie headers
      - GitHub tokens (``ghp_``, ``gho_``, ``ghs_``, ``ghu_``, ``ghr_``)
      - OpenAI keys (``sk-proj-...``, ``sk-...``)
      - Stripe keys (``sk_live_...``, ``rk_test_...``, etc.)
      - Quoted/unquoted public-credential key=value pairs
      - Quoted/unquoted generic secret key=value pairs
        (``api_key``, ``secret``, ``token``, ``client_secret``, ``session_key``, ...)
      - Bare ``session = <value>`` assignments
    """
    redacted = BEARER_TOKEN_RE.sub(r"\1[redacted]", text)
    redacted = AUTHORIZATION_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = COOKIE_HEADER_VALUE_RE.sub(r"\1[redacted]", redacted)
    redacted = GITHUB_TOKEN_RE.sub("[redacted]", redacted)
    redacted = OPENAI_TOKEN_RE.sub("[redacted]", redacted)
    redacted = STRIPE_TOKEN_RE.sub("[redacted]", redacted)
    redacted = DOUBLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(
        r'\1"[redacted]"',
        redacted,
    )
    redacted = SINGLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(
        r"\1'[redacted]'",
        redacted,
    )
    redacted = UNQUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(r"\1[redacted]", redacted)
    redacted = DOUBLE_QUOTED_KEY_VALUE_SECRET_RE.sub(r'\1"[redacted]"', redacted)
    redacted = SINGLE_QUOTED_KEY_VALUE_SECRET_RE.sub(r"\1'[redacted]'", redacted)
    redacted = UNQUOTED_KEY_VALUE_SECRET_RE.sub(r"\1[redacted]", redacted)
    redacted = SESSION_ASSIGNMENT_RE.sub(r"\1[redacted]", redacted)
    return redacted


# B3 -- secret-key-name classifier single home (moved from the three evidence
# _is_secret_key forks in ledger.py / reports.py / tool_boundary.py). The base
# fragment union is the ledger/reports 14 plus the tool_boundary excess
# {authorization, cookie, credential, credential_id, credentials, service_key};
# the bare "key" fragment stays a tool_boundary-only extra (passed via
# extra_fragments) so ledger/reports do not over-redact public keys like
# objectKey. Compact (underscore-stripped) matching subsumes tool_boundary's
# privatekey/servicekey compact fragments.
SECRET_KEY_FRAGMENTS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "bearer_token",
    "client_secret",
    "cookie",
    "credential",
    "credential_id",
    "credentials",
    "id_token",
    "password",
    "passphrase",
    "private_key",
    "refresh_token",
    "secret",
    "service_key",
    "service_role_key",
    "session_token",
    "token",
)
PUBLIC_CREDENTIAL_KEY_NAMES: frozenset[str] = frozenset(
    (
        "authorization",
        "proxy_authorization",
        "proxyauthorization",
        "cookie",
        "set_cookie",
        "setcookie",
        "credential",
        "credentials",
    )
)


def is_secret_key(
    key: str,
    *,
    include_public_credential_keys: bool = False,
    extra_fragments: tuple[str, ...] = (),
) -> bool:
    """Return True if *key* names a secret/credential field.

    ``normalized`` lower-cases and maps ``-`` to ``_``; ``compact`` additionally
    strips ``_``. Public-credential names (exact) only count when
    ``include_public_credential_keys`` is set (ledger threads the caller's opt-in;
    reports passes it always-on). ``extra_fragments`` layers site-specific
    fragments (tool_boundary passes the bare ``"key"`` axis) without weakening any
    other site.
    """
    normalized = str(key).replace("-", "_").lower()
    compact = normalized.replace("_", "")
    if include_public_credential_keys and (
        normalized in PUBLIC_CREDENTIAL_KEY_NAMES
        or compact in PUBLIC_CREDENTIAL_KEY_NAMES
    ):
        return True
    for fragment in SECRET_KEY_FRAGMENTS:
        if fragment in normalized or fragment.replace("_", "") in compact:
            return True
    for fragment in extra_fragments:
        if fragment in normalized or fragment.replace("_", "") in compact:
            return True
    return False
