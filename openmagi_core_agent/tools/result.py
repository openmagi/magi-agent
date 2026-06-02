from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


ToolStatus = Literal["ok", "error", "blocked", "needs_approval"]


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    status: ToolStatus
    output: object | None = None
    llm_output: object | None = Field(
        default=None,
        validation_alias=AliasChoices("llmOutput", "llm", "llm_output"),
        serialization_alias="llmOutput",
    )
    transcript_output: object | None = Field(
        default=None,
        validation_alias=AliasChoices("transcriptOutput", "transcript", "transcript_output"),
        serialization_alias="transcriptOutput",
    )
    error_code: str | None = Field(
        default=None,
        validation_alias=AliasChoices("errorCode", "error_code"),
        serialization_alias="errorCode",
    )
    error_message: str | None = Field(
        default=None,
        validation_alias=AliasChoices("errorMessage", "error", "error_message"),
        serialization_alias="errorMessage",
    )
    duration_ms: int | None = Field(
        default=None,
        validation_alias=AliasChoices("durationMs", "duration_ms"),
        serialization_alias="durationMs",
    )
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    file_refs: tuple[str, ...] = Field(default=(), alias="fileRefs")
    delivery_receipts: tuple[str, ...] = Field(default=(), alias="deliveryReceipts")
    retryable: bool = False
    metadata: dict[str, object] = Field(default_factory=dict)
    coding_mutation_receipt: object | None = Field(
        default=None,
        validation_alias=AliasChoices("codingMutationReceipt", "coding_mutation_receipt"),
        serialization_alias="codingMutationReceipt",
    )

    @property
    def llm(self) -> object | None:
        return self.llm_output

    @property
    def transcript(self) -> object | None:
        return self.transcript_output

    @property
    def error(self) -> str | None:
        return self.error_message
