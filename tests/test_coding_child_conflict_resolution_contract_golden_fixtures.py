from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.coding_child_conflict_resolution_contract import (
    CodingChildConflictResolutionAttachmentFlags,
    CodingChildConflictResolutionFixture,
    load_coding_child_conflict_resolution_fixture,
    project_coding_child_conflict_resolution_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "coding_child_conflict_resolution"


def _case_payload(payload: dict[str, object], case_id: str) -> dict[str, object]:
    cases = payload["cases"]
    assert isinstance(cases, list)
    for case in cases:
        assert isinstance(case, dict)
        if case.get("caseId") == case_id:
            return case
    raise AssertionError(f"missing fixture case {case_id}")


def test_coding_child_conflict_resolution_fixture_covers_freshness_order_metadata() -> None:
    fixture = load_coding_child_conflict_resolution_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_coding_child_conflict_resolution_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "coding_child_conflict_resolution_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "unresolved_child_worktree_conflict_remains_blocking",
        "later_conflict_resolver_covering_conflicted_files_clears_conflict",
        "same_spawn_reject_clears_conflict",
        "different_spawn_disposition_does_not_clear_conflict",
        "same_spawn_apply_clears_conflict_but_reviewer_freshness_required",
        "same_spawn_cherry_pick_clears_conflict_but_reviewer_freshness_required",
    )
    assert projection.by_resolution_state == {
        "blocking_conflict": 2,
        "conflict_metadata_cleared": 4,
    }
    assert projection.by_category == {
        "unresolved_conflict_blocks": 1,
        "later_resolver_clears": 1,
        "same_spawn_reject_clears": 1,
        "different_spawn_disposition_ignored": 1,
        "same_spawn_apply_requires_reviewer": 1,
        "same_spawn_cherry_pick_requires_reviewer": 1,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True

    unresolved = cases["unresolved_child_worktree_conflict_remains_blocking"]
    assert unresolved.conflict_metadata.blocking is True
    assert unresolved.conflict_metadata.conflicted_files == ("src/agent.ts", "src/state.ts")
    assert unresolved.resolution_state == "blocking_conflict"
    assert unresolved.reason_codes == ("child_worktree_conflict_unresolved",)
    assert projection.case_snapshots[unresolved.case_id]["conflictCleared"] is False
    assert projection.case_snapshots[unresolved.case_id]["blocking"] is True

    later = cases["later_conflict_resolver_covering_conflicted_files_clears_conflict"]
    assert later.resolution_attempt is not None
    assert later.resolution_attempt.persona == "conflict_resolver"
    assert later.resolution_attempt.spawn_ref == "spawn:resolver-later-1"
    assert later.resolution_attempt.spawn_started_at > later.conflict_metadata.spawn_observed_at
    assert later.resolution_attempt.covered_files == later.conflict_metadata.conflicted_files
    later_snapshot = projection.case_snapshots[later.case_id]
    assert later_snapshot["conflictCleared"] is True
    assert later_snapshot["resolutionReason"] == "later_conflict_resolver_covers_conflicted_files"

    reject = cases["same_spawn_reject_clears_conflict"]
    assert reject.disposition is not None
    assert reject.disposition.action == "reject"
    assert reject.disposition.spawn_ref == reject.conflict_metadata.spawn_ref
    assert reject.disposition.result_index > reject.conflict_metadata.conflict_index
    assert projection.case_snapshots[reject.case_id]["conflictCleared"] is True
    assert projection.case_snapshots[reject.case_id]["resolutionReason"] == "same_spawn_reject"

    different = cases["different_spawn_disposition_does_not_clear_conflict"]
    assert different.disposition is not None
    assert different.disposition.spawn_ref != different.conflict_metadata.spawn_ref
    assert different.resolution_state == "blocking_conflict"
    assert projection.case_snapshots[different.case_id]["conflictCleared"] is False
    assert projection.case_snapshots[different.case_id]["resolutionIgnoredReason"] == (
        "disposition_spawn_mismatch"
    )

    apply_case = cases["same_spawn_apply_clears_conflict_but_reviewer_freshness_required"]
    assert apply_case.disposition is not None
    assert apply_case.disposition.action == "apply"
    assert apply_case.reviewer_freshness.required is True
    assert apply_case.reviewer_freshness.satisfied is False
    assert apply_case.reviewer_freshness.latest_child_mutation_at == 50
    assert projection.case_snapshots[apply_case.case_id]["conflictCleared"] is True
    assert projection.case_snapshots[apply_case.case_id]["requiresFreshReviewer"] is True

    cherry_pick = cases[
        "same_spawn_cherry_pick_clears_conflict_but_reviewer_freshness_required"
    ]
    assert cherry_pick.disposition is not None
    assert cherry_pick.disposition.action == "cherry_pick"
    assert cherry_pick.reviewer_freshness.required is True
    assert cherry_pick.reviewer_freshness.satisfied is False
    assert projection.case_snapshots[cherry_pick.case_id]["resolutionReason"] == (
        "same_spawn_cherry_pick"
    )

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "Bearer unsafe",
        "ghp_conflictsecret",
        "sk-conflict-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "/data/bots",
        "/workspace",
        "/var/lib/kubelet",
        "/Users/kevin/.ssh/id_rsa",
        "/private/tmp/session.log",
        "/mnt/cluster-volume/session.log",
        "raw diff",
        "diff --git",
        "--- a/",
        "+++ b/",
        "raw transcript",
        "adkRunnerInvoked\": true",
        "childExecutionAttached\": true",
        "toolHostDispatched\": true",
        "gitExecuted\": true",
        "shellOrCodeExecuted\": true",
        "testExecuted\": true",
        "fileMutated\": true",
        "workspaceMutated\": true",
        "liveAdoptionAttached\": true",
        "routeOrApiAttached\": true",
        "productionStorageWritten\": true",
        "canaryTrafficAttached\": true",
        "telegramAttached\": true",
        "evidenceBlockEnabled\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


def test_coding_child_conflict_resolution_later_resolver_allows_extra_covered_files() -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    later = _case_payload(
        payload,
        "later_conflict_resolver_covering_conflicted_files_clears_conflict",
    )
    resolution_attempt = later["resolutionAttempt"]
    assert isinstance(resolution_attempt, dict)
    resolution_attempt["coveredFiles"] = [
        "src/agent.ts",
        "src/state.ts",
        "docs/conflict-notes.md",
    ]

    fixture = CodingChildConflictResolutionFixture.model_validate(payload)
    projection = project_coding_child_conflict_resolution_fixture(fixture)

    snapshot = projection.case_snapshots[
        "later_conflict_resolver_covering_conflicted_files_clears_conflict"
    ]
    assert snapshot["conflictCleared"] is True
    assert snapshot["resolutionReason"] == "later_conflict_resolver_covers_conflicted_files"


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"adkRunnerInvoked": True}),
            id="fixture-adk-runner-flag",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"childExecutionAttached": True}),
            id="fixture-child-execution-flag",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"toolHostDispatched": True}),
            id="fixture-toolhost-flag",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"evidenceBlockEnabled": True}),
            id="fixture-evidence-block-mode",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"workspaceMutated": True}
            ),
            id="case-workspace-mutated-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "raw diff diff --git a/src/agent.ts b/src/agent.ts"}
            ),
            id="raw-diff-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "raw transcript from reviewer"}
            ),
            id="raw-transcript-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["conflictMetadata"].update(
                {"conflictedFiles": ["/workspace/src/agent.ts"]}
            ),
            id="production-path-conflicted-file",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["conflictMetadata"].update(
                {"conflictedFiles": ["/Users/kevin/.ssh/id_rsa"]}
            ),
            id="private-user-path-conflicted-file",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "conflict recorded at /private/tmp/session.log"}
            ),
            id="private-tmp-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "conflict recorded at /mnt/cluster-volume/session.log"}
            ),
            id="mounted-volume-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "Bearer unsafe ghp_conflictsecret"}
            ),
            id="secret-public-preview",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "later_conflict_resolver_covering_conflicted_files_clears_conflict",
            )["resolutionAttempt"].update({"coveredFiles": ["src/agent.ts"]}),
            id="resolver-does-not-cover-all-conflicted-files",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "later_conflict_resolver_covering_conflicted_files_clears_conflict",
            )["resolutionAttempt"].update({"spawnStartedAt": 10}),
            id="resolver-not-later-than-conflict",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "same_spawn_reject_clears_conflict",
            )["disposition"].update({"spawnRef": "spawn:other"}),
            id="same-spawn-reject-mismatch",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "same_spawn_reject_clears_conflict",
            )["disposition"].update({"resultIndex": 29}),
            id="same-spawn-reject-before-conflict",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "different_spawn_disposition_does_not_clear_conflict",
            )["disposition"].update({"spawnRef": "spawn:child-conflict-4"}),
            id="different-spawn-disposition-claims-same-spawn",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "same_spawn_apply_clears_conflict_but_reviewer_freshness_required",
            )["reviewerFreshness"].update({"satisfied": True}),
            id="apply-case-reviewer-freshness-satisfied",
        ),
    ),
)
def test_coding_child_conflict_resolution_rejects_live_flags_and_bad_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        CodingChildConflictResolutionFixture.model_validate(payload)


