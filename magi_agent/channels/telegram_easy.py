"""Telegram "easy setup": phone number → MTProto user session → automated
@BotFather ``/newbot`` → bot token.

This module holds the *logic* (username generation, the BotFather conversation,
the ephemeral session state machine, and the orchestration). The MTProto user
session is reached only through an injected :class:`TelegramUserAuthPort`, so all
of this is unit-testable without Telethon or network. The concrete Telethon
adapter lives separately and is gated; the user-session string, login code, and
2FA password are never persisted or logged.
"""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

_TOKEN_RE = re.compile(r"(\d+:[A-Za-z0-9_-]{30,})")
_TAKEN_MARKERS = ("taken", "sorry", "invalid")
_MAX_USERNAME_LEN = 32


class TwoFactorRequired(Exception):
    """Raised by the auth port when the account has a 2FA password."""


class BotCreationFailed(Exception):
    """Raised when BotFather did not yield a token within the retry budget."""


class SessionNotFound(Exception):
    """Raised when an easy-setup session id is unknown, expired, or not ready."""


class BotFatherChannel(Protocol):
    """A messaging channel bound to a user session, talking to @BotFather."""

    def converse(self, text: str) -> str:
        """Send *text* to BotFather and return its next reply."""
        ...


class TelegramUserAuthPort(Protocol):
    """MTProto user-session operations (implemented by the Telethon adapter)."""

    def send_code(self, phone: str) -> tuple[str, str]:
        """Request a login code. Return ``(session_string, phone_code_hash)``."""
        ...

    def sign_in(
        self, *, session: str, phone: str, code: str, phone_code_hash: str
    ) -> str:
        """Sign in with the code. Return the session string; raise
        :class:`TwoFactorRequired` when a password is required."""
        ...

    def check_password(self, *, session: str, password: str) -> str:
        """Complete 2FA; return the session string."""
        ...

    def botfather(self, session: str) -> BotFatherChannel:
        """Return a BotFather channel bound to *session*."""
        ...

    def log_out(self, session: str) -> None:
        """Terminate and discard the user session."""
        ...


# ---------------------------------------------------------------------------
# username + token helpers
# ---------------------------------------------------------------------------

def normalize_bot_username(display_name: str, *, suffix: str = "") -> str:
    """Derive a valid BotFather username (ASCII, ends with ``bot``, ≤32)."""
    base = re.sub(r"[^a-z0-9]+", "_", display_name.strip().lower())
    base = re.sub(r"_+", "_", base).strip("_") or "magi"
    if suffix:
        clean_suffix = re.sub(r"[^a-z0-9]+", "", suffix.lower())
        if clean_suffix:
            base = f"{base}_{clean_suffix}"
    username = base if base.endswith("bot") else f"{base}_bot"
    if len(username) > _MAX_USERNAME_LEN:
        head = username[: _MAX_USERNAME_LEN - 4].rstrip("_")
        username = f"{head}_bot"
    return username


def extract_bot_token(text: str) -> str | None:
    """Extract a ``<id>:<secret>`` bot token from a BotFather reply, or None."""
    match = _TOKEN_RE.search(text or "")
    return match.group(1) if match else None


def default_username_suffixes(count: int = 5) -> list[str]:
    """Random short suffixes for retrying taken usernames (non-deterministic)."""
    return [secrets.token_hex(2) for _ in range(count)]


def create_bot_via_botfather(
    channel: BotFatherChannel,
    display_name: str,
    *,
    username_suffixes: Iterable[str],
) -> tuple[str, str]:
    """Drive the ``/newbot`` conversation; return ``(token, username)``.

    Tries the bare username first, then each provided suffix on conflict.
    """
    channel.converse("/newbot")
    channel.converse(display_name)
    for suffix in ["", *username_suffixes]:
        username = normalize_bot_username(display_name, suffix=suffix)
        reply = channel.converse(username)
        token = extract_bot_token(reply)
        if token:
            return token, username
        if not _looks_like_conflict(reply):
            # Unexpected reply with no token and no conflict marker — stop early.
            break
    raise BotCreationFailed("BotFather did not return a token")


