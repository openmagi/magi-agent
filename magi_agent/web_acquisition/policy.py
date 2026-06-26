from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from magi_agent.evidence.run_redaction import (
    redact_public_text as _redact_public_share_text,
)
from magi_agent.ops.safety import public_diagnostic_metadata
from magi_agent.security.ssrf import (
    METADATA_HOSTS as _SSRF_METADATA_HOSTS,
    coerce_ip as _ssrf_coerce_ip,
)


_LOCAL_HOSTS = frozenset(
    {
        "localhost",
        "0.0.0.0",
        "host.docker.internal",
        "docker.for.mac.localhost",
        "metadata.google.internal",
    }
)
# C-6: IP-literal SUBSET of the kernel metadata-host set, derived (not
# re-declared) from :data:`magi_agent.security.ssrf.METADATA_HOSTS`. The
# web-acquisition layer's ``url_policy_error`` historically returned
# ``"metadata_url_blocked"`` ONLY for the IP-literal metadata case
# (``169.254.169.254``); DNS-name metadata hosts (``metadata.google.internal``)
# were routed through the separate ``_LOCAL_HOSTS`` set and got
# ``"local_url_blocked"``. We must preserve that DNS-vs-IP split because
# downstream callers branch on the exact returned string; widening the
# DNS-name set here would silently re-categorize existing public-allowed URLs.
#
# The derivation below keeps this set in lockstep with the kernel: any new
# IP-literal metadata host added to the kernel automatically lands here.
# The set IS a strict subset of the kernel (so the kernel meta-test allowing
# only the kernel-leaf can confirm by construction that we did not re-fork).
_METADATA_HOST_IPS = frozenset(
    host for host in _SSRF_METADATA_HOSTS if host.replace(".", "").isdigit()
)
_CGNAT_NETWORK = ip_network("100.64.0.0/10")
_CLUSTER_HOSTS = frozenset({"browser-worker", "kubernetes", "kubernetes.default.svc"})
_CLUSTER_HOST_PARTS = (".cluster.local", ".svc", ".svc.cluster.local")
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth",
        "authorization",
        "cookie",
        "credential",
        "key",
        "password",
        "private_key",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
        "x-amz-signature",
        "x-amz-credential",
        "awsaccesskeyid",
        "googleaccessid",
    }
)
_SENSITIVE_QUERY_KEY_PARTS = frozenset(
    {"signature", "credential", "accesskey", "accessid", "token", "secret"}
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD)[A-Z0-9_]*\s*[:=]\s*[^,\s}{]{4,})",
    re.IGNORECASE,
)
_SENSITIVE_URL_RE = re.compile(
    r"(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|file|vault|"
    r"secret|secrets)://[^\s\"'<>]+|"
    r"https?://(?:"
    r"(?:storage\.googleapis\.com|storage\.cloud\.google\.com|[^/\s\"'<>]*\.storage\.googleapis\.com)|"
    r"(?:[^/\s\"'<>]*s3[^/\s\"'<>]*\.amazonaws\.com|s3[.-][^/\s\"'<>]*\.amazonaws\.com)|"
    r"(?:[^/\s\"'<>]*\.supabase\.co/storage/)|"
    r"(?:[^/\s\"'<>]*\.r2\.cloudflarestorage\.com)|"
    r"(?:[^/\s\"'<>]*blob\.core\.windows\.net)"
    r")[^\s\"'<>]*|"
    r"https?://api\.telegram\.org/bot[0-9]+:[^/\s\"'<>]+[^\s\"'<>]*|"
    r"https?://[^\s\"'<>]*[?&](?:X-Amz-Signature|access[_-]?token|api[_-]?key|auth|"
    r"authorization|cookie|credential|key|password|private[_-]?key|secret|session|"
    r"sig|signature|token)=[^\s\"'<>]+",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/workspace(?:/[^,\s\"']*)?|/data/bots(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_-]?(?:tool|browser|snapshot|transcript|prompt|content)|"
    r"hidden[_-]?reasoning|chain[_-]?of[_-]?thought|private[_-]?data|"
    r"captcha|cookie|authorization",
    re.IGNORECASE,
)
_RECENCY_INTENT_RE = re.compile(
    r"\b(?:latest|recent|today|this\s+year|newest|up\s+to\s+date)\b",
    re.IGNORECASE,
)
_EXISTING_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def normalize_query(
    query: str,
    *,
    max_chars: int = 512,
    inject_recency_year: bool = False,
    now: datetime | None = None,
) -> str:
    normalized = " ".join(query.strip().split())
    if not normalized:
        raise ValueError("query is required")
    result = redact_public_text(normalized)[:max_chars]
    if inject_recency_year:
        if _RECENCY_INTENT_RE.search(result) and not _EXISTING_YEAR_RE.search(result):
            effective_now = now if now is not None else datetime.now(timezone.utc)
            year_suffix = f" {effective_now.year}"
            if len(result) + len(year_suffix) <= max_chars:
                result = result + year_suffix
    return result


def url_policy_error(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "invalid_url"
    if parts.scheme not in {"http", "https"}:
        return "invalid_url"
    if _SENSITIVE_URL_RE.search(url):
        return "credential_url_blocked"
    if parts.username or parts.password:
        return "auth_bypass_blocked"
    host = (parts.hostname or "").casefold().rstrip(".")
    if not host:
        return "invalid_url"
    if host in _LOCAL_HOSTS or host.endswith(".localhost"):
        return "local_url_blocked"
    if host in _METADATA_HOST_IPS:
        return "metadata_url_blocked"
    if host in _CLUSTER_HOSTS or any(host.endswith(part) for part in _CLUSTER_HOST_PARTS):
        return "cluster_url_blocked"
    try:
        parsed_ip = _coerce_ip_address(host)
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None:
        ip_block = _classify_blocked_ip(parsed_ip)
        if ip_block is not None:
            return ip_block
    sensitive_keys = {key.casefold() for key, _value in parse_qsl(parts.query, keep_blank_values=True)}
    if sensitive_keys.intersection(_SENSITIVE_QUERY_KEYS) or any(
        any(part in key.replace("-", "").replace("_", "") for part in _SENSITIVE_QUERY_KEY_PARTS)
        for key in sensitive_keys
    ):
        return "credential_url_blocked"
    lowered = url.casefold()
    if "captcha" in lowered:
        return "captcha_flow_blocked"
    return None


def _classify_blocked_ip(parsed_ip: object) -> str | None:
    """Shared private/metadata/reserved/CGNAT classification for a parsed IP.

    Pure (no DNS / no network). Returns the same reason codes
    ``url_policy_error`` historically returned inline so the literal-URL
    firewall and the DNS-rebinding egress resolver share one source of truth.
    The DNS resolver lives in ``live_fetch_provider`` (network), not here.
    """
    if not isinstance(parsed_ip, IPv4Address | IPv6Address):
        return None
    if str(parsed_ip) in _METADATA_HOST_IPS:
        return "metadata_url_blocked"
    if parsed_ip.is_loopback or parsed_ip.is_link_local or parsed_ip.is_unspecified:
        return "local_url_blocked"
    if isinstance(parsed_ip, IPv4Address) and parsed_ip in _CGNAT_NETWORK:
        return "private_url_blocked"
    if (
        parsed_ip.is_private
        or parsed_ip.is_reserved
        or parsed_ip.is_multicast
        or not parsed_ip.is_global
    ):
        return "private_url_blocked"
    return None


def is_blocked_ip(ip_str: str) -> bool:
    """Return True if ``ip_str`` is a blocked egress target.

    Wraps the same classification used by ``url_policy_error`` so the
    DNS-rebinding egress guard cannot drift from the literal-URL firewall.
    Pure (no network); the caller resolves the host first.
    """
    try:
        parsed = ip_address(ip_str)
    except ValueError:
        # Unparseable address is treated as blocked (fail-safe).
        return True
    return _classify_blocked_ip(parsed) is not None


def normalize_public_url(url: str) -> str:
    parts = urlsplit(url)
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _SENSITIVE_QUERY_KEYS
    ]
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", urlencode(filtered_query), ""))


def source_ref(kind: str, source_index: int) -> str:
    return f"source:{kind}:src_{source_index}"


def evidence_ref(kind: str, source_index: int) -> str:
    return f"evidence:{kind}:src_{source_index}"


def content_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def redact_public_text(text: str, *, max_chars: int = 2_048) -> str:
    """Scrub fetched/acquired free text for a PUBLIC web-acquisition surface.

    Web-specific structural redactions run first (drop whole lines carrying
    agent-internal markers, redact storage/signed URLs), then the canonical
    public-share kernel
    (:func:`magi_agent.evidence.run_redaction.redact_public_text`, built on
    ``ops.safety``) closes the gaps this module used to leak: basic-auth URL
    userinfo, quoted credential values, internal cluster hostnames, RFC1918 IPs,
    emails, and the full provider-token denylist. The local ``_SECRET_TEXT_RE`` /
    ``_PRIVATE_PATH_RE`` remain as a defense-in-depth backstop for generic
    ``KEY=VALUE`` / workspace-path shapes the shared kernel intentionally scopes
    out. Coverage is a strict superset of the previous fork; the result is clipped
    to ``max_chars``.
    """
    if not isinstance(text, str):
        return ""
    public_lines = [
        line for line in text.splitlines() if not _RAW_PRIVATE_LINE_RE.search(line)
    ]
    redacted = "\n".join(public_lines)
    redacted = _SENSITIVE_URL_RE.sub("[redacted-url]", redacted)
    redacted = _SECRET_TEXT_RE.sub("[redacted]", redacted)
    redacted = _PRIVATE_PATH_RE.sub("[redacted-path]", redacted)
    # Canonical public-share kernel runs LAST as the additive superset: it closes
    # the gap (basic-auth userinfo, quoted credentials, cluster hostnames, RFC1918
    # IPs, emails, full provider denylist) without relabelling what the
    # web-specific passes above already redacted.
    redacted = _redact_public_share_text(redacted, max_chars=None)
    return redacted[:max_chars]


def safe_metadata(metadata: object) -> dict[str, object]:
    """One-line re-export of :func:`magi_agent.ops.safety.public_diagnostic_metadata`.

    C-2 reconciled the two ``safe_metadata`` functions (strict allow-list in
    ``ops/safety.py`` vs. lenient deny-list here) by giving this lenient web
    variant an explicit name (:func:`public_diagnostic_metadata`) in the kernel
    and turning the policy-level entry into this thin shim, preserving every
    existing import in ``provider_router.py`` / ``research_tools.py`` etc. The
    behavior change is the redactor: the per-value string scrub now routes
    through the C-1 kernel :func:`redact_private_text` (strict superset of the
    legacy ``redact_public_text`` denylist) rather than a local fork. Keys,
    deny-list marker set, primitive-type filter, and 512-char clip are
    byte-identical to the pre-C-2 behavior for inputs that do not carry
    legacy-fork-missed secret shapes.
    """
    if not isinstance(metadata, Mapping):
        return {}
    return public_diagnostic_metadata(metadata, max_chars=512)


def synthetic_url_ref(value: str, *, prefix: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _coerce_ip_address(host: str) -> object:
    """Web-acquisition-facing IP coercion shim.

    C-6 consolidation: the standalone ``_coerce_legacy_ipv4_address`` +
    ``_parse_legacy_ipv4_part`` helpers (that duplicated the sandbox copy
    line-for-line) moved to :func:`magi_agent.security.ssrf.coerce_ip`. This
    shim keeps the local name so existing callers (``url_policy_error``,
    ``is_blocked_ip``) and the internal ``_classify_blocked_ip`` path do not
    need to change call sites.
    """
    return _ssrf_coerce_ip(host)


__all__ = [
    "content_digest",
    "evidence_ref",
    "is_blocked_ip",
    "normalize_public_url",
    "normalize_query",
    "redact_public_text",
    "safe_metadata",
    "source_ref",
    "synthetic_url_ref",
    "url_policy_error",
]
