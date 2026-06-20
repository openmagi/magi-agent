"""C-6 RED → GREEN: single SSRF classifier with NAT64 strengthening + C-7 collapse.

Asserts that :func:`magi_agent.security.ssrf.classify_url` /
:func:`classify_host` / :func:`coerce_ip` cover every attack-host shape that
the legacy ``sandbox.network._classify_parsed_url`` /
``web_acquisition.policy._classify_blocked_ip`` /
``channels.telegram_adapter._url_is_private`` forks covered, PLUS the NAT64
``64:ff9b::/96`` family that no fork handled (i.e. consolidation strengthened
the floor — strict-superset direction, the safe C-2 invariant).
"""

from __future__ import annotations

import pytest

from magi_agent.security.ssrf import classify_host, classify_url, coerce_ip


_PRIVATE = "private_network_blocked"
_METADATA = "metadata_endpoint_blocked"
_CREDENTIAL = "credential_url_blocked"
_INVALID = "invalid_url_blocked"


@pytest.mark.parametrize(
    "host,expected",
    (
        # ipv4 dotted private/loopback
        ("127.0.0.1", _PRIVATE),
        ("10.0.0.1", _PRIVATE),
        ("192.168.1.1", _PRIVATE),
        ("172.16.0.1", _PRIVATE),
        # link-local + standard metadata
        ("169.254.169.254", _PRIVATE),
        ("metadata.google.internal", _METADATA),
        ("metadata.invalid", _METADATA),
        ("metadata", _METADATA),
        # legacy IPv4 forms — all decode to a private/metadata target
        ("0x7f000001", _PRIVATE),       # hex packed → 127.0.0.1
        ("2130706433", _PRIVATE),        # decimal packed → 127.0.0.1
        ("0177.0.0.1", _PRIVATE),        # octal first part → 127.0.0.1
        # IPv6 + NAT64 (the strengthened-floor cases)
        ("::1", _PRIVATE),
        ("fe80::1", _PRIVATE),
        ("::ffff:169.254.169.254", _PRIVATE),
        # localhost label family
        ("localhost", _PRIVATE),
        ("foo.localhost", _PRIVATE),
    ),
)
def test_classify_host_blocks_each_legacy_ssrf_shape(host: str, expected: str) -> None:
    reasons = classify_host(host)
    assert expected in reasons, (
        f"{host!r} should classify with {expected!r}; got {reasons!r}. "
        f"Legacy fork covered this case — consolidation must too."
    )


def test_classify_host_nat64_strengthens_floor_with_metadata_reason() -> None:
    """C-6 strengthens the SSRF floor on NAT64 embedded-IPv4-metadata.

    Legacy ``sandbox.network`` classified ``64:ff9b::a9fe:a9fe`` as
    ``private_network_blocked`` only (because IPv6 ``is_reserved`` is True).
    The consolidation ADDS ``metadata_endpoint_blocked`` because the embedded
    IPv4 is the AWS metadata host. Strict-superset direction: existing reason
    is preserved, new reason is added.
    """
    reasons = classify_host("64:ff9b::a9fe:a9fe")
    assert _PRIVATE in reasons, "NAT64 must still be private-blocked"
    assert _METADATA in reasons, (
        "NAT64-embedded 169.254.169.254 must also be metadata-blocked — "
        "C-6 strengthens the floor on the embedded IPv4."
    )


def test_classify_host_empty_returns_invalid() -> None:
    assert classify_host("") == (_INVALID,)


def test_classify_host_public_returns_empty() -> None:
    assert classify_host("api.openai.com") == ()
    assert classify_host("8.8.8.8") == ()
    assert classify_host("1.1.1.1") == ()


def test_coerce_ip_handles_legacy_ipv4_forms() -> None:
    assert str(coerce_ip("0x7f000001")) == "127.0.0.1"
    assert str(coerce_ip("2130706433")) == "127.0.0.1"
    assert str(coerce_ip("0177.0.0.1")) == "127.0.0.1"
    assert str(coerce_ip("017700000001")) == "127.0.0.1"
    assert str(coerce_ip("0xa9fea9fe")) == "169.254.169.254"
    assert coerce_ip("example.com") is None
    assert coerce_ip("not.an.ip.host") is None


def test_classify_url_credential_query_blocks() -> None:
    _safe, reasons = classify_url("https://example.com/?token=abc")
    assert _CREDENTIAL in reasons


def test_classify_url_top_level_userinfo_blocks() -> None:
    _safe, reasons = classify_url("https://" + "user:pass" + "@example.com/")
    assert _CREDENTIAL in reasons


def test_classify_url_nested_redirector_recurses_when_recurse_query_true() -> None:
    _safe, reasons = classify_url(
        "https://example.com/?next=http%3A%2F%2F169.254.169.254%2F",
        recurse_query=True,
    )
    assert _PRIVATE in reasons
    assert _METADATA in reasons


def test_classify_url_recurse_query_false_skips_nested() -> None:
    """C-7 collapse: ``recurse_query=False`` matches the legacy
    ``_classify_nested_url_without_deep_query`` behavior (the second copy in
    the same file) — host/credential checks only, no nested-URL parsing."""
    _safe, reasons = classify_url(
        "https://example.com/?next=http%3A%2F%2F169.254.169.254%2F",
        recurse_query=False,
    )
    assert _PRIVATE not in reasons
    assert _METADATA not in reasons


def test_classify_url_double_encoded_nested_redirector() -> None:
    _safe, reasons = classify_url(
        "https://example.com/?next=http%253A%252F%252Fmetadata.invalid%252Flatest",
    )
    assert _METADATA in reasons


def test_classify_url_depth_bound_terminates_on_self_reference() -> None:
    """C-7 collapse closes the depth-bounding seam: nested-query recursion
    must terminate even on a self-referential URL chain."""
    self_ref = (
        "https://example.com/?next=https%3A%2F%2Fexample.com%2F%3Fnext%3D"
        "https%253A%252F%252Fexample.com%252F"
    )
    # The call must return WITHOUT a RecursionError. The reasons may be empty
    # (the chain is all-public) — what matters is termination.
    _safe, reasons = classify_url(self_ref)
    assert isinstance(reasons, tuple)


def test_classify_url_invalid_scheme_blocks() -> None:
    _safe, reasons = classify_url("file:///etc/passwd")
    assert _INVALID in reasons


def test_classify_url_safe_host_for_dns_ip_and_invalid() -> None:
    safe_dns, _ = classify_url("https://api.openai.com/v1/chat")
    assert safe_dns == "api.openai.com"
    safe_ip, _ = classify_url("http://127.0.0.1/")
    assert safe_ip == "ip_host"
    safe_empty, _ = classify_url("file:///etc/passwd")
    assert safe_empty is None
