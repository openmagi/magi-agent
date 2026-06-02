from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.evidence.rollout import (
    EvidenceRolloutMetadata,
    default_audit_before_block_rollout_metadata,
)


@pytest.mark.parametrize(
    "flag",
    ("trafficAttached", "executionAttached", "blockModeEnabledForLiveTraffic"),
)
def test_rollout_metadata_model_copy_revalidates_traffic_free_flags(flag: str) -> None:
    rollout = default_audit_before_block_rollout_metadata()

    with pytest.raises(ValidationError):
        rollout.model_copy(update={flag: True})


@pytest.mark.parametrize("scope", (object(), ("not", "json"), {"unexpected": object()}))
def test_rollout_metadata_model_copy_revalidates_scope_metadata(scope: object) -> None:
    rollout = default_audit_before_block_rollout_metadata()

    with pytest.raises(ValidationError):
        rollout.model_copy(update={"scope": scope})


def test_rollout_metadata_model_copy_rejects_unexpected_extra_fields() -> None:
    rollout = default_audit_before_block_rollout_metadata()

    with pytest.raises(ValidationError):
        rollout.model_copy(update={"runnerKwargs": {"attach": True}})


def test_rollout_metadata_direct_construction_rejects_unexpected_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EvidenceRolloutMetadata.model_validate({"runnerKwargs": {"attach": True}})
