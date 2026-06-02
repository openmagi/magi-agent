from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from openmagi_core_agent.runtime.context_packet import ContextAttachment
from openmagi_core_agent.runtime.message_builder import (
    _field,
    _format_attachment_line,
    _public_sanitize_attachment_display,
)


_PRIVATE_VALUE_RE = re.compile(
    r"(?:"
    r"/workspace/private(?:/|\b)|"
    r"/private(?:/|\b)|"
    r"REDACT_ME_[A-Z0-9_]+|"
    r"api\.telegram\.org|"
    r"\bbot\d+:[A-Za-z0-9_-]{8,}\b|"
    r"\bbearer\b|"
    r"\bcookie\b|"
    r"\btoken\b|"
    r"\bapi[_-]?key\b|"
    r"\bsecret\b"
    r")",
    re.IGNORECASE,
)


def build_current_turn_context_attachments(
    *,
    channel: Mapping[str, object] | object | None = None,
    user_message: Mapping[str, object] | object | None = None,
    workspace_root: str | None = None,
    memory_refs: Sequence[str] | None = None,
    route_metadata: Mapping[str, object] | object | None = None,
) -> tuple[ContextAttachment, ...]:
    attachments: list[ContextAttachment] = []

    channel_attachment = _channel_attachment(channel)
    if channel_attachment is not None:
        attachments.append(channel_attachment)

    for attachment in _safe_user_attachments(user_message, workspace_root):
        attachments.append(attachment)

    system_note = _safe_system_note(user_message)
    if system_note:
        attachments.append(
            ContextAttachment(
                kind="runtime_note",
                label="system_prompt_addendum",
                text=system_note,
            )
        )

    for ref in memory_refs or ():
        safe_ref = _safe_text(ref)
        if safe_ref:
            attachments.append(
                ContextAttachment(kind="memory_ref", label="memory_ref", text=safe_ref)
            )

    route_label = _route_label(route_metadata)
    if route_label:
        attachments.append(
            ContextAttachment(kind="route", label="route_metadata", text=route_label)
        )

    return tuple(attachments)


def _channel_attachment(
    channel: Mapping[str, object] | object | None,
) -> ContextAttachment | None:
    if channel is None:
        return None
    channel_type = _safe_text(_field(channel, "type", default=""))
    channel_id = _safe_text(_field(channel, "channelId", "channel_id", default=""))
    memory_mode = _safe_text(_field(channel, "memoryMode", "memory_mode", default=""))
    if not channel_type and not memory_mode:
        return None
    label = "/".join(part for part in (channel_type, channel_id) if part)
    text = f"memory_mode={memory_mode}" if memory_mode else ""
    return ContextAttachment(kind="channel", label=label or "channel", text=text)


def _safe_user_attachments(
    user_message: Mapping[str, object] | object | None,
    workspace_root: str | None,
) -> tuple[ContextAttachment, ...]:
    raw_attachments = _field(user_message, "attachments", default=None)
    if not isinstance(raw_attachments, list | tuple):
        return ()
    attachments: list[ContextAttachment] = []
    for item in raw_attachments:
        line = _format_attachment_line(item, workspace_root)
        safe_line = _safe_text(line)
        if not safe_line:
            continue
        attachments.append(
            ContextAttachment(
                kind="attachment",
                label="user_attachment",
                text=safe_line.removeprefix("- ").strip(),
            )
        )
    return tuple(attachments)


def _safe_system_note(user_message: Mapping[str, object] | object | None) -> str:
    metadata = _field(user_message, "metadata", default=None)
    value = _field(metadata, "systemPromptAddendum", "system_prompt_addendum", default="")
    return _safe_text(value)


def _route_label(route_metadata: Mapping[str, object] | object | None) -> str:
    provider = _safe_text(_field(route_metadata, "providerLabel", "provider_label", default=""))
    model = _safe_text(_field(route_metadata, "modelLabel", "model_label", default=""))
    return " ".join(part for part in (provider, model) if part)


def _safe_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    sanitized = _public_sanitize_attachment_display(value)
    if not sanitized or _PRIVATE_VALUE_RE.search(sanitized):
        return ""
    return sanitized


__all__ = ["build_current_turn_context_attachments"]
