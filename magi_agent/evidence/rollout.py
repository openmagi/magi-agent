from __future__ import annotations

from typing import Literal

from pydantic import Field, field_serializer

from magi_agent.evidence.types import EvidenceContractScopeMetadata, EvidenceMetadataModel


class EvidenceRolloutMetadata(EvidenceMetadataModel):
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    mode: Literal["audit", "block_final_answer"] = "audit"
    audit_before_block: bool = Field(default=True, alias="auditBeforeBlock")
    block_mode_enabled_for_live_traffic: Literal[False] = Field(
        default=False,
        alias="blockModeEnabledForLiveTraffic",
    )
    scope: EvidenceContractScopeMetadata | None = None

    @field_serializer("scope")
    def _serialize_scope(
        self,
        value: EvidenceContractScopeMetadata | None,
    ) -> dict[str, object] | None:
        if value is None:
            return None
        return value.model_dump(by_alias=True, mode="json")


def default_audit_before_block_rollout_metadata(
    *,
    scope: EvidenceContractScopeMetadata | None = None,
) -> EvidenceRolloutMetadata:
    return EvidenceRolloutMetadata(scope=scope)


__all__ = [
    "EvidenceRolloutMetadata",
    "default_audit_before_block_rollout_metadata",
]
