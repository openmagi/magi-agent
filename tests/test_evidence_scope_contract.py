from __future__ import annotations

import subprocess
import sys
from types import ModuleType

import pytest
from pydantic import ValidationError


def _evidence_scope_module() -> ModuleType:
    try:
        from openmagi_core_agent.harness import evidence_scope
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing evidence scope scaffold module: {exc}")
    return evidence_scope


def test_policy_defaults_are_default_off_audit_before_block_and_traffic_free() -> None:
    module = _evidence_scope_module()

    defaults = module.third_party_evidence_policy_defaults()

    assert defaults.enforcement_default == "off"
    assert defaults.audit_before_block is True
    assert defaults.traffic_attached is False
    assert defaults.execution_attached is False

    dumped = defaults.model_dump(by_alias=True)
    assert dumped["enforcementDefault"] == "off"
    assert dumped["auditBeforeBlock"] is True
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


@pytest.mark.parametrize("attached_flag", ("trafficAttached", "executionAttached"))
def test_policy_defaults_reject_attached_flags(attached_flag: str) -> None:
    module = _evidence_scope_module()
    payload: dict[str, object] = {
        "enforcementDefault": "off",
        "auditBeforeBlock": True,
        attached_flag: True,
    }

    with pytest.raises(ValidationError):
        module.third_party_evidence_policy_defaults().__class__.model_validate(payload)


def test_contract_scope_accepts_snake_and_camel_case_and_dumps_camel_case() -> None:
    module = _evidence_scope_module()

    camel = module.EvidenceContractScope.model_validate(
        {
            "contractId": "coding-basic",
            "agentRoles": ["coding"],
            "runOn": ["main"],
            "spawnDepth": {"minDepth": 0, "maxDepth": 0},
            "enforcement": "audit",
            "optOutAllowed": True,
            "auditBeforeBlock": True,
        }
    )
    snake = module.EvidenceContractScope(
        contract_id="research-sources",
        agent_roles=("research",),
        run_on=("main", "child"),
        spawn_depth=module.SpawnDepthRange(min_depth=0, max_depth=2),
        enforcement="audit",
        opt_out_allowed=True,
    )

    assert camel.contract_id == "coding-basic"
    assert camel.agent_roles == ("coding",)
    assert snake.run_on == ("main", "child")

    dumped = snake.model_dump(by_alias=True)
    assert dumped["contractId"] == "research-sources"
    assert dumped["agentRoles"] == ("research",)
    assert dumped["runOn"] == ("main", "child")
    assert dumped["spawnDepth"]["minDepth"] == 0
    assert dumped["spawnDepth"]["maxDepth"] == 2
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


@pytest.mark.parametrize("extra_field", ("runnerAttached", "route"))
def test_evidence_scope_models_reject_unexpected_runtime_fields(extra_field: str) -> None:
    module = _evidence_scope_module()

    contract_payload: dict[str, object] = {
        "contractId": "coding-basic",
        "agentRoles": ["coding"],
        "runOn": ["main"],
        "spawnDepth": {"minDepth": 0, "maxDepth": 0},
        "enforcement": "audit",
        extra_field: False,
    }
    decision_payload: dict[str, object] = {
        "contractId": "coding-basic",
        "applies": True,
        "effectiveEnforcement": "audit",
        "enforcementEnabled": True,
        "optOutApplied": False,
        "hardSafety": False,
        extra_field: False,
    }

    with pytest.raises(ValidationError):
        module.EvidenceContractScope.model_validate(contract_payload)

    with pytest.raises(ValidationError):
        module.EvidenceScopeDecision.model_validate(decision_payload)