def test_coding_child_conflict_resolution_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = CodingChildConflictResolutionAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        childExecutionAttached=True,
        toolHostDispatched=True,
        gitExecuted=True,
        shellOrCodeExecuted=True,
        testExecuted=True,
        fileMutated=True,
        workspaceMutated=True,
        liveAdoptionAttached=True,
        routeOrApiAttached=True,
        productionStorageWritten=True,
        canaryTrafficAttached=True,
        telegramAttached=True,
        evidenceBlockEnabled=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"adkRunnerInvoked": True})


def test_coding_child_conflict_resolution_import_boundary_stays_runtime_free() -> None:
    module_name = "openmagi_core_agent.shadow.coding_child_conflict_resolution_contract"
    forbidden = (
        "google.adk",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.tools.registry",
        "openmagi_core_agent.plugins.agentmemory",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.services.memory",
        "openmagi_core_agent.hipocampus",
        "openmagi_core_agent.qmd",
        "openmagi_core_agent.git",
        "openmagi_core_agent.shell",
        "openmagi_core_agent.test",
        "openmagi_core_agent.tests",
        "openmagi_core_agent.workspace.mutation",
        "openmagi_core_agent.workspace.adoption",
        "openmagi_core_agent.app",
        "openmagi_core_agent.transport.chat",
        "openmagi_core_agent.routes",
    )
    removed_modules: dict[str, object] = {}
    for loaded_name in tuple(sys.modules):
        if (
            loaded_name == "openmagi_core_agent"
            or loaded_name.startswith("openmagi_core_agent.")
            or loaded_name == "google.adk"
            or loaded_name.startswith("google.adk.")
        ):
            removed = sys.modules.pop(loaded_name, None)
            if removed is not None:
                removed_modules[loaded_name] = removed

    try:
        module = importlib.import_module(module_name)
        fixture = module.load_coding_child_conflict_resolution_fixture(
            "policy_matrix.json",
            fixture_root=FIXTURES,
        )
        module.project_coding_child_conflict_resolution_fixture(fixture)

        loaded = [
            loaded_name
            for loaded_name in sorted(sys.modules)
            for forbidden_name in forbidden
            if loaded_name == forbidden_name
            or loaded_name.startswith(f"{forbidden_name}.")
        ]
        assert loaded == []
    finally:
        for loaded_name in tuple(sys.modules):
            if (
                loaded_name == "openmagi_core_agent"
                or loaded_name.startswith("openmagi_core_agent.")
                or loaded_name == "google.adk"
                or loaded_name.startswith("google.adk.")
            ):
                sys.modules.pop(loaded_name, None)
        sys.modules.update(removed_modules)
