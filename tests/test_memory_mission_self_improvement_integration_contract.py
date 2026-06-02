from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from magi_agent.harness.memory_compaction import MemoryCompactionHarnessConfig
from magi_agent.harness.memory_recall import MemoryRecallHarnessConfig
from magi_agent.harness.memory_write import MemoryWriteHarnessConfig
from magi_agent.memory.adk_bridge import ADKMemoryBridgeConfig
from magi_agent.missions.cron_policy import CronSchedulerMutationConfig
from magi_agent.missions.events import MissionEventProjectionConfig
from magi_agent.missions.lifecycle import MissionLifecycleConfig
from magi_agent.runtime.long_running_activity import LongRunningActivityConfig
from magi_agent.self_improvement.drift_watch import DriftWatchConfig
from magi_agent.self_improvement.eval_capture import EvalCaptureConfig
from magi_agent.self_improvement.promotion_gate import SelfImprovementPromotionConfig
from magi_agent.self_improvement.proposals import SelfImprovementProposalConfig
from magi_agent.self_improvement.review_gate import SelfImprovementReviewConfig
from magi_agent.self_improvement.rollback import RollbackConfig


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
MATRIX_PATH = (
    PYTHON_ROOT
    / "tests/fixtures/parity/memory_self_improvement_mission_matrix.json"
)
README_PATH = PYTHON_ROOT / "README.md"
PLAN_PATH = (
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-23-python-adk-memory-self-improvement-mission-parity.md"
)


DOMAIN_LAYER_EXPECTATIONS = {
    "hipocampus_qmd_compatibility_adapter": "Memory recipe/harness/plugin",
    "readonly_memory_recall_recipe": "Memory recipe/harness/plugin",
    "memory_write_compaction_approval_boundary": "Memory recipe/harness/plugin",
    "mission_lifecycle_state_machine": "Mission recipe/harness/plugin",
    "cron_scheduler_mutation_boundary": "Mission recipe/harness/plugin",
    "background_long_running_activity_boundary": "Mission recipe/harness/plugin",
    "mission_progress_public_event_projection": "Mission recipe/harness/plugin",
    "self_improvement_eval_capture": "Self-improvement recipe/harness/plugin",
    "self_improvement_proposal_recipe": "Self-improvement recipe/harness/plugin",
    "self_improvement_review_promotion_gate": "Self-improvement recipe/harness/plugin",
    "rollback_regression_drift_watch": "Self-improvement recipe/harness/plugin",
}
GENERIC_CORE_DIRECTORIES = (
    PYTHON_ROOT / "magi_agent/runtime",
    PYTHON_ROOT / "magi_agent/storage",
    PYTHON_ROOT / "magi_agent/adk_bridge",
    PYTHON_ROOT / "magi_agent/evidence",
    PYTHON_ROOT / "magi_agent/artifacts",
)
DOMAIN_SPECIFIC_CORE_MARKERS = (
    "hermes",
    "hipocampus",
    "qmd",
    "mission_policy",
    "mission policy",
    "cron_policy",
    "cron policy",
    "mission completion criteria",
    "cron mutation policy",
    "self-improvement policy",
    "self_improvement policy",
    "self-evolution",
    "self_evolution",
)
BLOCKER_TERMS = (
    "separate approval boundary",
    "selected-bot canary",
    "k8s/env",
    "provider credentials",
    "production memory writes",
    "cron mutation",
    "background execution",
    "self-improvement promotion",
)
FORBIDDEN_DOC_CLAIMS = (
    "python adk is production authority",
    "python adk replaces typescript",
    "production parity complete",
    "user-visible python output is enabled",
)


def _load_matrix() -> dict[str, object]:
    return json.loads(MATRIX_PATH.read_text())


def _rows() -> dict[str, Mapping[str, object]]:
    matrix = _load_matrix()
    return {str(row["id"]): row for row in matrix["rows"]}  # type: ignore[index]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for nested in value.values():
            result.extend(_strings(nested))
        return result
    if isinstance(value, list):
        result = []
        for nested in value:
            result.extend(_strings(nested))
        return result
    return []


