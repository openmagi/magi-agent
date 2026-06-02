from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from magi_agent.hooks.scope import HookScope
from magi_agent.tools.manifest import ToolSource

# Single authoritative definition — import this everywhere instead of
# re-declaring the same Literal.
ExecutionType = Literal["handler", "command", "http", "llm"]

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


class HookPoint(str, Enum):
    BEFORE_TURN_START = "beforeTurnStart"
    AFTER_TURN_END = "afterTurnEnd"
    BEFORE_SYSTEM_PROMPT = "beforeSystemPrompt"
    BEFORE_MESSAGE_SEND = "beforeMessageSend"
    BEFORE_LLM_CALL = "beforeLLMCall"
    AFTER_LLM_CALL = "afterLLMCall"
    BEFORE_TOOL_USE = "beforeToolUse"
    AFTER_TOOL_USE = "afterToolUse"
    BEFORE_COMMIT = "beforeCommit"
    AFTER_COMMIT = "afterCommit"
    ON_ABORT = "onAbort"
    ON_ERROR = "onError"
    ON_TASK_CHECKPOINT = "onTaskCheckpoint"
    BEFORE_COMPACTION = "beforeCompaction"
    AFTER_COMPACTION = "afterCompaction"
    ON_RULE_VIOLATION = "onRuleViolation"
    ON_ARTIFACT_CREATED = "onArtifactCreated"


class HookManifest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    name: str
    point: HookPoint
    description: str
    source: ToolSource
    priority: int = 100
    blocking: bool = True
    fail_open: bool = Field(default=False, alias="failOpen")
    timeout_ms: int = Field(default=5_000, alias="timeoutMs", ge=100, le=60_000)
    enabled: bool = True
    security_critical: bool = Field(default=False, alias="securityCritical")
    if_condition: str | None = Field(default=None, alias="if")
    scope: HookScope = Field(default_factory=HookScope)
    opt_out: bool = Field(default=True, alias="optOut")

    # External execution fields
    execution_type: Literal["handler", "command", "http", "llm"] = Field(
        default="handler", alias="executionType"
    )
    command: str | None = None
    url: str | None = None
    http_headers: dict[str, str] | None = Field(default=None, alias="httpHeaders")
    http_method: str = Field(default="POST", alias="httpMethod")

    # LLM execution fields
    prompt_template: str | None = Field(default=None, alias="promptTemplate")
    max_prompt_tokens: int = Field(default=2000, alias="maxPromptTokens", ge=100, le=8000)

    @model_validator(mode="after")
    def _validate_execution_type_fields(self) -> HookManifest:
        if self.execution_type == "command" and self.command is None:
            raise ValueError("command hooks require 'command' to be set")
        if self.execution_type == "http" and self.url is None:
            raise ValueError("http hooks require 'url' to be set")
        if self.execution_type == "llm" and self.prompt_template is None:
            raise ValueError("llm hooks require 'prompt_template' to be set")
        return self
