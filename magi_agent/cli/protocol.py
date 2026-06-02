"""Pydantic models for the Magi headless CLI wire protocol.

Outbound frames are emitted by the runtime to stdout (NDJSON). Inbound frames
arrive from the controlling client over stdin. Every model has a discriminating
``type`` field (and ``subtype`` where needed) so the unions
``OutboundFrame`` / ``InboundFrame`` round-trip via pydantic.

All models carry generous defaults so tests can construct them with minimal
arguments.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Literal, Union

from pydantic import BaseModel, Field


def _new_uuid() -> str:
    return str(_uuid.uuid4())


class _FrameBase(BaseModel):
    uuid: str = Field(default_factory=_new_uuid)
    session_id: str = ""


# ---------------------------------------------------------------------------
# Outbound frames (runtime -> client, NDJSON on stdout)
# ---------------------------------------------------------------------------
class SystemInit(_FrameBase):
    type: Literal["system"] = "system"
    subtype: Literal["init"] = "init"
    tools: list[str] = Field(default_factory=list)
    model: str = ""
    mcp_servers: list = Field(default_factory=list)
    cwd: str = ""


class AssistantFrame(_FrameBase):
    type: Literal["assistant"] = "assistant"
    message: dict = Field(default_factory=dict)
    parent_tool_use_id: str | None = None


class UserFrame(_FrameBase):
    type: Literal["user"] = "user"
    message: dict = Field(default_factory=dict)


class StreamEvent(_FrameBase):
    type: Literal["stream_event"] = "stream_event"
    event: dict = Field(default_factory=dict)


class SystemStatus(_FrameBase):
    type: Literal["system"] = "system"
    subtype: Literal[
        "status",
        "task_started",
        "task_progress",
        "compact_boundary",
    ] = "status"
    payload: dict = Field(default_factory=dict)


class ResultFrame(_FrameBase):
    type: Literal["result"] = "result"
    subtype: Literal[
        "success",
        "error_max_turns",
        "error_during_execution",
    ] = "success"
    result: str | None = None
    usage: dict = Field(default_factory=dict)
    total_cost_usd: float = 0.0
    is_error: bool = False
    errors: list = Field(default_factory=list)


class ControlRequestFrame(_FrameBase):
    type: Literal["control_request"] = "control_request"
    request_id: str = ""
    request: dict = Field(default_factory=dict)


OutboundFrame = Union[
    SystemInit,
    AssistantFrame,
    UserFrame,
    StreamEvent,
    SystemStatus,
    ResultFrame,
    ControlRequestFrame,
]


# ---------------------------------------------------------------------------
# Inbound frames (client -> runtime, NDJSON on stdin)
# ---------------------------------------------------------------------------
class UserInput(BaseModel):
    type: Literal["user"] = "user"
    message: dict = Field(default_factory=dict)


class ControlResponse(BaseModel):
    type: Literal["control_response"] = "control_response"
    request_id: str = ""
    response: dict = Field(default_factory=dict)


class ControlCancel(BaseModel):
    type: Literal["control_cancel_request"] = "control_cancel_request"
    request_id: str = ""


InboundFrame = Union[UserInput, ControlResponse, ControlCancel]


__all__ = [
    "SystemInit",
    "AssistantFrame",
    "UserFrame",
    "StreamEvent",
    "SystemStatus",
    "ResultFrame",
    "ControlRequestFrame",
    "OutboundFrame",
    "UserInput",
    "ControlResponse",
    "ControlCancel",
    "InboundFrame",
]
