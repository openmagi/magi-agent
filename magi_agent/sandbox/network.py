from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, unquote, urlparse

from .policy import SandboxDecision, SandboxPolicy, build_decision, digest_payload, require_safe_ref


_METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.invalid",
        "169.254.169.254",
    }
)
_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth",
        "authorization",
        "credential",
        "key",
        "password",
        "secret",
        "session",
        "token",
    }
)
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)


def evaluate_network_access(policy: SandboxPolicy, *, url: str) -> SandboxDecision:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    reason_codes = _classify_parsed_url(parsed)
    safe_host = _safe_host(host)

    if not policy.allow_network:
        reason_codes.append("network_disabled")
    elif policy.network_allowlist and safe_host not in policy.network_allowlist:
        reason_codes.append("network_host_not_allowlisted")

    return build_decision(
        allowed=not reason_codes,
        operation="network",
        reason_codes=tuple(reason_codes),
        target_digest=digest_payload(
            {
                "scheme": parsed.scheme,
                "host": host,
                "pathDigest": digest_payload({"path": parsed.path or "/"}),
                "queryDigest": digest_payload({"query": parsed.query}),
            }
        ),
        target_kind="url",
        host=safe_host if host else None,
        policy=policy,
    )


def classify_network_url(url: str) -> tuple[str | None, tuple[str, ...]]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    reason_codes = _classify_parsed_url(parsed)
    return (_safe_host(host) if host else None), tuple(dict.fromkeys(reason_codes))


def _classify_parsed_url(parsed: object) -> list[str]:
    host = (getattr(parsed, "hostname", None) or "").lower().rstrip(".")
    scheme = getattr(parsed, "scheme", "")
    reason_codes: list[str] = []
    if not host or scheme not in {"http", "https"}:
        reason_codes.append("invalid_url_blocked")
    if _is_private_host(host):
        reason_codes.append("private_network_blocked")
    if host in _METADATA_HOSTS or "metadata" in host:
        reason_codes.append("metadata_endpoint_blocked")
    if _has_top_level_credential_material(parsed):
        reason_codes.append("credential_url_blocked")
    reason_codes.extend(_nested_query_url_reason_codes(getattr(parsed, "query", "")))
    return list(dict.fromkeys(reason_codes))


def _safe_host(host: str) -> str:
    if not host:
        return "invalid_host"
    if _coerce_ip_address(host) is None:
        return require_safe_ref(host.replace("-", "_"), field_name="host")
    return "ip_host"


def _is_private_host(host: str) -> bool:
    if host in {"localhost"} or host.endswith(".localhost"):
        return True
    address = _coerce_ip_address(host)
    if address is None:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def _coerce_ip_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    return _coerce_legacy_ipv4_address(host)


def _coerce_legacy_ipv4_address(host: str) -> ipaddress.IPv4Address | None:
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
    return ipaddress.IPv4Address(value)


def _parse_legacy_ipv4_part(part: str) -> int | None:
    try:
        if part.lower().startswith("0x"):
            return int(part, 16)
        if len(part) > 1 and part.startswith("0"):
            return int(part, 8)
        return int(part, 10)
    except ValueError:
        return None


def _has_top_level_credential_material(parsed_url: object) -> bool:
    parsed = urlparse(parsed_url) if isinstance(parsed_url, str) else parsed_url
    username = getattr(parsed, "username", None)
    password = getattr(parsed, "password", None)
    if username or password:
        return True
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.lower().replace("-", "_")
        if normalized in _CREDENTIAL_QUERY_KEYS or any(
            marker in normalized for marker in ("token", "secret", "credential", "password")
        ):
            return True
    return False


def _nested_query_url_reason_codes(
    query: str,
    *,
    depth: int = 0,
    visited: frozenset[str] = frozenset(),
) -> list[str]:
    if depth >= 4:
        return []
    reason_codes: list[str] = []
    for _key, value in parse_qsl(query, keep_blank_values=True):
        for variant in _decoded_variants(value):
            for nested_url in _URL_IN_TEXT_RE.findall(variant):
                clean_url = nested_url.rstrip(").,;")
                if clean_url in visited:
                    continue
                nested = urlparse(clean_url)
                nested_reasons = _classify_nested_url_without_deep_query(nested)
                reason_codes.extend(nested_reasons)
                reason_codes.extend(
                    _nested_query_url_reason_codes(
                        getattr(nested, "query", ""),
                        depth=depth + 1,
                        visited=visited | frozenset({clean_url}),
                    )
                )
    return list(dict.fromkeys(reason_codes))


def _classify_nested_url_without_deep_query(parsed: object) -> list[str]:
    host = (getattr(parsed, "hostname", None) or "").lower().rstrip(".")
    scheme = getattr(parsed, "scheme", "")
    reason_codes: list[str] = []
    if not host or scheme not in {"http", "https"}:
        reason_codes.append("invalid_url_blocked")
    if _is_private_host(host):
        reason_codes.append("private_network_blocked")
    if host in _METADATA_HOSTS or "metadata" in host:
        reason_codes.append("metadata_endpoint_blocked")
    if _has_top_level_credential_material(parsed):
        reason_codes.append("credential_url_blocked")
    return reason_codes


def _decoded_variants(value: str, *, rounds: int = 4) -> tuple[str, ...]:
    variants: list[str] = []
    current = value
    for _ in range(rounds + 1):
        if current not in variants:
            variants.append(current)
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return tuple(variants)


__all__ = ["classify_network_url", "evaluate_network_access"]