def test_opt_out_disables_non_hard_safety_enforcement_but_not_hard_safety_metadata() -> None:
    module = _evidence_scope_module()

    coding_contract = module.EvidenceContractScope(
        contract_id="coding-basic",
        agent_roles=("coding",),
        run_on=("main",),
        spawn_depth=module.SpawnDepthRange(min_depth=0, max_depth=0),
        enforcement="audit",
        opt_out_allowed=True,
    )
    hard_safety_contract = module.EvidenceContractScope(
        contract_id="path-safety-evidence",
        agent_roles=("coding", "research", "general"),
        run_on=("main", "child"),
        spawn_depth=module.SpawnDepthRange(min_depth=0, max_depth=3),
        enforcement="block_final_answer",
        hard_safety=True,
        opt_out_allowed=False,
    )
    context = module.EvidenceScopeContext(agent_role="coding", run_on="main", spawn_depth=0)

    opted_out = module.resolve_evidence_scope(
        coding_contract,
        context,
        opted_out_contract_ids=("coding-basic",),
    )
    hard_safety = module.resolve_evidence_scope(
        hard_safety_contract,
        context,
        opted_out_contract_ids=("path-safety-evidence",),
    )

    assert opted_out.applies is True
    assert opted_out.opt_out_applied is True
    assert opted_out.effective_enforcement == "off"
    assert opted_out.enforcement_enabled is False
    assert opted_out.hard_safety is False

    assert hard_safety.applies is True
    assert hard_safety.opt_out_applied is False
    assert hard_safety.effective_enforcement == "block_final_answer"
    assert hard_safety.enforcement_enabled is True
    assert hard_safety.hard_safety is True
    assert hard_safety.traffic_attached is False
    assert hard_safety.execution_attached is False


@pytest.mark.parametrize(
    "payload",
    (
        {
            "contractId": " ",
            "applies": True,
            "effectiveEnforcement": "audit",
            "enforcementEnabled": True,
            "optOutApplied": False,
            "hardSafety": False,
        },
        {
            "contractId": "coding-basic",
            "applies": True,
            "effectiveEnforcement": "off",
            "enforcementEnabled": True,
            "optOutApplied": False,
            "hardSafety": False,
        },
        {
            "contractId": "coding-basic",
            "applies": True,
            "effectiveEnforcement": "audit",
            "enforcementEnabled": False,
            "optOutApplied": False,
            "hardSafety": False,
        },
        {
            "contractId": "coding-basic",
            "applies": False,
            "effectiveEnforcement": "off",
            "enforcementEnabled": False,
            "optOutApplied": True,
            "hardSafety": False,
        },
        {
            "contractId": "coding-basic",
            "applies": True,
            "effectiveEnforcement": "audit",
            "enforcementEnabled": True,
            "optOutApplied": True,
            "hardSafety": False,
        },
        {
            "contractId": "coding-basic",
            "applies": False,
            "effectiveEnforcement": "audit",
            "enforcementEnabled": True,
            "optOutApplied": False,
            "hardSafety": False,
        },
        {
            "contractId": "path-safety-evidence",
            "applies": True,
            "effectiveEnforcement": "off",
            "enforcementEnabled": False,
            "optOutApplied": True,
            "hardSafety": True,
        },
    ),
)
def test_evidence_scope_decision_rejects_inconsistent_effective_enforcement(
    payload: dict[str, object],
) -> None:
    module = _evidence_scope_module()

    with pytest.raises(ValidationError):
        module.EvidenceScopeDecision.model_validate(payload)


def test_hard_safety_contracts_cannot_be_declared_opt_out_allowed() -> None:
    module = _evidence_scope_module()

    with pytest.raises(ValidationError):
        module.EvidenceContractScope(
            contract_id="unsafe-hard-safety",
            agent_roles=("coding",),
            run_on=("main",),
            spawn_depth=module.SpawnDepthRange(min_depth=0, max_depth=0),
            enforcement="audit",
            hard_safety=True,
            opt_out_allowed=True,
        )


def test_role_scope_separates_coding_evidence_from_research_citation_gate() -> None:
    module = _evidence_scope_module()

    coding_contract = module.EvidenceContractScope(
        contract_id="coding-basic",
        agent_roles=("coding",),
        run_on=("main",),
        spawn_depth=module.SpawnDepthRange(min_depth=0, max_depth=0),
        enforcement="audit",
    )

    coding_decision = module.resolve_evidence_scope(
        coding_contract,
        module.EvidenceScopeContext(agent_role="coding", run_on="main", spawn_depth=0),
    )
    research_decision = module.resolve_evidence_scope(
        coding_contract,
        module.EvidenceScopeContext(agent_role="research", run_on="main", spawn_depth=0),
    )

    assert coding_decision.applies is True
    assert coding_decision.failure_channel == "evidence_contract"
    assert coding_decision.research_citation_gate is False
    assert research_decision.applies is False
    assert research_decision.effective_enforcement == "off"


