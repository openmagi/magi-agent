from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from secrets import token_hex

from google.adk.tools import FunctionTool

LOCAL_FAKE_TOOL_NAMES = ("LocalEchoReceipt", "LocalStatusReceipt")
_LOCAL_FAKE_RECEIPT_TOOL_TOKEN = object()
_LOCAL_FAKE_RECEIPT_TOOL_ATTR = "_openmagi_local_fake_receipt_tool_token"
AdkLocalTool = FunctionTool


@dataclass(frozen=True)
class LocalToolCallRecord:
    tool_name: str
    receipt_id: str


@dataclass(frozen=True)
class LocalFakeToolResult:
    status: str
    output: dict[str, object] | None = None
    metadata: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def model_dump(self, *, by_alias: bool = False) -> dict[str, object]:
        result: dict[str, object] = {"status": self.status}
        if self.output is not None:
            result["output"] = self.output
        if self.metadata is not None:
            result["metadata"] = self.metadata
        if self.error_code is not None:
            result["errorCode" if by_alias else "error_code"] = self.error_code
        if self.error_message is not None:
            result["errorMessage" if by_alias else "error_message"] = self.error_message
        return result


class LocalFakeToolHost:
    def __init__(self) -> None:
        self._calls: list[LocalToolCallRecord] = []

    @property
    def calls(self) -> tuple[LocalToolCallRecord, ...]:
        return tuple(self._calls)

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        exposed_tool_names: Sequence[str] | None = None,
        mode: str = "act",
    ) -> LocalFakeToolResult:
        del mode
        exposed = _normalize_exposed_tool_names(exposed_tool_names)
        if name not in LOCAL_FAKE_TOOL_NAMES:
            return LocalFakeToolResult(
                status="error",
                error_code="tool_not_found",
                error_message="tool not found",
                metadata={
                    "toolName": name,
                    "availableTools": tuple(exposed or LOCAL_FAKE_TOOL_NAMES),
                },
            )
        if exposed is not None and name not in exposed:
            return LocalFakeToolResult(
                status="error",
                error_code="tool_not_exposed",
                error_message="tool not exposed",
                metadata={
                    "toolName": name,
                    "reason": "not exposed to this turn",
                    "availableTools": exposed,
                },
            )
        return self.record_call(name, arguments)

    def record_call(self, name: str, arguments: dict[str, object]) -> LocalFakeToolResult:
        receipt = _local_receipt(name, arguments)
        self._calls.append(
            LocalToolCallRecord(
                tool_name=name,
                receipt_id=str(receipt["receiptId"]),
            )
        )
        return LocalFakeToolResult(
            status="ok",
            output={"receipt": receipt},
            metadata={"receipt": receipt},
        )


@dataclass(frozen=True)
class LocalToolHostAdkBundle:
    host: LocalFakeToolHost
    tools: tuple[AdkLocalTool, ...]
    exposed_tool_names: tuple[str, ...]
    attach_enabled: bool = False
    local_only: bool = True
    traffic_attached: bool = False
    production_attached: bool = False
    canary_attached: bool = False
    route_attached: bool = False
    deploy_attached: bool = False
    user_visible_output_attached: bool = False
    transcript_write_attached: bool = False
    sse_write_attached: bool = False
    control_write_attached: bool = False
    db_write_attached: bool = False
    workspace_mutation_attached: bool = False


def build_local_toolhost_adk_tools(
    *,
    attach_enabled: bool = False,
    exposed_tool_names: Sequence[str] | None = None,
    mode: str = "act",
) -> LocalToolHostAdkBundle:
    del mode
    host = LocalFakeToolHost()
    exposed = _normalize_exposed_tool_names(exposed_tool_names)
    tools = ()
    if attach_enabled:
        tools = tuple(_build_local_fake_function_tool(host, name) for name in exposed or ())
    return LocalToolHostAdkBundle(
        host=host,
        tools=tools,
        exposed_tool_names=tuple(tool.name for tool in tools),
        attach_enabled=attach_enabled,
    )


def _build_local_fake_function_tool(host: LocalFakeToolHost, name: str) -> FunctionTool:
    async def invoke_local_fake_receipt(
        arguments: dict[str, object],
        tool_context: object,
    ) -> dict[str, object]:
        del tool_context
        return host.record_call(name, arguments).model_dump(by_alias=True)

    invoke_local_fake_receipt.__name__ = name
    invoke_local_fake_receipt.__doc__ = "Local-only fake ADK FunctionTool receipt."
    tool = FunctionTool(invoke_local_fake_receipt, require_confirmation=False)
    setattr(tool, _LOCAL_FAKE_RECEIPT_TOOL_ATTR, _LOCAL_FAKE_RECEIPT_TOOL_TOKEN)
    return tool


def is_local_fake_receipt_adk_tool(tool: object) -> bool:
    return (
        isinstance(tool, FunctionTool)
        and getattr(tool, _LOCAL_FAKE_RECEIPT_TOOL_ATTR, None) is _LOCAL_FAKE_RECEIPT_TOOL_TOKEN
        and tool.name in LOCAL_FAKE_TOOL_NAMES
    )


def _normalize_exposed_tool_names(
    exposed_tool_names: Sequence[str] | None,
) -> tuple[str, ...] | None:
    if exposed_tool_names is None:
        return LOCAL_FAKE_TOOL_NAMES
    exposed = tuple(name for name in dict.fromkeys(exposed_tool_names) if name in LOCAL_FAKE_TOOL_NAMES)
    return exposed


def _local_receipt(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "toolName": name,
        "localOnly": True,
        "handlerKind": "fake-local",
        "receiptId": f"local-{name}-{token_hex(8)}",
        "argumentCount": len(arguments),
        "effect": "none",
    }


__all__ = [
    "LOCAL_FAKE_TOOL_NAMES",
    "AdkLocalTool",
    "LocalFakeToolHost",
    "LocalFakeToolResult",
    "LocalToolCallRecord",
    "LocalToolHostAdkBundle",
    "build_local_toolhost_adk_tools",
    "is_local_fake_receipt_adk_tool",
]
