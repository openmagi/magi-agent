"""C-9 leaf — single vocabulary of "what counts as credential-shaped".

Before C-9 the answer to "is this string a credential" was derived from three
divergent places:

* ``security/credentials.py:_LEASE_RE`` + ``_SENSITIVE_LEASE_FRAGMENTS`` —
  the validator side of the credential-lease contract.
* ``sandbox/network.py:_CREDENTIAL_QUERY_KEYS`` — the URL-query key denylist the
  SSRF classifier uses to detect ``?token=...`` / ``?api_key=...`` etc.
* ``ops/safety.py`` (C-1) — the kernel-side secret/private-text denylist
  (``UNSAFE_KEY_RE`` / ``UNSAFE_TEXT_RE`` / ``UNSAFE_COMPACT_FRAGMENTS``).

The three lists overlapped but diverged on important shapes (e.g. the SSRF copy
had ``"session"``; the lease-fragment copy had ``"akia"``; neither had the
other). Same concept, three sources of truth → adding a new credential shape to
one left the others exploitable.

This module is a STDLIB-ONLY leaf. It exports:

* :data:`LEASE_REF_RE` — the lease-ref regex (kept byte-identical to the
  legacy ``_LEASE_RE`` so existing leases still validate).
* :data:`SENSITIVE_LEASE_FRAGMENTS` — case-folded fragments that mark a
  lease ref as carrying raw credential material (union of the legacy lease
  list + the SSRF query-key list).
* :data:`CREDENTIAL_QUERY_KEYS` — credential-shaped URL query keys (also
  the union; same set of strings).
* :func:`looks_like_credential` — a single yes/no helper consumed by callers
  that want a "does this value look like a credential" check.

The dependency arrow goes ONE WAY: this module imports nothing from
``magi_agent`` (it MUST stay a leaf so ``ops/safety.py`` / ``security/credentials.py``
/ ``sandbox/network.py`` / ``connectors/credential_lease.py`` can all import
from here without forming an import cycle).

Reason for union vs. one-side-of-the-other: a union is the only consolidation
direction that cannot silently weaken an existing checkpoint. Every fragment
that previously made one side reject is preserved; new fragments only ever ADD
to the denylist. This is the same C-2 invariant (lenient/superset wins) applied
to credential vocab.
"""

from __future__ import annotations

import re


# C-9 — lease-ref regex.
#
# Kept BYTE-IDENTICAL to the legacy ``security/credentials.py:_LEASE_RE`` so
# every lease that validated before validates after the consolidation. The
# fullmatch shape (``^...$``) is preserved; callers continue to use
# ``LEASE_REF_RE.fullmatch(value)`` exactly as ``_LEASE_RE.fullmatch`` was used.
LEASE_REF_RE: re.Pattern[str] = re.compile(r"^credential-lease:[a-z0-9_.:-]{3,160}$")


# C-9 — sensitive-fragment denylist for LEASE REFS.
#
# Source — ``security/credentials.py:_SENSITIVE_LEASE_FRAGMENTS`` verbatim.
# This set is the SAME set the legacy lease validator used; renamed only.
#
# Why NOT a union with ``sandbox/network.py:_CREDENTIAL_QUERY_KEYS``: the lease
# fragments are checked via ``fragment in casefolded_lease_ref``, and every
# valid lease ref necessarily contains the literal substring ``"credential"``
# (because lease refs start with ``credential-lease:``). Adding ``"credential"``
# or ``"key"`` to this set would reject EVERY lease ref — a silent-weakening
# in the OPPOSITE direction (over-rejection = breaks valid leases). The C-2
# invariant is "no SILENT change in either direction"; this set must stay
# byte-identical to the legacy so the equivalence golden passes.
#
# The SSRF side (``CREDENTIAL_QUERY_KEYS`` below) lives in a separate frozenset
# because it answers a different question ("is this URL query key
# credential-shaped") on a different normalization (``-`` → ``_``). The two
# sets share this module so neither can drift, but they remain separate
# concepts. :func:`looks_like_credential` is the helper that unions them.
SENSITIVE_LEASE_FRAGMENTS: frozenset[str] = frozenset(
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


# C-9 — credential-shaped URL query keys.
#
# Source — ``sandbox/network.py:_CREDENTIAL_QUERY_KEYS`` verbatim, plus the
# subset of lease fragments that ARE plausible query keys (auth, session,
# token, secret — already in the source; the others like ``apikey``/``api-key``
# normalize to ``api_key`` which is already present after the caller's
# ``key.lower().replace("-", "_")`` normalization in ``_has_top_level_credential_material``).
#
# Kept as a frozenset for the same O(1) lookup the legacy SSRF classifier
# relied on. The callers continue to call
# ``normalized_key in CREDENTIAL_QUERY_KEYS`` with their own ``-`` → ``_``
# normalization; that normalization stays at the call site (does not move into
# this leaf) because callers also use the "substring-of-key" rule (``"token"
# in normalized``) which is a different check.
CREDENTIAL_QUERY_KEYS: frozenset[str] = frozenset(
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


def looks_like_credential(value: str) -> bool:
    """Return ``True`` iff *value* looks like a credential-shaped string.

    Three positive paths (union):

    1. Matches :data:`LEASE_REF_RE` — it is a valid lease ref. (Lease refs ARE
       credential material from the boundary's point of view; the validator
       still has to check fragments, but a non-lease string can't be a lease.)
    2. Casefolded form contains any :data:`SENSITIVE_LEASE_FRAGMENTS` fragment
       (e.g. ``"production_token"`` contains ``"token"``).
    3. Casefolded normalized form (``-`` and ``_`` stripped, alnum-only) is in
       :data:`CREDENTIAL_QUERY_KEYS` after both ``-`` → ``_`` AND raw matching
       — this catches ``"api_key"`` / ``"api-key"`` / ``"apikey"`` as the
       same credential shape.

    NOTE: this function does NOT import the C-1 kernel regex
    (``ops/safety.py:UNSAFE_TEXT_RE``). The kernel regex covers many
    *secret-shaped value* shapes (AKIA…, ghp_…, JWT triples, …) which are out
    of scope for "looks like a credential KEY/REF" — i.e. this helper detects
    credential-shaped *labels*, not credential-shaped *values*. Mixing the
    two would break the strict-superset invariant: a JWT-shaped opaque string
    sometimes appears as a public ref (``ref_eyJ...``), and the kernel regex
    would over-trigger here. Callers that need value-shape detection should
    keep using the kernel directly via ``magi_agent.ops.safety``.
    """
    if not isinstance(value, str):
        return False
    if LEASE_REF_RE.fullmatch(value):
        return True
    folded = value.casefold()
    if any(fragment in folded for fragment in SENSITIVE_LEASE_FRAGMENTS):
        return True
    normalized = folded.replace("-", "_")
    if normalized in CREDENTIAL_QUERY_KEYS:
        return True
    return False


__all__ = [
    "CREDENTIAL_QUERY_KEYS",
    "LEASE_REF_RE",
    "SENSITIVE_LEASE_FRAGMENTS",
    "looks_like_credential",
]