def _looks_like_conflict(reply: str) -> bool:
    lowered = (reply or "").lower()
    return any(marker in lowered for marker in _TAKEN_MARKERS)


# ---------------------------------------------------------------------------
# ephemeral session store (never persisted)
# ---------------------------------------------------------------------------

@dataclass
class EasySession:
    session: str
    phone: str
    phone_code_hash: str
    step: str  # "code" | "2fa" | "authenticated"
    created_at: float


class EasySessionStore:
    """In-memory session state with a short TTL. Holds the MTProto session
    string only for the duration of setup; nothing is written to disk."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._items: dict[str, EasySession] = {}

    def create(self, *, session: str, phone: str, phone_code_hash: str, now: float) -> str:
        session_id = uuid.uuid4().hex
        self._items[session_id] = EasySession(
            session=session,
            phone=phone,
            phone_code_hash=phone_code_hash,
            step="code",
            created_at=now,
        )
        return session_id

    def get(self, session_id: str, now: float) -> EasySession | None:
        item = self._items.get(session_id)
        if item is None:
            return None
        if now - item.created_at > self._ttl:
            self._items.pop(session_id, None)
            return None
        return item

    def update(self, session_id: str, **fields: Any) -> None:
        item = self._items.get(session_id)
        if item is None:
            return
        for key, value in fields.items():
            setattr(item, key, value)

    def delete(self, session_id: str) -> None:
        self._items.pop(session_id, None)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def begin_login(
    store: EasySessionStore,
    port: TelegramUserAuthPort,
    phone: str,
    *,
    now: float,
) -> str:
    session, phone_code_hash = port.send_code(phone)
    return store.create(
        session=session, phone=phone, phone_code_hash=phone_code_hash, now=now
    )


def submit_code(
    store: EasySessionStore,
    port: TelegramUserAuthPort,
    session_id: str,
    code: str,
    *,
    now: float,
) -> bool:
    """Submit the login code. Return True when a 2FA password is still needed."""
    s = _require(store, session_id, now)
    try:
        new_session = port.sign_in(
            session=s.session, phone=s.phone, code=code, phone_code_hash=s.phone_code_hash
        )
    except TwoFactorRequired:
        store.update(session_id, step="2fa")
        return True
    store.update(session_id, session=new_session, step="authenticated")
    return False


def submit_password(
    store: EasySessionStore,
    port: TelegramUserAuthPort,
    session_id: str,
    password: str,
    *,
    now: float,
) -> None:
    s = _require(store, session_id, now)
    new_session = port.check_password(session=s.session, password=password)
    store.update(session_id, session=new_session, step="authenticated")


def finish_create_bot(
    store: EasySessionStore,
    port: TelegramUserAuthPort,
    session_id: str,
    display_name: str,
    *,
    now: float,
    persist: Callable[[str], dict[str, Any]],
    username_suffixes: Iterable[str],
) -> dict[str, Any]:
    """Automate BotFather, persist the token via *persist*, then discard the
    user session. ``persist`` is the convergence point shared with the
    advanced (token-paste) path: it validates + vaults the token."""
    s = _require(store, session_id, now)
    if s.step != "authenticated":
        raise SessionNotFound("session is not authenticated")
    channel = port.botfather(s.session)
    token, _username = create_bot_via_botfather(
        channel, display_name, username_suffixes=username_suffixes
    )
    try:
        return persist(token)
    finally:
        port.log_out(s.session)
        store.delete(session_id)


def _require(store: EasySessionStore, session_id: str, now: float) -> EasySession:
    item = store.get(session_id, now)
    if item is None:
        raise SessionNotFound("unknown or expired easy-setup session")
    return item
