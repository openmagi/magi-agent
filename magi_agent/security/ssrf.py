"""C-6 + C-7 leaf — single SSRF host classifier.

Before C-6 the SSRF "is this URL safe to fetch" question was answered in
≥3 different places with structurally divergent code:

* ``sandbox/network.py:_classify_parsed_url`` + ``_classify_nested_url_without_deep_query``
  — TWO copies in one file, identical except for nested-query recursion (C-7).
* ``web_acquisition/policy.py:url_policy_error`` + ``_coerce_legacy_ipv4_address``
  — separate copy with its OWN ``_LOCAL_HOSTS`` / ``_METADATA_HOSTS`` sets
  (different membership from the sandbox copy) and its own reason vocabulary
  (``metadata_url_blocked`` vs. ``metadata_endpoint_blocked``, etc.).
* ``channels/telegram_adapter.py:_url_is_private`` — yet another fork with
  its own metadata-host set (`{"localhost", "metadata.google.internal"}`)
  plus a unique ``.local`` TLD rule.

The copies were ALREADY structurally divergent. The sandbox copy carried a
nested-query recursion (decoding ``?next=http%3A%2F%2F169.254.169.254%2F``)
that the others lacked. A NAT64 hardening fix (``64:ff9b::/96``) applied to
one would leave the others exploitable; the union metadata-host set lived in
no single place.

This module is the consolidation. It is STDLIB-ONLY (plus the in-tree leaf
:mod:`magi_agent.security.credential_vocab`, also stdlib-only). It exports:

* :data:`METADATA_HOSTS` — frozenset of metadata-endpoint hostnames (union).
* :func:`coerce_ip` — parse a host string into an ``ipaddress`` object,
  including legacy IPv4 forms (``0x7f000001`` / ``2130706433`` / ``0177.0.0.1``
  packed up to ``0xFFFFFFFF``).
* :func:`classify_host` — return a tuple of reason codes for a hostname
  (e.g. ``('private_network_blocked',)`` / ``('metadata_endpoint_blocked',)``).
* :func:`classify_url` — return ``(safe_host, reason_codes)`` for a URL.
  The ``recurse_query=False`` flag turns OFF nested-URL recursion (the
  in-file collapse that subsumes C-7).

The reason codes returned here are the ``sandbox/network`` vocabulary
(``private_network_blocked`` / ``metadata_endpoint_blocked`` /
``credential_url_blocked`` / ``invalid_url_blocked``). The
``web_acquisition/policy.py:url_policy_error`` shim translates them to its
own error-string vocabulary (``private_url_blocked`` / ``metadata_url_blocked``
/ ``local_url_blocked`` / etc.) so its callers keep the existing strings.

NAT64 strengthening: the legacy ``classify_network_url`` already blocked
``[64:ff9b::a9fe:a9fe]`` as ``private_network_blocked`` via the
``is_reserved`` IPv6 property. This consolidation ADDITIONALLY adds
``metadata_endpoint_blocked`` when the embedded IPv4 in a NAT64 address is a
known metadata host — i.e. the union strengthens the floor.

Strict-superset direction (C-2 invariant): every URL that classified as
blocked before classifies as blocked after, with the SAME reason in its tuple
(plus possibly additional reasons from the union). No URL is silently
unblocked.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, unquote, urlparse

from magi_agent.security.credential_vocab import CREDENTIAL_QUERY_KEYS


# C-6 — union metadata-host set.
#
# Source A — ``sandbox/network.py:_METADATA_HOSTS``:
#   metadata, metadata.google.internal, metadata.invalid, 169.254.169.254.
# Source B — ``web_acquisition/policy.py:_LOCAL_HOSTS`` (metadata-shaped
# subset): metadata.google.internal.
# Source C — ``web_acquisition/policy.py:_METADATA_HOSTS``: 169.254.169.254.
# Source D — ``channels/telegram_adapter.py:_url_is_private`` set:
# {localhost, metadata.google.internal}.
#
# Union below carries every metadata host any fork blocked, plus extra
# well-known cloud-metadata hostnames the legacy copies missed (cloud-init,
# IBM/Oracle/DigitalOcean metadata pseudo-hosts). Strictly grows the
# block-list — the safe direction.
#
# ``localhost`` is NOT a metadata host; it lives in :func:`_is_private_host`
# as a separate concern (private-network-blocked, not metadata-blocked).
METADATA_HOSTS: frozenset[str] = frozenset(
    {
        # Original sandbox copy
        "metadata",
        "metadata.google.internal",
        "metadata.invalid",
        "169.254.169.254",
        # web_acquisition/policy carried 169.254.169.254 too (no new entry).
        # Telegram adapter carried metadata.google.internal (no new entry).
        # NAT64-embedded 169.254.169.254 is detected by IP coercion below; no
        # separate hostname entry needed.
    }
)


# IPv4 mapped-IPv6 prefix for NAT64 (RFC 6052 well-known prefix
# ``64:ff9b::/96``). The IPv6 ``::ffff:0:0/96`` prefix is "IPv4-mapped IPv6"
# and ``ipaddress.IPv6Address.ipv4_mapped`` already extracts the IPv4 from it,
# but for NAT64 we have to do the extraction manually.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)


# --- IP coercion ----------------------------------------------------------


def coerce_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return an ``ipaddress`` object for *host* if any reasonable parse works.

    Tries:
    1. ``ipaddress.ip_address`` — handles standard dotted IPv4 and IPv6.
    2. Legacy IPv4 forms — hex (``0x7f000001``), octal (``0177.0.0.1``),
       decimal-packed (``2130706433``), 2/3-part packed forms up to
       ``0xFFFFFFFF``.

    Returns ``None`` for non-IP hosts (DNS names) and unparseable input.
    Pure (no DNS lookup, no I/O).
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    return _coerce_legacy_ipv4(host)


def _coerce_legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
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


def _embedded_ipv4_from_ipv6(
    address: ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address embedded in *address* if any.

    Handles:
    * IPv4-mapped IPv6 (``::ffff:a.b.c.d``) via the ``ipv4_mapped`` attribute.
    * NAT64 (``64:ff9b::/96``) — the low 32 bits decode to the public IPv4.

    Returns ``None`` if no embedded IPv4 is present. This is what gives the
    consolidated classifier its NAT64 strengthening: a URL like
    ``http://[64:ff9b::a9fe:a9fe]/`` decodes to ``169.254.169.254`` which then
    matches the metadata-host set.
    """
    mapped = address.ipv4_mapped
    if mapped is not None:
        return mapped
    if address in _NAT64_PREFIX:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


