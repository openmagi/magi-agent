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
def test_rollout_metadata_model_copy_force_falses_traffic_free_flags(flag: str) -> None:
    # C-4: ``Literal[False]`` traffic-free flags are owned by the
    # ``FalseOnlyAuthorityModel`` kernel; a True assertion via ``model_copy``
    # is force-falsed (strictly stronger than the legacy raise -- the security
    # contract "rollout stays traffic-free" is preserved without an escape
    # hatch).
    rollout = default_audit_before_block_rollout_metadata()

    snake_name = {
        "trafficAttached": "traffic_attached",
        "executionAttached": "execution_attached",
        "blockModeEnabledForLiveTraffic": "block_mode_enabled_for_live_traffic",
    }[flag]
    coerced = rollout.model_copy(update={flag: True})
    assert getattr(coerced, snake_name) is False


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
