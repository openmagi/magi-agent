from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.envelopes import (
    ExecutionStartRequest,
    LeaseSnapshot,
)
from magi_agent.execution_authority.state_machine import LeaseState


DIGEST = "sha256:" + ("0" * 64)


def _lease_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "partitionId": "workspace_01",
        "leaseName": "mutation",
        "state": LeaseState.HELD,
        "ownerId": "worker_01",
        "fencingToken": 10,
        "highWaterFencingToken": 10,
        "expiresAt": datetime(2030, 1, 1, tzinfo=UTC),
        "compareVersion": 4,
    }


@pytest.mark.parametrize("invalid", [True, 1.0, "1"])
def test_snake_case_schema_version_cannot_bypass_wire_strictness(invalid: object) -> None:
    payload = _lease_payload()
    payload.pop("schemaVersion")
    payload["schema_version"] = invalid

    with pytest.raises(ValidationError, match="schemaVersion"):
        LeaseSnapshot.model_validate(payload)


@pytest.mark.parametrize("invalid", [1_735_689_600, 1_735_689_600.5, "1735689600"])
def test_snake_case_datetime_cannot_bypass_wire_strictness(invalid: object) -> None:
    payload = _lease_payload()
    payload.pop("expiresAt")
    payload["expires_at"] = invalid

    with pytest.raises(ValidationError, match="datetime"):
        LeaseSnapshot.model_validate(payload)


def _execution_start_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "actionId": "action_01",
        "attemptId": "attempt_01",
        "partitionId": "workspace_01",
        "taskContractDigest": DIGEST,
        "actionIntentDigest": DIGEST,
        "requestDigest": DIGEST,
        "authorityContractId": "authority_01",
        "authorityContractDigest": DIGEST,
        "fencingToken": 1,
        "executorId": "executor_01",
        "executorVersion": "1.0.0",
        "sandboxProfileDigest": DIGEST,
        "providerId": None,
        "providerVersion": None,
        "providerCapabilitiesDigest": None,
        "executionTokenDigest": DIGEST,
    }


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("actionId", b"action_01"),
        ("executorId", bytearray(b"executor_01")),
        ("executionTokenDigest", DIGEST.encode()),
    ],
)
def test_execution_boundary_rejects_binary_string_coercion(
    field: str,
    invalid: object,
) -> None:
    payload = _execution_start_payload()
    payload[field] = invalid

    with pytest.raises(ValidationError, match="exact JSON string"):
        ExecutionStartRequest.model_validate(payload)


def test_held_lease_requires_positive_fencing_token() -> None:
    payload = _lease_payload()
    payload["fencingToken"] = 0
    payload["highWaterFencingToken"] = 0

    with pytest.raises(ValidationError, match="positive fencing token"):
        LeaseSnapshot.model_validate(payload)
