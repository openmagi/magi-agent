from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from magi_agent.missions.cron_policy import (
    CronMutationPolicy,
    CronMutationRequest,
    CronSchedulerMutationConfig,
    CronSchedulerMutationReceipt,
    CronSchedulerMutationResult,
    duplicate_result,
    evaluate_cron_mutation,
    idempotency_conflict_result,
)


class CronSchedulerMutationBoundary:
    """Mission-owned cron mutation planner.

    This boundary records local fake receipts only. It does not start a scheduler
    loop, mutate cron state, write to a database, or call providers.
    """

    def __init__(
        self,
        config: CronSchedulerMutationConfig | Mapping[str, Any] | None = None,
        *,
        idempotency_ledger: MutableMapping[str, tuple[str, CronSchedulerMutationReceipt]]
        | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, CronSchedulerMutationConfig)
            else CronSchedulerMutationConfig.model_validate(config or {})
        )
        self._idempotency_ledger: MutableMapping[
            str,
            tuple[str, CronSchedulerMutationReceipt],
        ] = idempotency_ledger if idempotency_ledger is not None else {}

    def plan_mutation(
        self,
        *,
        request: CronMutationRequest | Mapping[str, Any],
        policy: CronMutationPolicy | Mapping[str, Any] | None,
    ) -> CronSchedulerMutationResult:
        safe_request = CronMutationRequest.model_validate(request)
        safe_policy = (
            policy
            if isinstance(policy, CronMutationPolicy)
            else CronMutationPolicy.model_validate(policy)
            if policy is not None
            else None
        )
        result = evaluate_cron_mutation(
            config=self.config,
            request=safe_request,
            policy=safe_policy,
        )
        if result.status != "recorded_local_fake" or result.receipt is None:
            return result

        idempotency_key = result.receipt.idempotency_key_digest
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
            return duplicate_result(receipt=previous_receipt)

        return idempotency_conflict_result(
            request=safe_request,
            policy=safe_policy,
            existing_request_digest=previous_digest,
        )


__all__ = ["CronSchedulerMutationBoundary"]
