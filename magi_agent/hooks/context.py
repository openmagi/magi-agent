from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HookContext(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    bot_id: str = Field(alias="botId")
    user_id: str | None = Field(default=None, alias="userId")
    session_id: str | None = Field(default=None, alias="sessionId")
    turn_id: str | None = Field(default=None, alias="turnId")
    channel: str | None = None
    locale: str | None = None
    memory_mode: str | None = Field(default=None, alias="memoryMode")
    agent_model: str | None = Field(default=None, alias="agentModel")
    classifier_model: str | None = Field(default=None, alias="classifierModel")
    provider_name: str | None = Field(default=None, alias="providerName")
    plugin_id: str | None = Field(default=None, alias="pluginId")
    policy_scope: str | None = Field(default=None, alias="policyScope")
    deadline_ms: int | None = Field(default=None, alias="deadlineMs")
    # Track 16 §4 — beforeSystemPrompt hooks read the currently-assembled
    # system-prompt sections here so additive transforms (e.g. language
    # preference, project context) can return existing sections + new ones.
    # An immutable tuple so a hook cannot mutate sections in place (rule 3).
    # Default None keeps every existing construction site backward-compatible.
    prompt_sections: tuple[str, ...] | None = Field(
        default=None, alias="promptSections"
    )
