"""Memory subsystem golden-oracle scenario drivers (Pack C4 oracle).

Three drivers over the REAL memory harnesses, captured BEFORE any C4
strategy decomposition:

``compaction_matrix`` — the full ``MemoryCompactionHarness.compact`` decision
table: the disabled gate, every ``_compaction_denial_reasons`` branch (the C4
policy being decomposed), the kernel local-fake adapter gate (both the
harness-config and the policy side), and the success receipt. Compaction
receipts are pure digests — no wall clock anywhere (verified) — so the trace
records the receipt envelope scalars too.

``recall_gate`` — ``MemoryRecallHarness.recall`` gating + policy-passthrough
rows (disabled with/without policies, missing namespace/projection policy,
allowed local-fake). Request/policy/adapter shapes copied from
tests/test_memory_recall_recipe_harness.py — the None-policy rows freeze the
exact passthrough behavior the C4 default-policy seam must preserve.

``review_trigger`` — the pure ``should_run_review`` N-turn trigger table plus
``MemoryReviewHarness.review`` envelope rows (config-off, env-gate-off,
reviewed mixed batch). Reviewer fixtures follow tests/test_memory_review.py;
the write host is an in-memory fake (no disk, no env write gates) so the
trace is machine-independent.

Env hermeticity: ``MAGI_MEMORY_REVIEW_ENABLED`` is pinned per row and restored
(mirrors tests/fixtures/goal_loop_golden/scenarios.py).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from magi_agent.harness.memory_compaction import (
    MemoryCompactionHarness,
    MemoryCompactionPolicy,
    MemoryCompactionRequest,
)
from magi_agent.harness.memory_review import (
    MAGI_MEMORY_REVIEW_ENABLED_ENV,
    MemoryReviewConfig,
    MemoryReviewHarness,
    should_run_review,
)
from magi_agent.memory.contracts import MemoryRecord, RecallRequest, RecallResult


# ---------------------------------------------------------------------------
# compaction_matrix
# ---------------------------------------------------------------------------


def _request(**overrides: Any) -> MemoryCompactionRequest:
    base: dict[str, Any] = {
        "providerId": "hipocampus-local",
        "turnId": "turn-1",
        "sourceRefs": ("evidence:src-1",),
        "evidenceRefs": ("evidence:compaction-1",),
    }
    base.update(overrides)
    return MemoryCompactionRequest.model_validate(base)


def _policy(**overrides: Any) -> MemoryCompactionPolicy:
    base: dict[str, Any] = {
        "policyRef": "policy:memory-compaction",
        "policySnapshotRef": "policy:memory-compaction@snapshot",
        "localFakeCompactionAllowed": True,
    }
    base.update(overrides)
    return MemoryCompactionPolicy.model_validate(base)


def _compact(
    harness: MemoryCompactionHarness,
    request: MemoryCompactionRequest,
    policy: MemoryCompactionPolicy | None,
) -> dict[str, Any]:
    result = asyncio.run(harness.compact(request=request, policy=policy))
    return {
        "status": result.status,
        "reasonCodes": list(result.reason_codes),
        "executed": result.receipt.executed,
        "localTestOnly": result.receipt.local_test_only,
        "receiptId": result.receipt.receipt_id,
        "outputDigest": result.receipt.output_digest,
        "policySnapshotDigest": result.receipt.policy_snapshot_digest,
        "redactionStatus": result.receipt.redaction_status,
    }


def run_compaction_denial_matrix() -> list[dict[str, Any]]:
    on = MemoryCompactionHarness({"enabled": True, "localFakeAdapterEnabled": True})
    no_adapter = MemoryCompactionHarness({"enabled": True})
    off = MemoryCompactionHarness({})
    rows: list[tuple[str, MemoryCompactionHarness, Any, Any]] = [
        # mechanism: master enable gate (kernel)
        ("disabled_harness", off, _request(), _policy()),
        # the 8 denial-strategy branches (the C4 policy being decomposed)
        ("missing_policy", on, _request(), None),
        ("missing_evidence", on, _request(evidenceRefs=()), _policy()),
        ("approval_required", on, _request(), _policy(approvalRequired=True)),
        ("missing_sources", on, _request(sourceRefs=()), _policy()),
        ("redaction_unverified", on, _request(), _policy(redactionStatus="unverified")),
        ("retention_expired", on, _request(), _policy(retentionState="expired")),
        ("erase_tombstoned", on, _request(), _policy(eraseState="tombstoned")),
        ("private_payload", on, _request(privatePayload=True), _policy()),
        ("child_isolated", on, _request(childMemoryIsolated=True), _policy()),
        # mechanism: local-fake adapter gate, both sides (kernel)
        ("adapter_gate_off", no_adapter, _request(), _policy()),
        (
            "policy_fake_not_allowed",
            on,
            _request(),
            _policy(localFakeCompactionAllowed=False),
        ),
        # the only success completion
        ("success_local_fake", on, _request(), _policy()),
    ]
    return [
        {"scenario": name, **_compact(harness, request, policy)}
        for name, harness, request, policy in rows
    ]


# ---------------------------------------------------------------------------
# recall_gate
# ---------------------------------------------------------------------------

_RECALL_NAMESPACE = "memory-ns:tenant-a.bot-a"


class _FakeRecallAdapter:
    """Local-fake recall provider (copied from
    tests/test_memory_recall_recipe_harness.py::FakeMemoryRecallAdapter)."""

    openmagi_local_fake_provider = True

    def __init__(self, *records: MemoryRecord) -> None:
        self.records = records
        self.calls = 0

    async def recall(self, request: RecallRequest, *, policy: object) -> RecallResult:
        self.calls += 1
        assert policy is not None
        return RecallResult(
            providerId="local-fake-memory",
            records=self.records,
            recallAllowed=True,
            writeAllowed=False,
            promptProjectionAllowed=False,
            publicProjectionAllowed=True,
            reasonCodes=("local_fake_memory_fixture",),
        )


def _recall_request() -> RecallRequest:
    return RecallRequest(
        scope={"tenantId": "tenant-a", "botId": "bot-a", "sessionKey": "session-a"},
        query="How should we continue the launch plan?",
        purpose="answer_user",
    )


def _recall_record() -> MemoryRecord:
    return MemoryRecord(
        id="allowed",
        scope="bot",
        kind="note",
        body="Launch plan: keep memory recall read-only and require receipts.",
        sourceRef="memory:fixture.allowed",
        providerId="local-fake-memory",
        confidence="observed",
        visibility="public-safe",
        score=0.95,
        customMetadata={"namespaceRef": _RECALL_NAMESPACE},
    )


def _namespace_policy() -> object:
    from magi_agent.memory.namespaces import MemoryNamespacePolicy

    return MemoryNamespacePolicy(namespaceRef=_RECALL_NAMESPACE)


def _projection_policy() -> object:
    from magi_agent.recipes.first_party.memory_recall import (
        MemoryRecallProjectionPolicy,
    )

    return MemoryRecallProjectionPolicy(
        latestUserText="continue the launch plan",
        maxBytes=2048,
        policySnapshotRef="policy-snapshot:memory-golden",
    )


def run_recall_gate_matrix() -> list[dict[str, Any]]:
    from magi_agent.harness.memory_recall import (
        MemoryRecallHarness,
        MemoryRecallHarnessConfig,
    )

    def _row(
        name: str,
        harness_config: dict[str, Any],
        *,
        namespace: bool,
        projection: bool,
    ) -> dict[str, Any]:
        adapter = _FakeRecallAdapter(_recall_record())
        harness = MemoryRecallHarness(
            MemoryRecallHarnessConfig.model_validate(harness_config), adapter=adapter
        )
        result = asyncio.run(
            harness.recall(
                request=_recall_request(),
                namespace_policy=_namespace_policy() if namespace else None,
                projection_policy=_projection_policy() if projection else None,
            )
        )
        return {
            "scenario": name,
            "status": result.status,
            "reasonCodes": list(result.reason_codes),
            "adapterCalls": adapter.calls,
            "decisionCounts": dict(result.receipt.decision_counts),
            "references": len(result.projection.references),
        }

    enabled = {"enabled": True, "localFakeAdapterEnabled": True}
    return [
        _row("disabled_with_policies", {}, namespace=True, projection=True),
        _row("disabled_missing_policies", {}, namespace=False, projection=False),
        _row("missing_namespace", enabled, namespace=False, projection=True),
        _row("missing_projection", enabled, namespace=True, projection=False),
        _row("allowed_local_fake", enabled, namespace=True, projection=True),
    ]


# ---------------------------------------------------------------------------
# review_trigger
# ---------------------------------------------------------------------------


class _FakeWriteHost:
    """Deterministic stand-in for the PR2 MemoryWriteToolHost ``_handle``
    boundary: every routed fact reports a simulated (non-real) write."""

    class _Result:
        status = "ok"
        metadata = {"reasonCodes": ("memory_write_simulated",)}
        output = {"realWrite": False}

    async def _handle(self, arguments: dict, context: object) -> object:
        _ = arguments, context
        return self._Result()


def _pin_review_env(value: str | None) -> str | None:
    previous = os.environ.get(MAGI_MEMORY_REVIEW_ENABLED_ENV)
    if value is None:
        os.environ.pop(MAGI_MEMORY_REVIEW_ENABLED_ENV, None)
    else:
        os.environ[MAGI_MEMORY_REVIEW_ENABLED_ENV] = value
    return previous


def _review_row(
    name: str,
    *,
    enabled: bool,
    env: str | None,
    facts: list[str],
) -> dict[str, Any]:
    previous = _pin_review_env(env)
    try:
        harness = MemoryReviewHarness(MemoryReviewConfig(enabled=enabled))
        receipt = asyncio.run(
            harness.review(
                [{"role": "user", "content": "please answer in Korean"}],
                reviewer=lambda transcript: list(facts),
                write_host=_FakeWriteHost(),
            )
        )
    finally:
        _pin_review_env(previous)
    return {
        "scenario": name,
        "kind": "review",
        "status": receipt.status,
        "reasonCodes": list(receipt.reason_codes),
        "candidates": receipt.candidates,
        "droppedDeclarative": receipt.dropped_declarative,
        "attemptedWrites": receipt.attempted_writes,
        "written": receipt.written,
        "simulated": receipt.simulated,
        "blocked": receipt.blocked,
        "writeReceipts": [
            {
                "factPreview": entry.fact_preview,
                "status": entry.status,
                "realWrite": entry.real_write,
                "reasonCodes": list(entry.reason_codes),
            }
            for entry in receipt.write_receipts
        ],
    }


def run_review_trigger_table() -> list[dict[str, Any]]:
    trigger_rows = [
        ("disabled", 10, 10, False),
        ("zero_turns", 0, 10, True),
        ("on_boundary", 10, 10, True),
        ("off_boundary", 11, 10, True),
        ("interval_one_every_turn", 3, 1, True),
        ("double_boundary", 20, 10, True),
    ]
    trace: list[dict[str, Any]] = [
        {
            "scenario": name,
            "kind": "trigger",
            "fires": should_run_review(turns, interval_turns=interval, enabled=enabled),
        }
        for name, turns, interval, enabled in trigger_rows
    ]
    # Envelope rows: fact fixtures copied from tests/test_memory_review.py —
    # the preference fact passes the declarative filter, the PR fact is dropped.
    trace.append(
        _review_row(
            "config_disabled",
            enabled=False,
            env="1",
            facts=["user prefers Korean responses"],
        )
    )
    trace.append(
        _review_row(
            "env_gate_disabled",
            enabled=True,
            env=None,
            facts=["user prefers Korean responses"],
        )
    )
    trace.append(
        _review_row(
            "reviewed_mixed_batch",
            enabled=True,
            env="1",
            facts=["user prefers Korean responses", "merged PR #123"],
        )
    )
    return trace