def _ref_paths(row: Mapping[str, object], key: str) -> set[str]:
    paths: set[str] = set()
    for item in row[key]:  # type: ignore[index]
        if isinstance(item, Mapping):
            paths.add(str(item["path"]))
        else:
            paths.add(str(item))
    return paths


def _assert_false_flags(label: str, dump: Mapping[str, object], aliases: tuple[str, ...]) -> None:
    for alias in aliases:
        assert dump.get(alias) is False, (label, alias, dump.get(alias))


def test_pr15_readiness_rows_are_closed_and_activation_blocked() -> None:
    rows = _rows()
    readiness = rows["integrated_memory_mission_self_improvement_readiness"]
    blockers = rows["activation_ladder_blockers"]

    readiness_refs = _ref_paths(readiness, "latestMainCoveredRefs")
    blocker_refs = _ref_paths(blockers, "latestMainCoveredRefs")

    assert "tests/test_memory_mission_self_improvement_integration_contract.py" in readiness_refs
    assert "tests/test_memory_mission_self_improvement_integration_contract.py" in blocker_refs
    assert "docs/superpowers/plans/2026-05-23-python-adk-memory-self-improvement-mission-parity.md" in readiness_refs
    assert readiness["missingImplementation"] == []
    assert blockers["missingImplementation"] == []
    assert readiness["owningLayer"] == "Tests/docs only"
    assert blockers["owningLayer"] == "Tests/docs only"
    assert readiness["defaultOff"] is True
    assert blockers["defaultOff"] is True
    assert readiness["trafficAttached"] is False
    assert blockers["trafficAttached"] is False
    assert readiness["productionAuthority"] is False
    assert blockers["productionAuthority"] is False


def test_domain_behavior_is_recipe_harness_or_plugin_owned() -> None:
    rows = _rows()

    for row_id, owning_layer in DOMAIN_LAYER_EXPECTATIONS.items():
        row = rows[row_id]
        assert row["owningLayer"] == owning_layer
        assert row["owningLayer"] != "Core substrate"
        assert row["productionAuthority"] is False
        assert row["trafficAttached"] is False


def test_generic_core_paths_do_not_hard_code_memory_mission_or_self_improvement_policy() -> None:
    offenders: list[str] = []
    for directory in GENERIC_CORE_DIRECTORIES:
        for path in directory.rglob("*.py"):
            text = path.read_text().lower()
            for marker in DOMAIN_SPECIFIC_CORE_MARKERS:
                if marker in text:
                    offenders.append(f"{path.relative_to(PYTHON_ROOT)} contains {marker}")

    assert offenders == []


