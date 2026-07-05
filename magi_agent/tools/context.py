from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.runtime.session_identity import MemoryMode


class FrozenDict(Mapping[str, object]):
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = {key: _freeze_nested(nested) for key, nested in value.items()}

    def __getitem__(self, key: str) -> object:
        return self._value[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return _thaw_nested(self) == _thaw_nested(other)
        return False

    def __repr__(self) -> str:
        return repr(_thaw_nested(self))


def _freeze_nested(value: object) -> object:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze_nested(item) for item in value)
    return value


def _thaw_nested(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_nested(nested) for key, nested in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw_nested(item) for item in value]
    return value


class ToolContext(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    bot_id: str = Field(alias="botId")
    user_id: str | None = Field(default=None, alias="userId")
    session_id: str | None = Field(default=None, alias="sessionId")
    session_key: str | None = Field(default=None, alias="sessionKey")
    turn_id: str | None = Field(default=None, alias="turnId")
    workspace_root: str | None = Field(default=None, alias="workspaceRoot")
    workspace_ref: str | None = Field(default=None, alias="workspaceRef")
    memory_mode: MemoryMode = Field(default=MemoryMode.NORMAL, alias="memoryMode")
    channel: str | None = None
    locale: str | None = None
    current_user_message: object | None = Field(default=None, alias="currentUserMessage")
    trace_id: str | None = Field(default=None, alias="traceId")
    tool_use_id: str | None = Field(default=None, alias="toolUseId")
    abort_signal: object | None = Field(default=None, alias="abortSignal")
    deadline_ms: int | None = Field(default=None, alias="deadlineMs")
    permission_scope: object | None = Field(default=None, alias="permissionScope")
    files_read: tuple[str, ...] = Field(default_factory=tuple, alias="filesRead")
    read_ledger: object | None = Field(default=None, alias="readLedger")
    source_ledger: tuple[object, ...] = Field(default_factory=tuple, alias="sourceLedger")
    execution_contract: object | None = Field(default=None, alias="executionContract")
    emit_progress: Callable[..., object] | None = Field(default=None, alias="emitProgress")
    emit_agent_event: Callable[..., object] | None = Field(default=None, alias="emitAgentEvent")
    emit_control_event: Callable[..., object] | None = Field(default=None, alias="emitControlEvent")
    ask_user: Callable[..., object] | None = Field(default=None, alias="askUser")
    staging: object | None = None
    commit_handle: object | None = Field(default=None, alias="commitHandle")
    spawn_depth: int = Field(default=0, ge=0, alias="spawnDepth")
    parent_tool_names: tuple[str, ...] = Field(default=(), alias="parentToolNames")
    spawn_cap: tuple[str, ...] | None = Field(default=None, alias="spawnCap")
    spawn_workspace: str | None = Field(default=None, alias="spawnWorkspace")
    plugin_id: str | None = Field(default=None, alias="pluginId")
    secret_scope: str | None = Field(default=None, alias="secretScope")
    secret_broker: object | None = Field(default=None, alias="secretBroker")
    adk_tool_context: object | None = Field(default=None, alias="adkToolContext")
    adk_context: object | None = Field(default=None, alias="adkContext")
    # Wave 2 source-citation: the live SessionSourceRegistry for this session,
    # threaded by the CLI/serve tool_context_factory when citation is enabled.
    # Handlers that render their own per-source ids (research_fact) read this to
    # allocate session-global ``src_N``. A live object like ``secret_broker`` /
    # ``abort_signal``; never serialized (see ``serialize_citation_registry``).
    citation_registry: object | None = Field(default=None, alias="citationRegistry")

    @field_serializer("memory_mode")
    def serialize_memory_mode(self, value: MemoryMode) -> str:
        return value.value

    @field_serializer("citation_registry")
    def serialize_citation_registry(self, value: object) -> None:
        # A live registry object is transport-local plumbing, not serializable
        # state. Drop it from any model_dump so a serialized ToolContext stays
        # JSON-safe (mirrors the intent of the source_ledger serializer).
        return None

    @field_validator("source_ledger", mode="before")
    @classmethod
    def freeze_source_ledger(cls, value: object) -> object:
        return _freeze_nested(value)

    @field_serializer("parent_tool_names")
    def serialize_parent_tool_names(self, value: tuple[str, ...]) -> list[str]:
        return list(value)

    @field_serializer("files_read")
    def serialize_files_read(self, value: tuple[str, ...]) -> list[str]:
        return list(value)

    @field_serializer("source_ledger")
    def serialize_source_ledger(self, value: tuple[object, ...]) -> list[object]:
        thawed = _thaw_nested(value)
        if isinstance(thawed, list):
            return thawed
        return [thawed]
