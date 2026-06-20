from __future__ import annotations

import subprocess
import sys

import pytest
from google.genai import types
from pydantic import ValidationError

from magi_agent.adk_bridge.runner_adapter import (
    OpenMagiRunnerAdapter,
    RunnerTurnInput,
)
from magi_agent.evidence.rollout import EvidenceRolloutMetadata
from magi_agent.harness.engine import HarnessEngine, HarnessResolutionRequest
from magi_agent.harness.evidence_scope import (
    EvidenceContractScope,
    SpawnDepthRange,
)
from magi_agent.harness.resolved import (
    ResolvedEvidenceContractSnapshot,
    ResolvedHarnessPresetState,
    build_default_resolved_harness_state,
)
from magi_agent.hooks.bus import HookBus
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookPoint


def _contract(
    contract_id: str,
    *,
    agent_roles: tuple[str, ...],
    run_on: tuple[str, ...] = ("main",),
    min_depth: int = 0,
    max_depth: int | None = 0,
    enforcement: str = "audit",
    opt_out_allowed: bool = True,
    hard_safety: bool = False,
) -> EvidenceContractScope:
    return EvidenceContractScope(
        contract_id=contract_id,
        agent_roles=agent_roles,
        run_on=run_on,
        spawn_depth=SpawnDepthRange(min_depth=min_depth, max_depth=max_depth),
        enforcement=enforcement,
        opt_out_allowed=opt_out_allowed,
        hard_safety=hard_safety,
    )


def _snapshot_by_id(state) -> dict[str, object]:
    return {snapshot.contract_id: snapshot for snapshot in state.evidence_contracts}


def _flatten_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for nested in value.values():
            keys.update(_flatten_keys(nested))
        return keys
    if isinstance(value, list | tuple):
        keys: set[str] = set()
        for nested in value:
            keys.update(_flatten_keys(nested))
        return keys
    return set()


def test_evidence_contracts_resolve_into_snapshot_when_scope_matches() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
            _contract(
                "path-safety-evidence",
                agent_roles=("general", "coding", "research"),
                run_on=("main", "child"),
                min_depth=0,
                max_depth=3,
                enforcement="block_final_answer",
                opt_out_allowed=False,
                hard_safety=True,
            ),
        )
    )

    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    snapshots = _snapshot_by_id(state)

    assert state.effective_evidence_contracts == ("coding-basic", "path-safety-evidence")
    assert snapshots["coding-basic"].applies is True
    assert snapshots["coding-basic"].effective_enforcement == "audit"
    assert snapshots["coding-basic"].traffic_attached is False
    assert snapshots["coding-basic"].execution_attached is False
    assert snapshots["path-safety-evidence"].hard_safety is True
    assert snapshots["path-safety-evidence"].effective_enforcement == "block_final_answer"
    assert state.evidence_verdict_readiness.status == "not_evaluated"
    assert state.evidence_verdict_readiness.pending_contract_ids == (
        "coding-basic",
        "path-safety-evidence",
    )


def test_role_separation_preserves_coding_and_research_evidence_boundaries() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
            _contract("research-sources", agent_roles=("research",), enforcement="audit"),
        )
    )

    _, state = engine.resolve(HarnessResolutionRequest(agent_role="research"))
    snapshots = _snapshot_by_id(state)

    assert state.effective_evidence_contracts == ("research-sources",)
    assert state.skipped_evidence_contracts[0].contract_id == "coding-basic"
    assert state.skipped_evidence_contracts[0].reason == "agent_role_mismatch"
    assert snapshots["coding-basic"].effective_enforcement == "off"
    assert snapshots["coding-basic"].failure_channel == "evidence_contract"
    assert snapshots["coding-basic"].research_citation_gate is False


