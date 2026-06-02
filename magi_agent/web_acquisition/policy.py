from __future__ import annotations

import hashlib
from ipaddress import IPv4Address, ip_address, ip_network
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_LOCAL_HOSTS = frozenset(
    {
        "localhost",
        "0.0.0.0",
        "host.docker.internal",
        "docker.for.mac.localhost",
        "metadata.google.internal",
    }
)
_METADATA_HOSTS = frozenset({"169.254.169.254"})
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


def normalize_query(query: str, *, max_chars: int = 512) -> str:
    normalized = " ".join(query.strip().split())
    if not normalized:
        raise ValueError("query is required")
    return redact_public_text(normalized)[:max_chars]


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
    if host in _METADATA_HOSTS:
        return "metadata_url_blocked"
    if host in _CLUSTER_HOSTS or any(host.endswith(part) for part in _CLUSTER_HOST_PARTS):
        return "cluster_url_blocked"
    try:
        parsed_ip = _coerce_ip_address(host)
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None:
        if str(parsed_ip) in _METADATA_HOSTS:
            return "metadata_url_blocked"
        if parsed_ip.is_loopback or parsed_ip.is_link_local or parsed_ip.is_unspecified:
            return "local_url_blocked"
        if parsed_ip in _CGNAT_NETWORK:
            return "private_url_blocked"
        if (
            parsed_ip.is_private
            or parsed_ip.is_reserved
            or parsed_ip.is_multicast
            or not parsed_ip.is_global
        ):
            return "private_url_blocked"
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
    public_lines = [
        line for line in text.splitlines() if not _RAW_PRIVATE_LINE_RE.search(line)
    ]
    redacted = "\n".join(public_lines)
    redacted = _SENSITIVE_URL_RE.sub("[redacted-url]", redacted)
    redacted = _SECRET_TEXT_RE.sub("[redacted]", redacted)
    redacted = _PRIVATE_PATH_RE.sub("[redacted-path]", redacted)
    return redacted[:max_chars]


def safe_metadata(metadata: object) -> dict[str, object]:
    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(
            marker in normalized_key
            for marker in (
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
            )
        ):
            continue
        if isinstance(value, str):
            safe[str(key)] = redact_public_text(value, max_chars=512)
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def synthetic_url_ref(value: str, *, prefix: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _coerce_ip_address(host: str) -> object:
    try:
        return ip_address(host)
    except ValueError:
        return _coerce_legacy_ipv4_address(host)


def _coerce_legacy_ipv4_address(host: str) -> IPv4Address | None:
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or any(part == "" for part in parts):
        return None
    parsed_parts: list[int] = []
    for part in parts:
        parsed = _parse_legacy_ipv4_part(part)
        if parsed is None:
            return None
        parsed_parts.append(parsed)
    if len(parsed_parts) == 1:
        value = parsed_parts[0]
    elif len(parsed_parts) == 2:
        if parsed_parts[0] > 0xFF or parsed_parts[1] > 0xFFFFFF:
            return None
        value = (parsed_parts[0] << 24) | parsed_parts[1]
    elif len(parsed_parts) == 3:
        if parsed_parts[0] > 0xFF or parsed_parts[1] > 0xFF or parsed_parts[2] > 0xFFFF:
            return None
        value = (parsed_parts[0] << 24) | (parsed_parts[1] << 16) | parsed_parts[2]
    else:
        if any(part > 0xFF for part in parsed_parts):
            return None
        value = (
            (parsed_parts[0] << 24)
            | (parsed_parts[1] << 16)
            | (parsed_parts[2] << 8)
            | parsed_parts[3]
        )
    if not 0 <= value <= 0xFFFFFFFF:
        return None
    return IPv4Address(value)


def _parse_legacy_ipv4_part(part: str) -> int | None:
    try:
        if part.casefold().startswith("0x"):
            return int(part, 16)
        if len(part) > 1 and part.startswith("0"):
            return int(part, 8)
        return int(part, 10)
    except ValueError:
        return None


__all__ = [
    "content_digest",
    "evidence_ref",
    "normalize_public_url",
    "normalize_query",
    "redact_public_text",
    "safe_metadata",
    "source_ref",
    "synthetic_url_ref",
    "url_policy_error",
]
