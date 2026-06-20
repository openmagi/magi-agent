"""C-9 RED → GREEN: single credential vocabulary leaf.

Asserts :func:`magi_agent.security.credential_vocab.looks_like_credential`
catches every credential-shaped label any legacy fork caught (lease side +
SSRF query-key side + their union), plus a round-trip check that the lease
validator (``security.credentials._public_lease_ref``) and the lease-ref
regex agree on what they accept.
"""

from __future__ import annotations

import pytest

from magi_agent.security.credential_vocab import (
    CREDENTIAL_QUERY_KEYS,
    LEASE_REF_RE,
    SENSITIVE_LEASE_FRAGMENTS,
    looks_like_credential,
)


@pytest.mark.parametrize(
    "value",
    (
        "api_key",
        "api-key",
        "apikey",
        "token",
        "access_token",
        "authorization",
        "password",
        "secret",
        "credential",
        "key",
        "session",
        "auth",
        # lease-fragment hits via substring match
        "production_token",
        "user_secret_v2",
        "akia-payload",
        "credential-lease:abc",  # accept on valid lease ref
    ),
)
def test_looks_like_credential_catches_each_legacy_shape(value: str) -> None:
    assert looks_like_credential(value), (
        f"{value!r} should be flagged as credential-shaped by the C-9 union vocab."
    )


@pytest.mark.parametrize(
    "value",
    (
        "user_id",
        "request_id",
        "trace_id",
        "version",
        "count",
        "name",
        "title",
        "https://example.com/",
    ),
)
def test_looks_like_credential_passes_public_labels(value: str) -> None:
    assert not looks_like_credential(value), (
        f"{value!r} should NOT be flagged as credential-shaped (public label)."
    )


def test_credential_query_keys_union_covers_legacy_sandbox_set() -> None:
    """The legacy ``sandbox/network.py:_CREDENTIAL_QUERY_KEYS`` must be a
    SUBSET of the consolidated set (strict-superset C-2 invariant)."""
    legacy = frozenset(
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
    assert legacy.issubset(CREDENTIAL_QUERY_KEYS)


def test_sensitive_fragments_byte_identical_to_legacy_lease_set() -> None:
    """The lease-side ``SENSITIVE_LEASE_FRAGMENTS`` must be BYTE-IDENTICAL to
    the legacy ``security/credentials.py:_SENSITIVE_LEASE_FRAGMENTS``.

    Adding ``"credential"`` / ``"key"`` from the SSRF query-key vocab would
    break every valid ``credential-lease:`` ref (the prefix itself contains
    the substring ``"credential"``). That is the opposite-direction silent
    change C-2 also bans — the union direction is the union of REJECTIONS,
    not the union of vocabularies, and the lease side already had the most
    specific fragment (``credential-value``).
    """
    legacy = frozenset(
        {
            "akia",
            "api-key",
            "apikey",
            "asia",
            "auth",
            "cookie",
            "credential-value",
            "private",
            "secret",
            "sk-",
            "session",
            "token",
        }
    )
    assert SENSITIVE_LEASE_FRAGMENTS == legacy


def test_lease_ref_regex_round_trips_through_validator() -> None:
    """A lease ref that ``LEASE_REF_RE`` accepts MUST also pass the
    ``security.credentials._public_lease_ref`` validator (modulo the sensitive-
    fragment guard). Cross-side accept/reject must agree."""
    from magi_agent.security.credentials import _public_lease_ref

    public_ok = "credential-lease:tenant.scope"
    public_bad = "lease:wrong-prefix"
    sensitive = "credential-lease:has-token"  # matches "token" fragment

    assert LEASE_REF_RE.fullmatch(public_ok) is not None
    assert _public_lease_ref(public_ok) == public_ok

    assert LEASE_REF_RE.fullmatch(public_bad) is None
    assert _public_lease_ref(public_bad) is None

    # Both sides see the same regex; sensitive-fragment guard is layered above.
    assert LEASE_REF_RE.fullmatch(sensitive) is not None
    assert _public_lease_ref(sensitive) is None