def test_run_and_spawn_depth_filtering_are_represented_with_skip_reasons() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("main-coding", agent_roles=("coding",), run_on=("main",)),
            _contract(
                "child-coding",
                agent_roles=("coding",),
                run_on=("child",),
                min_depth=1,
                max_depth=2,
            ),
            _contract(
                "shallow-child",
                agent_roles=("coding",),
                run_on=("child",),
                min_depth=1,
                max_depth=1,
            ),
        )
    )

    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding", spawn_depth=2))

    assert state.effective_evidence_contracts == ("child-coding",)
    assert [(item.contract_id, item.reason) for item in state.skipped_evidence_contracts] == [
        ("main-coding", "run_on_mismatch"),
        ("shallow-child", "spawn_depth_mismatch"),
    ]


def test_opt_out_removes_only_allowed_non_hard_safety_evidence() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
            _contract(
                "sealed-file-evidence",
                agent_roles=("coding",),
                enforcement="block_final_answer",
                opt_out_allowed=False,
                hard_safety=True,
            ),
        )
    )

    _, state = engine.resolve(
        HarnessResolutionRequest(
            agent_role="coding",
            opted_out_evidence_contract_ids=("coding-basic", "sealed-file-evidence"),
        )
    )
    snapshots = _snapshot_by_id(state)

    assert state.effective_evidence_contracts == ("sealed-file-evidence",)
    assert state.skipped_evidence_contracts[0].contract_id == "coding-basic"
    assert state.skipped_evidence_contracts[0].reason == "opted_out"
    assert snapshots["coding-basic"].opt_out_applied is True
    assert snapshots["coding-basic"].effective_enforcement == "off"
    assert snapshots["sealed-file-evidence"].opt_out_applied is False
    assert snapshots["sealed-file-evidence"].effective_enforcement == "block_final_answer"


def test_malformed_agent_role_with_hard_safety_contract_fails_before_resolution() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract(
                "hard-safety-evidence",
                agent_roles=("general", "coding", "research"),
                enforcement="block_final_answer",
                opt_out_allowed=False,
                hard_safety=True,
            ),
        )
    )

    with pytest.raises(ValidationError, match="agentRole"):
        engine.resolve(HarnessResolutionRequest(agentRole="malformed"))


def test_default_builder_rejects_malformed_agent_role_before_evidence_scope() -> None:
    hard_safety_contract = _contract(
        "hard-safety-evidence",
        agent_roles=("general", "coding", "research"),
        enforcement="block_final_answer",
        opt_out_allowed=False,
        hard_safety=True,
    )

    with pytest.raises(ValidationError, match="agentRole"):
        build_default_resolved_harness_state(
            agent_role="malformed",
            evidence_contracts=(hard_safety_contract,),
        )


def test_resolved_state_rejects_malformed_agent_role_from_direct_payload() -> None:
    hard_safety_contract = _contract(
        "hard-safety-evidence",
        agent_roles=("general", "coding", "research"),
        enforcement="block_final_answer",
        opt_out_allowed=False,
        hard_safety=True,
    )
    valid_state = build_default_resolved_harness_state(
        agent_role="coding",
        evidence_contracts=(hard_safety_contract,),
    )
    payload = valid_state.model_dump(by_alias=True, mode="python")
    payload["agentRole"] = "malformed"

    with pytest.raises(ValidationError, match="agentRole"):
        ResolvedHarnessPresetState.model_validate(payload)


def test_default_off_evidence_contracts_are_not_effective_or_pending() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("audit-coding", agent_roles=("coding",), enforcement="audit"),
            _contract("off-coding", agent_roles=("coding",), enforcement="off"),
        )
    )

    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    snapshots = _snapshot_by_id(state)

    assert snapshots["off-coding"].applies is True
    assert snapshots["off-coding"].effective_enforcement == "off"
    assert state.effective_evidence_contracts == ("audit-coding",)
    assert state.evidence_verdict_readiness.pending_contract_ids == ("audit-coding",)


def test_rollout_mode_is_snapshotted_without_live_enforcement() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-block", agent_roles=("coding",), enforcement="block_final_answer"),
        ),
        evidence_rollout_mode="block_final_answer",
    )

    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    snapshot = state.evidence_contracts[0]
    dumped = state.model_dump(by_alias=True)

    assert snapshot.rollout.mode == "block_final_answer"
    assert snapshot.rollout.block_mode_enabled_for_live_traffic is False
    assert snapshot.enforcement_enabled is True
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