def test_run_and_spawn_depth_scope_are_explicit() -> None:
    module = _evidence_scope_module()

    main_only = module.EvidenceContractScope(
        contract_id="main-coding",
        agent_roles=("coding",),
        run_on=("main",),
        spawn_depth=module.SpawnDepthRange(min_depth=0, max_depth=0),
        enforcement="audit",
    )
    child_depths = module.EvidenceContractScope(
        contract_id="child-coding",
        agent_roles=("coding",),
        run_on=("child",),
        spawn_depth=module.SpawnDepthRange(min_depth=1, max_depth=2),
        enforcement="audit",
    )

    main_context = module.EvidenceScopeContext(agent_role="coding", run_on="main", spawn_depth=0)
    child_depth_1 = module.EvidenceScopeContext(agent_role="coding", run_on="child", spawn_depth=1)
    child_depth_3 = module.EvidenceScopeContext(agent_role="coding", run_on="child", spawn_depth=3)

    assert module.resolve_evidence_scope(main_only, main_context).applies is True
    assert module.resolve_evidence_scope(main_only, child_depth_1).applies is False
    assert module.resolve_evidence_scope(child_depths, child_depth_1).applies is True
    assert module.resolve_evidence_scope(child_depths, child_depth_3).applies is False


@pytest.mark.parametrize(
    "range_payload",
    (
        {"minDepth": -1, "maxDepth": 0},
        {"minDepth": 2, "maxDepth": 1},
    ),
)
def test_spawn_depth_range_rejects_invalid_ranges(range_payload: dict[str, int]) -> None:
    module = _evidence_scope_module()

    with pytest.raises(ValidationError):
        module.SpawnDepthRange.model_validate(range_payload)


@pytest.mark.parametrize(
    "context_payload",
    (
        {"agentRole": "coding", "runOn": "main", "spawnDepth": -1},
        {"agentRole": "coding", "runOn": "main", "spawnDepth": 1},
        {"agentRole": "coding", "runOn": "child", "spawnDepth": 0},
    ),
)
def test_scope_context_rejects_invalid_depths(context_payload: dict[str, object]) -> None:
    module = _evidence_scope_module()

    with pytest.raises(ValidationError):
        module.EvidenceScopeContext.model_validate(context_payload)


def test_resolve_evidence_scope_revalidates_copied_invalid_context() -> None:
    module = _evidence_scope_module()
    contract = module.EvidenceContractScope(
        contract_id="main-coding",
        agent_roles=("coding",),
        run_on=("main",),
        spawn_depth=module.SpawnDepthRange(min_depth=0, max_depth=0),
        enforcement="audit",
    )
    context = module.EvidenceScopeContext(agent_role="coding", run_on="main", spawn_depth=0)
    copied_invalid_context = context.model_copy(update={"spawn_depth": 1})

    with pytest.raises(ValidationError, match="main runs must use spawnDepth=0"):
        module.resolve_evidence_scope(contract, copied_invalid_context)


def test_resolve_evidence_scope_revalidates_copied_invalid_contract_range() -> None:
    module = _evidence_scope_module()
    valid_range = module.SpawnDepthRange(min_depth=0, max_depth=1)
    copied_invalid_range = valid_range.model_copy(update={"min_depth": 2})
    contract = module.EvidenceContractScope(
        contract_id="main-coding",
        agent_roles=("coding",),
        run_on=("main",),
        spawn_depth=module.SpawnDepthRange(min_depth=0, max_depth=1),
        enforcement="audit",
    )
    copied_invalid_contract = contract.model_copy(
        update={"spawn_depth": copied_invalid_range}
    )
    context = module.EvidenceScopeContext(agent_role="coding", run_on="main", spawn_depth=0)

    with pytest.raises(ValidationError, match="maxDepth must be greater than or equal to minDepth"):
        module.resolve_evidence_scope(copied_invalid_contract, context)


@pytest.mark.parametrize("attached_flag", ("trafficAttached", "executionAttached"))
def test_direct_contract_construction_rejects_attached_flags(attached_flag: str) -> None:
    module = _evidence_scope_module()
    payload: dict[str, object] = {
        "contractId": "coding-basic",
        "agentRoles": ["coding"],
        "runOn": ["main"],
        "spawnDepth": {"minDepth": 0, "maxDepth": 0},
        "enforcement": "audit",
        attached_flag: True,
    }

    with pytest.raises(ValidationError):
        module.EvidenceContractScope.model_validate(payload)


def test_evidence_scope_import_stays_traffic_and_execution_free_in_fresh_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.harness.evidence_scope")
forbidden_modules = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.hooks.bus",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"evidence scope import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
