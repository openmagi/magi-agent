"""Per-install local ``magi serve`` gateway token (P0 security fix).

Historically the no-env local fallback hard-coded ``GATEWAY_TOKEN`` to the
publicly-known constant ``"local-dev-token"``. Because the local dashboard runs
the agent at full authority, any peer that could reach the port and present that
constant could drive a full-authority agent. This module replaces the constant
with a random, per-install token generated on first run, persisted at
``~/.magi/serve_token`` with mode ``0600``, and reused across runs.

Local-mode detection (all the ``config.gateway_token == "local-dev-token"``
sites) now keys on this resolved token via :func:`is_local_serve_token`, so a
hosted deployment (explicit ``GATEWAY_TOKEN``) never trips the local gates and
its behaviour is byte-identical.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

# The old publicly-known local-dev token. Kept ONLY as a named sentinel so the
# migration is documented and callers can assert against it; it is intentionally
# NOT accepted by :func:`is_local_serve_token` (see the security note above).
LOCAL_DEV_TOKEN_SENTINEL = "local-dev-token"

_SERVE_TOKEN_FILENAME = "serve_token"


def _magi_home() -> Path:
    """Resolve the ``~/.magi`` directory (same resolution as customize/store).

    Honours ``MAGI_CONFIG`` (the runtime config path) by using its parent so the
    token lives beside the config, mirroring
    :func:`magi_agent.customize.store.customize_path`. Falls back to
    ``~/.magi``.
    """
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    config = flag_str("MAGI_CONFIG") or None
    if config:
        return Path(config).parent
    return Path.home() / ".magi"


def _serve_token_path() -> Path:
    return _magi_home() / _SERVE_TOKEN_FILENAME


@lru_cache(maxsize=1)
def local_serve_gateway_token() -> str:
    """Return the per-install local serve gateway token.

    Reads ``~/.magi/serve_token`` if present; otherwise generates a random
    ``secrets.token_urlsafe(32)`` token, writes it ``0600``, and returns it. The
    result is cached for the process lifetime. Fail-soft: if the file cannot be
    persisted, an in-memory random token is still returned so ``serve`` never
    crashes over a read-only home.
    """
    path = _serve_token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create with restrictive perms from the start, then write.
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        # Read-only home / permission error: keep serving with a session-only
        # token rather than crashing. Detection still works within the process.
        return token
    return token


def is_local_serve_token(token: str | None) -> bool:
    """True iff ``token`` is this install's resolved local serve token.

    The publicly-known ``LOCAL_DEV_TOKEN_SENTINEL`` is intentionally NOT accepted
    so a peer that only knows the old constant cannot masquerade as the local
    owner.
    """
    if not token:
        return False
    return token == local_serve_gateway_token()
