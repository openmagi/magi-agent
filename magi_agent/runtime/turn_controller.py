from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.harness.resolved import ResolvedHarnessPresetState


class TurnControllerInput(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    user_id: str = Field(alias="userId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    message_text: str = Field(alias="messageText")
    harness_state: ResolvedHarnessPresetState = Field(alias="harnessState")