def test_live_attachment_flags_remain_false_by_default() -> None:
    checks: tuple[tuple[str, Mapping[str, object], tuple[str, ...]], ...] = (
        (
            "memory-recall",
            MemoryRecallHarnessConfig().model_dump(by_alias=True),
            (
                "liveProviderEnabled",
                "trafficAttached",
                "userVisibleOutputAllowed",
                "memoryWriteAllowed",
                "productionWriteAllowed",
            ),
        ),
        (
            "memory-write",
            MemoryWriteHarnessConfig().model_dump(by_alias=True),
            (
                "productionWriteEnabled",
                "providerCallAllowed",
                "filesystemMutationAllowed",
                "databaseMutationAllowed",
                "networkCallAllowed",
                "adkMemoryServiceWriteEnabled",
                "trafficAttached",
            ),
        ),
        (
            "memory-compaction",
            MemoryCompactionHarnessConfig().model_dump(by_alias=True),
            (
                "productionWriteEnabled",
                "providerCallAllowed",
                "filesystemMutationAllowed",
                "databaseMutationAllowed",
                "networkCallAllowed",
                "adkMemoryServiceWriteEnabled",
                "trafficAttached",
            ),
        ),
        (
            "adk-memory",
            ADKMemoryBridgeConfig().model_dump(by_alias=True),
            ("enabled", "localFakeProviderEnabled", "localFakeAdkServiceEnabled"),
        ),
        (
            "mission-lifecycle",
            MissionLifecycleConfig().model_dump(by_alias=True),
            (
                "productionMutationEnabled",
                "trafficAttached",
                "schedulerAttached",
                "cronMutationEnabled",
                "backgroundExecutionEnabled",
                "toolHostDispatchEnabled",
                "channelDeliveryEnabled",
                "workspaceMutationEnabled",
                "memoryMutationEnabled",
            ),
        ),
        (
            "cron-scheduler",
            CronSchedulerMutationConfig().model_dump(by_alias=True),
            (
                "liveCronMutationEnabled",
                "schedulerAttached",
                "backgroundExecutionEnabled",
                "trafficAttached",
                "productionWritesEnabled",
                "providerCallAllowed",
            ),
        ),
        (
            "mission-events",
            MissionEventProjectionConfig().model_dump(by_alias=True),
            (
                "productionWriteEnabled",
                "routeActivationEnabled",
                "userVisibleOutputEnabled",
                "channelDeliveryEnabled",
                "workspaceMutationEnabled",
                "memoryMutationEnabled",
                "cronMutationEnabled",
                "liveBackgroundExecutionEnabled",
            ),
        ),
        (
            "background-activity",
            LongRunningActivityConfig().model_dump(by_alias=True),
            (
                "longRunningFunctionToolAttached",
                "productionBackgroundExecutionEnabled",
                "trafficAttached",
                "userVisibleOutputEnabled",
                "productionWritesEnabled",
                "providerCallAllowed",
            ),
        ),
        (
            "self-improvement-eval",
            EvalCaptureConfig().model_dump(by_alias=True),
            (
                "enabled",
                "localFakeCaptureEnabled",
                "productionWriteEnabled",
                "liveAdkEvaluationEnabled",
                "automaticMutationEnabled",
            ),
        ),
        (
            "self-improvement-proposal",
            SelfImprovementProposalConfig().model_dump(by_alias=True),
            ("liveAdkRunnerEnabled", "automaticMutationEnabled"),
        ),
        (
            "self-improvement-review",
            SelfImprovementReviewConfig().model_dump(by_alias=True),
            ("liveAdkRunnerEnabled", "automaticPromotionEnabled"),
        ),
        (
            "self-improvement-promotion",
            SelfImprovementPromotionConfig().model_dump(by_alias=True),
            ("productionMutationEnabled", "automaticPromotionEnabled"),
        ),
        (
            "self-improvement-rollback",
            RollbackConfig().model_dump(by_alias=True),
            (
                "localFakeRollbackExecutionEnabled",
                "productionRollbackEnabled",
                "automaticRollbackEnabled",
            ),
        ),
        (
            "self-improvement-drift",
            DriftWatchConfig().model_dump(by_alias=True),
            ("enabled", "localFakeDriftWatchEnabled", "automaticRollbackEnabled"),
        ),
    )

    for label, dump, aliases in checks:
        _assert_false_flags(label, dump, aliases)


def test_readiness_docs_record_boundaries_and_do_not_claim_production_parity() -> None:
    docs_text = "\n".join((README_PATH.read_text(), PLAN_PATH.read_text())).lower()
    unwrapped_docs_text = " ".join(docs_text.split())

    assert "memory/mission/self-improvement readiness" in docs_text
    assert "typescript remains response authority" in docs_text
    assert "separate gate 5b/gate 1a activation track" in unwrapped_docs_text
    assert "python adk remains default-off" in docs_text
    assert "covered rows" in docs_text
    assert "remaining gaps" in docs_text
    assert "next activation boundary" in docs_text
    for term in BLOCKER_TERMS:
        assert term in docs_text
    for claim in FORBIDDEN_DOC_CLAIMS:
        assert claim not in docs_text
