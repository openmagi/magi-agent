"""Shared primitives for the gate-readiness health-metadata modules.

Each ``gate*_readiness.py`` / ``*_readiness.py`` module projects a small,
digest-bearing readiness snapshot and matches the operator-selected scope by
comparing a ``sha256:`` text digest of the bot / owner id. The digest format
regex, the text hasher, and the digest-presence check were copy-pasted verbatim
into all ten of them. This leaf is their single home so a change to the digest
scheme (the ``sha256:`` prefix or the hex length) lands once instead of drifting
across ten security-readiness surfaces.

Dependency-free (stdlib only) so any gate module may import it without a cycle.
"""

from __future__ import annotations

import hashlib
import re

DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


def sha256_text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest_present(value: object) -> bool:
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


__all__ = ["DIGEST_RE", "digest_present", "sha256_text_digest"]
