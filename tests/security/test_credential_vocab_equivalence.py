"""C-9 EQUIVALENCE GOLDEN — lease-ref validator byte-identical.

Asserts :func:`magi_agent.security.credentials._public_lease_ref` produces
byte-identical output on a fixture table before / after the C-9 vocab
consolidation. The legacy lease-ref grammar (``^credential-lease:[a-z0-9_.:-]{3,160}$``)
and sensitive-fragment denylist MUST remain a SUBSET of the consolidated
vocab; every lease ref that validated before keeps validating.
"""

from __future__ import annotations

import pytest


# Legacy golden — captured from pre-C-9 ``security/credentials.py:_public_lease_ref``.
_PUBLIC_LEASE_REF_GOLDEN: tuple[tuple[str | None, str | None], ...] = (
    (None, None),
    ("credential-lease:abc", "credential-lease:abc"),
    ("credential-lease:scope.tenant", "credential-lease:scope.tenant"),
    ("credential-lease:has-token", None),         # contains "token" → sensitive
    ("credential-lease:akia-pattern", None),      # contains "akia" → sensitive
    ("credential-lease:api-key", None),           # contains "api-key" → sensitive
    ("credential-lease:no-bad-fragment", "credential-lease:no-bad-fragment"),
    ("credential-lease:credential-value", None),  # contains "credential-value" → sensitive
    ("lease:wrong-prefix", None),                 # wrong prefix
    ("credential-lease:" + "X" * 200, None),      # over length bound
    ("", None),
)


@pytest.mark.parametrize("value,expected", _PUBLIC_LEASE_REF_GOLDEN)
def test_public_lease_ref_equivalence(value: str | None, expected: str | None) -> None:
    """Byte-identical on every legacy lease-ref fixture."""
    from magi_agent.security.credentials import _public_lease_ref

    assert _public_lease_ref(value) == expected, (
        f"_public_lease_ref drift on {value!r}: legacy={expected!r} "
        f"new={_public_lease_ref(value)!r}"
    )
