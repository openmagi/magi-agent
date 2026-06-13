from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MemoryMode(str, Enum):
    NORMAL = "normal"
    READ_ONLY = "read_only"
    INCOGNITO = "incognito"


class ChannelRef(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: str
    channel_id: str = Field(alias="channelId")


class SessionIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    session_key: str = Field(alias="sessionKey")
    effective_session_key: str = Field(alias="effectiveSessionKey")
    channel: ChannelRef
    memory_mode: MemoryMode = Field(alias="memoryMode")


def parse_session_identity(
    headers: Mapping[str, object],
    *,
    bot_id: str,
    reset_counter: int = 0,
) -> SessionIdentity:
    normalized = {_normalize_header_name(k): v for k, v in headers.items()}
    session_key = _session_key_from_normalized_headers(normalized) or (
        f"agent:main:app:default:{bot_id[:8]}"
    )
    channel = _channel_from_session_key(session_key)
    effective_session_key = (
        session_key if reset_counter <= 0 else f"{session_key}:{int(reset_counter)}"
    )
    return SessionIdentity(
        session_key=session_key,
        effective_session_key=effective_session_key,
        channel=channel,
        memory_mode=_memory_mode_from_header(
            _first_header(normalized, "x-core-agent-memory-mode")
        ),
    )


def session_key_from_headers(headers: Mapping[str, object]) -> str | None:
    normalized = {_normalize_header_name(k): v for k, v in headers.items()}
    return _session_key_from_normalized_headers(normalized)


def _normalize_header_name(value: str) -> str:
    return value.strip().lower()


def _session_key_from_normalized_headers(headers: Mapping[str, object]) -> str | None:
    return _first_header(headers, "x-core-agent-session-key") or _first_header(
        headers,
        "x-openclaw-session-key",
    )


def _first_header(headers: Mapping[str, object], name: str) -> str | None:
    raw = headers.get(name)
    if isinstance(raw, list | tuple):
        raw = raw[0] if raw else None
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value or None


def _memory_mode_from_header(raw: str | None) -> MemoryMode:
    if raw == MemoryMode.READ_ONLY.value:
        return MemoryMode.READ_ONLY
    if raw == MemoryMode.INCOGNITO.value:
        return MemoryMode.INCOGNITO
    return MemoryMode.NORMAL


def _channel_from_session_key(session_key: str) -> ChannelRef:
    parts = session_key.split(":")
    if len(parts) >= 4 and parts[0] == "agent":
        return ChannelRef(type=parts[2] or "app", channel_id=parts[3] or "default")
    return ChannelRef(type="app", channel_id="default")
