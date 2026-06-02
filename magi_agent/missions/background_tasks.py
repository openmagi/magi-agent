from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from magi_agent.runtime.long_running_activity import (
    LongRunningActivityConfig,
    LongRunningActivityPolicy,
    LongRunningActivityReceipt,
    LongRunningActivityRequest,
    LongRunningActivityResult,
    duplicate_activity_result,
    evaluate_long_running_activity,
    idempotency_conflict_result,
)
from magi_agent.runtime.receipt_utils import sha256_ref


class BackgroundTaskActivityBoundary:
    """Mission-owned background task activity planner.

    This boundary records local fake long-running activity receipts only. It
    does not invoke ADK, start background work, mutate mission state, or attach
    production scheduler/tool/channel routes.
    """

    def __init__(
        self,
        config: LongRunningActivityConfig | Mapping[str, Any] | None = None,
        *,
        idempotency_ledger: MutableMapping[str, tuple[str, LongRunningActivityReceipt]]
        | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, LongRunningActivityConfig)
            else LongRunningActivityConfig.model_validate(config or {})
        )
        self._idempotency_ledger: MutableMapping[
            str,
            tuple[str, LongRunningActivityReceipt],
        ] = idempotency_ledger if idempotency_ledger is not None else {}

    def record_activity(
        self,
        *,
        request: LongRunningActivityRequest | Mapping[str, Any],
        policy: LongRunningActivityPolicy | Mapping[str, Any] | None,
    ) -> LongRunningActivityResult:
        safe_request = LongRunningActivityRequest.model_validate(_mission_request_payload(request))
        safe_policy = (
            policy
            if isinstance(policy, LongRunningActivityPolicy)
            else LongRunningActivityPolicy.model_validate(policy)
            if policy is not None
            else None
        )
        result = evaluate_long_running_activity(
            config=self.config,
            request=safe_request,
            policy=safe_policy,
        )
        if result.status != "recorded_local_fake":
            return result

        idempotency_key = _scoped_idempotency_key(result.receipt)
        if idempotency_key is None:
            return result

        previous = self._idempotency_ledger.get(idempotency_key)
        if previous is None:
            self._idempotency_ledger[idempotency_key] = (
                result.receipt.request_digest,
                result.receipt,
            )
            return result

        previous_digest, previous_receipt = previous
        if previous_digest == result.receipt.request_digest:
            return duplicate_activity_result(receipt=previous_receipt)

        return idempotency_conflict_result(
            request=safe_request,
            policy=safe_policy,
            existing_request_digest=previous_digest,
        )


def _mission_request_payload(
    request: LongRunningActivityRequest | Mapping[str, Any],
) -> LongRunningActivityRequest | Mapping[str, Any]:
    if isinstance(request, LongRunningActivityRequest):
        return request
    payload = dict(request)
    mission_id = payload.pop("missionId", None)
    if mission_id is None:
        mission_id = payload.pop("mission_id", None)
    if "scopeRef" not in payload and "scope_ref" not in payload and mission_id is not None:
        payload["scopeRef"] = "scope:" + sha256_ref(str(mission_id)).removeprefix("sha256:")
    return payload


def _scoped_idempotency_key(receipt: LongRunningActivityReceipt) -> str | None:
    if receipt.idempotency_key_digest is None:
        return None
    return ":".join(
        (
            receipt.scope_ref,
            receipt.activity_id,
            receipt.run_id,
            receipt.idempotency_key_digest,
        )
    )


__all__ = ["BackgroundTaskActivityBoundary"]
