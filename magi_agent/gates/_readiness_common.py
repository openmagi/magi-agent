"""Shared primitives for the gate-readiness health-metadata modules.

Each ``gate*_readiness.py`` / ``*_readiness.py`` module projects a small,
digest-bearing readiness snapshot and matches the operator-selected scope by
comparing a ``sha256:`` text digest of the bot / owner id. The digest format
regex, the text hasher, the digest-presence check, the safe-environment set,
and the scope-matching predicate itself were copy-pasted verbatim into all ten
of them. This leaf is their single home so a change to the digest scheme (the
``sha256:`` prefix or the hex length) or the scope-matching rule lands once
instead of drifting across ten security-readiness surfaces.

The scope matcher is a live-authority security predicate: it decides whether an
operator-selected bot/owner scope matches the running request. It is typed
against ``ScopedReadinessConfig``, a structural ``Protocol`` so each readiness
module keeps its own frozen (``Literal[False]``) config class while sharing one
implementation.

Dependency-free (stdlib only, ``typing`` allowed) so any gate module may import
it without a cycle.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})


def sha256_text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest_present(value: object) -> bool:
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


class ScopedReadinessConfig(Protocol):
    """Structural contract for the operator-selected readiness scope.

    Each ``*_readiness.py`` module defines its own frozen config class; they all
    expose these fields, so the shared matcher types against this Protocol.
    """

    enabled: bool
    selected_bot_digest: str
    selected_owner_user_id_digest: str
    environment: str
    environment_allowlist: tuple[str, ...]


def selected_scope_matched(
    config: ScopedReadinessConfig, *, bot_id: str, user_id: str
) -> bool:
    if not config.enabled:
        return False
    if not digest_present(config.selected_bot_digest) or not digest_present(
        config.selected_owner_user_id_digest
    ):
        return False
    if config.selected_bot_digest != sha256_text_digest(bot_id):
        return False
    if config.selected_owner_user_id_digest != sha256_text_digest(user_id):
        return False
    if config.environment not in SAFE_ENVIRONMENTS:
        return False
    return config.environment in config.environment_allowlist


__all__ = [
    "DIGEST_RE",
    "SAFE_ENVIRONMENTS",
    "ScopedReadinessConfig",
    "digest_present",
    "selected_scope_matched",
    "sha256_text_digest",
]