def test_hook_bus_observes_evidence_readiness_but_does_not_block_turns() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="block_final_answer"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))

    result = HookBus().run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=HookContext(
            bot_id="bot-1",
            user_id="user-1",
            session_id="session-1",
            turn_id="turn-1",
        ),
        harness_state=state,
    )

    assert result.final_action == "continue"
    assert result.observation.blocked_by == ()
    assert result.harness_state.evidence_verdict_readiness.status == "not_evaluated"
    assert result.harness_state.evidence_verdict_readiness.pending_contract_ids == ("coding-basic",)


def test_snapshot_dump_does_not_project_evidence_state_into_adk_runner_kwargs() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )

    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    dumped = state.model_dump(by_alias=True)
    keys = _flatten_keys(dumped)

    assert "runnerKwargs" not in keys
    assert "runner_kwargs" not in keys
    assert "newMessage" not in keys
    assert "stateDelta" not in keys
    assert "runConfig" not in keys
    assert "invocationId" not in keys
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


def test_runner_adapter_kwargs_do_not_include_evidence_snapshot_state() -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def run_async(self, **kwargs: object):
            self.calls.append(kwargs)
            yield {"type": "fake_adk_event"}

    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)

    async def collect() -> list[object]:
        return [
            event
            async for event in adapter.run_turn(
                RunnerTurnInput(
                    user_id="user-1",
                    session_id="agent:main:app:default",
                    turn_id="turn-1",
                    invocation_id="turn-1",
                    new_message=types.Content(
                        role="user",
                        parts=[types.Part(text="hello")],
                    ),
                    harness_state=state,
                )
            )
        ]

    import asyncio

    assert asyncio.run(collect()) == [{"type": "fake_adk_event"}]
    assert "harness_state" not in runner.calls[0]
    assert "harnessState" not in runner.calls[0]
    assert "evidence_contracts" not in runner.calls[0]
    assert "evidenceContracts" not in runner.calls[0]


@pytest.mark.parametrize("attached_flag", ("traffic_attached", "trafficAttached"))
def test_resolved_evidence_snapshot_model_copy_rejects_attached_traffic(
    attached_flag: str,
) -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))

    with pytest.raises(
        ValidationError,
        match="resolved evidence snapshots must stay traffic-free",
    ):
        state.evidence_contracts[0].model_copy(update={attached_flag: True})


@pytest.mark.parametrize("attached_flag", ("execution_attached", "executionAttached"))
def test_resolved_evidence_readiness_model_copy_rejects_attached_execution(
    attached_flag: str,
) -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))

    with pytest.raises(
        ValidationError,
        match="evidence verdict readiness metadata must stay traffic-free",
    ):
        state.evidence_verdict_readiness.model_copy(update={attached_flag: True})


def test_resolved_harness_state_model_copy_rejects_attached_runtime_fields() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))

    with pytest.raises(ValidationError, match="resolved harness state must stay traffic-free"):
        state.model_copy(update={"traffic_attached": True})

    with pytest.raises(ValidationError, match="main runs must use spawnDepth=0"):
        state.model_copy(update={"spawn_depth": 1})

    with pytest.raises(
        ValidationError,
        match="child runs must use spawnDepth greater than 0",
    ):
        state.model_copy(update={"runOn": "child"})