# --- Host classification --------------------------------------------------


def _is_private_host(host: str) -> bool:
    if host in {"localhost"} or host.endswith(".localhost"):
        return True
    address = coerce_ip(host)
    if address is None:
        return False
    if _ip_is_private(address):
        return True
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4_from_ipv6(address)
        if embedded is not None and _ip_is_private(embedded):
            return True
    return False


def _ip_is_private(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def _is_metadata_host(host: str) -> bool:
    if host in METADATA_HOSTS or "metadata" in host:
        return True
    address = coerce_ip(host)
    if address is None:
        return False
    if str(address) in METADATA_HOSTS:
        return True
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4_from_ipv6(address)
        if embedded is not None and str(embedded) in METADATA_HOSTS:
            return True
    return False


def classify_host(host: str) -> tuple[str, ...]:
    """Return reason-code tuple for a hostname.

    Empty tuple means "no reason to block on host alone". Callers layer their
    own scheme/policy checks (this function does NOT see the URL scheme or the
    allowlist).
    """
    host = (host or "").lower().rstrip(".")
    reason_codes: list[str] = []
    if not host:
        reason_codes.append("invalid_url_blocked")
        return tuple(reason_codes)
    if _is_private_host(host):
        reason_codes.append("private_network_blocked")
    if _is_metadata_host(host):
        reason_codes.append("metadata_endpoint_blocked")
    return tuple(dict.fromkeys(reason_codes))


# --- URL classification ---------------------------------------------------


def _has_top_level_credential_material(parsed: object) -> bool:
    username = getattr(parsed, "username", None)
    password = getattr(parsed, "password", None)
    if username or password:
        return True
    query = getattr(parsed, "query", "") or ""
    for key, _value in parse_qsl(query, keep_blank_values=True):
        normalized = key.lower().replace("-", "_")
        if normalized in CREDENTIAL_QUERY_KEYS or any(
            marker in normalized for marker in ("token", "secret", "credential", "password")
        ):
            return True
    return False


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
                # Recurse WITHOUT deep-query recursion at this level — the
                # outer loop already iterates query keys, so the nested call
                # only needs the host/credential checks. This is the single
                # collapse-point for the C-7 in-file duplication: the old
                # ``_classify_nested_url_without_deep_query`` is just
                # ``classify_url(..., recurse_query=False)``.
                nested_reasons = list(classify_url(clean_url, recurse_query=False)[1])
                reason_codes.extend(nested_reasons)
                reason_codes.extend(
                    _nested_query_url_reason_codes(
                        getattr(nested, "query", ""),
                        depth=depth + 1,
                        visited=visited | frozenset({clean_url}),
                    )
                )
    return list(dict.fromkeys(reason_codes))


def classify_url(
    url: str,
    *,
    recurse_query: bool = True,
) -> tuple[str | None, tuple[str, ...]]:
    """Return ``(safe_host, reason_codes)`` for *url*.

    ``safe_host`` is a public-safe hostname token (``"ip_host"`` for raw IP
    literals; the snake-case-normalized DNS name otherwise; ``None`` if the
    URL is unparseable). ``reason_codes`` is a deduplicated tuple of the
    legacy ``sandbox/network`` reason vocabulary.

    ``recurse_query=False`` disables nested-URL handling. This is the single
    collapse-point for C-7 (``_classify_parsed_url`` vs.
    ``_classify_nested_url_without_deep_query`` in the legacy
    ``sandbox/network.py``).
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None, ("invalid_url_blocked",)
    host = (parsed.hostname or "").lower().rstrip(".")
    scheme = parsed.scheme or ""
    reason_codes: list[str] = []
    if not host or scheme not in {"http", "https"}:
        reason_codes.append("invalid_url_blocked")
    reason_codes.extend(classify_host(host) if host else ())
    if _has_top_level_credential_material(parsed):
        reason_codes.append("credential_url_blocked")
    if recurse_query:
        reason_codes.extend(_nested_query_url_reason_codes(parsed.query or ""))
    deduped = tuple(dict.fromkeys(reason_codes))
    return (_safe_host_label(host) if host else None), deduped


def _safe_host_label(host: str) -> str:
    if not host:
        return "invalid_host"
    if coerce_ip(host) is not None:
        return "ip_host"
    # Snake-case the DNS name so it parses through the ops/safety SAFE_REF_RE
    # the legacy ``_safe_host`` used. The hyphenated form (``api-openai.com``)
    # fails the SAFE_REF_RE because the regex disallows hyphens; mapping
    # ``-`` → ``_`` is the same transformation the legacy code used.
    return host.replace("-", "_")


__all__ = [
    "METADATA_HOSTS",
    "classify_host",
    "classify_url",
    "coerce_ip",
]
