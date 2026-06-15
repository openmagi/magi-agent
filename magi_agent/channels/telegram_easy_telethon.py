"""Telethon adapter for the Telegram "easy setup" path.

Implements :class:`magi_agent.channels.telegram_easy.TelegramUserAuthPort` over an
MTProto user session. Imported lazily (only when
``MAGI_TELEGRAM_EASY_SETUP_ENABLED`` + ``TELEGRAM_API_ID``/``TELEGRAM_API_HASH``
are set and the ``telegram-easy`` extra is installed); ``telethon`` is imported at
module top so its absence surfaces as ImportError to the caller, which then
disables the easy path.

The user session string is reconstructed per call and never persisted to disk;
:meth:`log_out` invalidates it once a bot has been created.

NOTE: each operation uses ``telethon.sync``, which drives its own event loop and
therefore MUST be called from a worker thread (the transport wraps these in
``asyncio.to_thread``) — never directly inside the serve event loop.

This adapter has not been exercised against live Telegram in CI (it needs a real
account + app credentials); the surrounding logic is covered by faked-port tests.
"""

from __future__ import annotations

import time
from typing import Any

from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

from magi_agent.channels.telegram_easy import TelegramUserAuthPort, TwoFactorRequired

_BOTFATHER = "BotFather"
_REPLY_WAIT_SECONDS = 2.5


def build_telethon_auth_port(*, api_id: int, api_hash: str) -> TelegramUserAuthPort:
    return _TelethonAuthPort(api_id=api_id, api_hash=api_hash)


class _TelethonBotFather:
    def __init__(self, client: Any, peer: Any) -> None:
        self._client = client
        self._peer = peer

    def converse(self, text: str) -> str:
        self._client.send_message(self._peer, text)
        time.sleep(_REPLY_WAIT_SECONDS)
        messages = self._client.get_messages(self._peer, limit=1)
        if messages and getattr(messages[0], "message", None):
            return str(messages[0].message)
        return ""


class _TelethonAuthPort:
    def __init__(self, *, api_id: int, api_hash: str) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._botfather_client: Any | None = None

    def _client(self, session: str = "") -> Any:
        return TelegramClient(StringSession(session), self._api_id, self._api_hash)

    def send_code(self, phone: str) -> tuple[str, str]:
        client = self._client()
        client.connect()
        try:
            sent = client.send_code_request(phone)
            return client.session.save(), sent.phone_code_hash
        finally:
            client.disconnect()

    def sign_in(
        self, *, session: str, phone: str, code: str, phone_code_hash: str
    ) -> str:
        client = self._client(session)
        client.connect()
        try:
            try:
                client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError as exc:
                raise TwoFactorRequired() from exc
            return client.session.save()
        finally:
            client.disconnect()

    def check_password(self, *, session: str, password: str) -> str:
        client = self._client(session)
        client.connect()
        try:
            client.sign_in(password=password)
            return client.session.save()
        finally:
            client.disconnect()

    def botfather(self, session: str) -> _TelethonBotFather:
        client = self._client(session)
        client.connect()
        self._botfather_client = client
        peer = client.get_entity(_BOTFATHER)
        return _TelethonBotFather(client, peer)

    def log_out(self, session: str) -> None:
        client = self._botfather_client
        if client is None:
            client = self._client(session)
            client.connect()
        try:
            client.log_out()
        except Exception:  # noqa: BLE001 — discard session best-effort
            pass
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._botfather_client = None