def test_resolved_harness_state_model_copy_revalidates_nested_snapshots() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    snapshot = state.evidence_contracts[0]
    invalid_snapshot = snapshot.model_construct(
        contract_id=snapshot.contract_id,
        applies=snapshot.applies,
        effective_enforcement=snapshot.effective_enforcement,
        enforcement_enabled=snapshot.enforcement_enabled,
        opt_out_applied=snapshot.opt_out_applied,
        hard_safety=snapshot.hard_safety,
        failure_channel=snapshot.failure_channel,
        research_citation_gate=snapshot.research_citation_gate,
        skip_reason=snapshot.skip_reason,
        rollout=snapshot.rollout,
        traffic_attached=True,
        execution_attached=snapshot.execution_attached,
    )

    with pytest.raises(
        ValidationError,
        match="resolved evidence snapshots must stay traffic-free",
    ):
        state.model_copy(update={"evidence_contracts": (invalid_snapshot,)})


def test_resolved_evidence_snapshot_rejects_constructed_invalid_rollout() -> None:
    # C-4: ``EvidenceRolloutMetadata.model_construct`` no longer provides a
    # bypass escape hatch -- it routes through ``model_validate`` via the
    # ``FalseOnlyAuthorityModel`` kernel, so a True assertion on a
    # ``Literal[False]`` field (``traffic_attached``) is coerced to False at
    # construction time. The "constructed-invalid rollout" scenario is
    # therefore unreachable through the pydantic API; the security invariant
    # ("resolved evidence snapshots stay traffic-free") is now enforced
    # fail-CLOSED at construction rather than fail-CLOSED at the downstream
    # snapshot validator (strictly stronger).
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    snapshot = state.evidence_contracts[0]
    coerced_rollout = EvidenceRolloutMetadata.model_construct(
        traffic_attached=True,
        execution_attached=snapshot.rollout.execution_attached,
        mode=snapshot.rollout.mode,
        audit_before_block=snapshot.rollout.audit_before_block,
        block_mode_enabled_for_live_traffic=snapshot.rollout.block_mode_enabled_for_live_traffic,
        scope=snapshot.rollout.scope,
    )
    assert coerced_rollout.traffic_attached is False


def test_resolved_harness_state_rejects_constructed_invalid_evidence_contract() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    snapshot = state.evidence_contracts[0]
    invalid_snapshot = snapshot.model_construct(
        contract_id=snapshot.contract_id,
        applies=snapshot.applies,
        effective_enforcement=snapshot.effective_enforcement,
        enforcement_enabled=snapshot.enforcement_enabled,
        opt_out_applied=snapshot.opt_out_applied,
        hard_safety=snapshot.hard_safety,
        failure_channel=snapshot.failure_channel,
        research_citation_gate=snapshot.research_citation_gate,
        skip_reason=snapshot.skip_reason,
        rollout=snapshot.rollout,
        traffic_attached=True,
        execution_attached=snapshot.execution_attached,
    )
    payload = state.model_dump(by_alias=True, mode="python")
    payload["evidenceContracts"] = (invalid_snapshot,)

    with pytest.raises(
        ValidationError,
        match="resolved evidence snapshots must stay traffic-free",
    ):
        ResolvedHarnessPresetState.model_validate(payload)


def test_resolved_harness_state_rejects_constructed_invalid_evidence_readiness() -> None:
    engine = HarnessEngine(
        evidence_contracts=(
            _contract("coding-basic", agent_roles=("coding",), enforcement="audit"),
        )
    )
    _, state = engine.resolve(HarnessResolutionRequest(agent_role="coding"))
    readiness = state.evidence_verdict_readiness
    invalid_readiness = readiness.model_construct(
        status=readiness.status,
        ready_contract_ids=readiness.ready_contract_ids,
        pending_contract_ids=readiness.pending_contract_ids,
        traffic_attached=True,
        execution_attached=readiness.execution_attached,
    )
    payload = state.model_dump(by_alias=True, mode="python")
    payload["evidenceVerdictReadiness"] = invalid_readiness

    with pytest.raises(
        ValidationError,
        match="evidence verdict readiness metadata must stay traffic-free",
    ):
        ResolvedHarnessPresetState.model_validate(payload)


def test_resolved_harness_import_stays_runner_route_and_dispatcher_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.resolved")
forbidden_modules = (
    "google.adk.runners",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.tools.dispatcher",
    "magi_agent.hooks.bus",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"resolved harness import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
