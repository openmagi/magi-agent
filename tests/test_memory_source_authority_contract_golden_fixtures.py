from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.memory_source_authority_contract import (
    MemorySourceAuthorityAttachmentFlags,
    MemorySourceAuthorityGuardFixture,
    load_memory_source_authority_guard_fixture,
    project_memory_source_authority_guard_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "memory_source_authority"


def test_memory_source_authority_fixture_covers_guard_decisions() -> None:
    fixture = load_memory_source_authority_guard_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_memory_source_authority_guard_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "memory_source_authority_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "normal_recall_metadata_only",
        "read_only_recall_metadata_only",
        "read_only_write_blocked",
        "incognito_recall_blocked",
        "incognito_write_blocked",
        "source_authority_long_term_disabled",
        "redaction_failure_blocks_projection",
        "provider_unavailable_fail_open_no_claim",
        "explicit_write_requires_receipt_before_claim",
        "memory_redact_authority_supersedes_provider",
        "stale_conflicting_memory_background_only",
        "child_agent_memory_scope_isolated",
        "selected_kb_current_source_background_only",
        "attachment_current_source_background_only",
        "image_current_source_background_only",
        "classifier_disabled_blocks_recall",
        "root_memory_background_without_continuation",
        "qmd_active_with_continuation_overlap",
        "stale_background_memory_retry_metadata",
        "passive_background_memory_reference_audit_ok",
        "hipocampus_root_precedes_legacy_memory_metadata",
    )
    assert projection.by_decision == {
        "allow_metadata_only": 11,
        "block": 7,
        "fail_open_no_claim": 1,
        "approval_required": 2,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_memory_runtime is True

    normal_recall = cases["normal_recall_metadata_only"]
    assert normal_recall.memory_mode == "normal"
    assert normal_recall.source_authority == "long_term_allowed"
    assert normal_recall.provider.provider_id == "hipocampus"
    assert normal_recall.provider.provider_call_made is False
    assert normal_recall.recall_intent is True
    assert normal_recall.write_intent is False
    assert normal_recall.decision == "allow_metadata_only"
    assert normal_recall.prompt_projection_allowed is False
    assert normal_recall.public_projection_allowed is True
    assert normal_recall.write_claim_allowed is False

    read_only_write = cases["read_only_write_blocked"]
    assert read_only_write.memory_mode == "read_only"
    assert read_only_write.write_intent is True
    assert read_only_write.decision == "block"
    assert read_only_write.reason_codes == ("read_only_blocks_writes",)

    incognito_recall = cases["incognito_recall_blocked"]
    assert incognito_recall.memory_mode == "incognito"
    assert incognito_recall.recall_intent is True
    assert incognito_recall.decision == "block"
    assert incognito_recall.prompt_projection_allowed is False
    assert incognito_recall.public_projection_allowed is False

    source_disabled = cases["source_authority_long_term_disabled"]
    assert source_disabled.source_authority == "long_term_disabled"
    assert source_disabled.decision == "block"
    assert source_disabled.reason_codes == ("source_authority_disables_long_term_memory",)

    redaction_failure = cases["redaction_failure_blocks_projection"]
    assert redaction_failure.redaction_status == "failed"
    assert redaction_failure.prompt_projection_allowed is False
    assert redaction_failure.public_projection_allowed is False
    assert redaction_failure.decision == "block"

    provider_unavailable = cases["provider_unavailable_fail_open_no_claim"]
    assert provider_unavailable.provider.provider_id == "agentmemory"
    assert provider_unavailable.provider.provider_status == "unavailable"
    assert provider_unavailable.decision == "fail_open_no_claim"
    assert provider_unavailable.write_claim_allowed is False
    assert provider_unavailable.no_user_facing_memory_claim is True

    write_without_receipt = cases["explicit_write_requires_receipt_before_claim"]
    assert write_without_receipt.write_intent is True
    assert write_without_receipt.decision == "approval_required"
    assert write_without_receipt.control_request is not None
    assert write_without_receipt.write_claim_allowed is False
    assert write_without_receipt.write_receipt_ref is None
    assert projection.control_requests["explicit_write_requires_receipt_before_claim"] == {
        "requestId": "memory-write:turn-memory-1",
        "turnId": "turn-memory-1",
        "toolName": "MemoryRemember",
        "reason": "explicit memory write requires approval and receipt before claim",
    }

    memory_redact = cases["memory_redact_authority_supersedes_provider"]
    assert memory_redact.redact_intent is True
    assert memory_redact.memory_redact_authority == "openmagi_memory_redact"
    assert memory_redact.provider.provider_id == "agentmemory"
    assert memory_redact.provider.provider_delete_or_redact_allowed is False
    assert memory_redact.decision == "approval_required"

    stale_memory = cases["stale_conflicting_memory_background_only"]
    assert stale_memory.source_authority == "background_only"
    assert stale_memory.background_only is True
    assert stale_memory.priority == "background"
    assert stale_memory.current_source_priority == "current_workspace_user_instruction"

    child_isolated = cases["child_agent_memory_scope_isolated"]
    assert child_isolated.scope.child_execution_id == "child-memory-1"
    assert child_isolated.scope.inherited_from_parent is False
    assert child_isolated.decision == "block"
    assert child_isolated.reason_codes == ("child_memory_scope_isolated",)

    selected_kb = cases["selected_kb_current_source_background_only"]
    assert selected_kb.source_metadata is not None
    assert selected_kb.source_metadata.current_source_kinds == ("selected_kb",)
    assert selected_kb.source_metadata.effective_long_term_memory_policy == "background_only"
    assert selected_kb.background_only is True

    attachment = cases["attachment_current_source_background_only"]
    assert attachment.source_metadata is not None
    assert attachment.source_metadata.current_source_kinds == ("attachment",)
    assert attachment.source_metadata.effective_long_term_memory_policy == "background_only"

    image = cases["image_current_source_background_only"]
    assert image.source_metadata is not None
    assert image.source_metadata.current_source_kinds == ("image",)
    assert image.source_metadata.effective_long_term_memory_policy == "background_only"

    classifier_disabled = cases["classifier_disabled_blocks_recall"]
    assert classifier_disabled.source_metadata is not None
    assert classifier_disabled.source_metadata.effective_long_term_memory_policy == "disabled"
    assert classifier_disabled.decision == "block"
    assert classifier_disabled.reason_codes == (
        "classifier_disabled_long_term_memory",
        "source_authority_disables_long_term_memory",
    )

    root_background = cases["root_memory_background_without_continuation"]
    assert root_background.continuity_metadata is not None
    assert root_background.continuity_metadata.memory_recall_source == "root"
    assert root_background.continuity_metadata.continuity == "background"
    assert root_background.continuity_metadata.continuation_cue is False

    qmd_active = cases["qmd_active_with_continuation_overlap"]
    assert qmd_active.continuity_metadata is not None
    assert qmd_active.continuity_metadata.memory_recall_source == "qmd"
    assert qmd_active.continuity_metadata.continuity == "active"
    assert qmd_active.continuity_metadata.continuation_cue is True
    assert qmd_active.continuity_metadata.token_overlap is True

    stale_retry = cases["stale_background_memory_retry_metadata"]
    assert stale_retry.continuity_metadata is not None
    assert stale_retry.continuity_metadata.stale_promotion_retry is not None
    assert stale_retry.continuity_metadata.stale_promotion_retry.model_dump(
        by_alias=True,
        mode="json",
    ) == {
        "retry": True,
        "phrase": "legacy onboarding question",
        "path": "memory/daily/redacted.md",
        "reason": "background memory phrase promoted into decision request",
    }

    passive = cases["passive_background_memory_reference_audit_ok"]
    assert passive.continuity_metadata is not None
    assert passive.continuity_metadata.stale_promotion_retry is not None
    assert passive.continuity_metadata.stale_promotion_retry.retry is False
    assert passive.decision == "allow_metadata_only"
    assert "passive_background_reference_audit_ok" in passive.reason_codes

    root_precedence = cases["hipocampus_root_precedes_legacy_memory_metadata"]
    assert root_precedence.continuity_metadata is not None
    assert root_precedence.continuity_metadata.memory_recall_source == "root"
    assert root_precedence.continuity_metadata.path == "memory/ROOT.md"
    assert "legacy_memory_md_shadowed_by_root" in root_precedence.reason_codes

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "Bearer unsafe",
        "ghp_memorysecret",
        "sk-memory-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "/data/bots",
        "/workspace",
        "raw memory payload",
        "adkRunnerInvoked\": true",
        "liveMemoryProviderCalled\": true",
        "promptInjected\": true",
        "memoryWritten\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json

    assert projection.public_previews["normal_recall_metadata_only"] == (
        "recall metadata available; provider call omitted"
    )
    assert projection.public_previews["provider_unavailable_fail_open_no_claim"] == (
        "provider unavailable; continuing without memory claim"
    )
    assert projection.case_snapshots["normal_recall_metadata_only"]["provider"]["providerId"] == (
        "hipocampus"
    )
    assert projection.case_snapshots["memory_redact_authority_supersedes_provider"][
        "memoryRedactAuthority"
    ] == "openmagi_memory_redact"
    assert projection.case_snapshots["selected_kb_current_source_background_only"][
        "sourceMetadata"
    ] == {
        "currentSourceKinds": ["selected_kb"],
        "effectiveLongTermMemoryPolicy": "background_only",
        "classifierReason": "current selected KB is authoritative for this turn",
    }
    assert projection.case_snapshots["qmd_active_with_continuation_overlap"][
        "continuityMetadata"
    ]["continuity"] == "active"
    assert projection.case_snapshots["stale_background_memory_retry_metadata"][
        "continuityMetadata"
    ]["stalePromotionRetry"]["retry"] is True


def test_memory_source_authority_rejects_normal_policy_with_current_sources() -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    normal_recall = next(
        case for case in payload["cases"] if case["caseId"] == "normal_recall_metadata_only"
    )
    normal_recall["sourceMetadata"] = {
        "currentSourceKinds": ["selected_kb"],
        "effectiveLongTermMemoryPolicy": "normal",
        "classifierReason": "normal long-term memory cannot share current source authority",
    }

    with pytest.raises(ValidationError):
        MemorySourceAuthorityGuardFixture.model_validate(payload)


def test_memory_source_authority_accepts_disabled_policy_with_current_sources() -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    classifier_disabled = next(
        case
        for case in payload["cases"]
        if case["caseId"] == "classifier_disabled_blocks_recall"
    )
    classifier_disabled["sourceMetadata"]["currentSourceKinds"] = ["selected_kb"]

    fixture = MemorySourceAuthorityGuardFixture.model_validate(payload)
    cases = {case.case_id: case for case in fixture.cases}

    assert cases["classifier_disabled_blocks_recall"].source_metadata is not None
    assert cases[
        "classifier_disabled_blocks_recall"
    ].source_metadata.current_source_kinds == ("selected_kb",)


def test_memory_source_authority_accepts_background_only_policy_without_current_sources() -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    selected_kb = next(
        case
        for case in payload["cases"]
        if case["caseId"] == "selected_kb_current_source_background_only"
    )
    selected_kb["sourceMetadata"]["currentSourceKinds"] = []

    fixture = MemorySourceAuthorityGuardFixture.model_validate(payload)
    cases = {case.case_id: case for case in fixture.cases}

    assert cases["selected_kb_current_source_background_only"].source_metadata is not None
    assert cases[
        "selected_kb_current_source_background_only"
    ].source_metadata.current_source_kinds == ()


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update(
                {"liveMemoryProviderCalled": True}
            ),
            id="fixture-live-provider-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"adkRunnerInvoked": True}
            ),
            id="case-runner-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["provider"].update(
                {"providerCallMade": True}
            ),
            id="provider-call",
        ),
        pytest.param(
            lambda payload: payload["cases"][2].update(
                {"decision": "allow_metadata_only"}
            ),
            id="read-only-write-allowed",
        ),
        pytest.param(
            lambda payload: payload["cases"][3].update(
                {"decision": "allow_metadata_only"}
            ),
            id="incognito-recall-allowed",
        ),
        pytest.param(
            lambda payload: payload["cases"][6].update(
                {"promptProjectionAllowed": True}
            ),
            id="redaction-failed-prompt-projection",
        ),
        pytest.param(
            lambda payload: payload["cases"][7].update(
                {"decision": "allow_metadata_only"}
            ),
            id="provider-unavailable-claim",
        ),
        pytest.param(
            lambda payload: payload["cases"][8].update({"writeClaimAllowed": True}),
            id="write-claim-without-receipt",
        ),
        pytest.param(
            lambda payload: payload["cases"][9]["provider"].update(
                {"providerDeleteOrRedactAllowed": True}
            ),
            id="provider-redact-authority",
        ),
        pytest.param(
            lambda payload: payload["cases"][11]["scope"].update(
                {"inheritedFromParent": True}
            ),
            id="child-isolation-inherited",
        ),
        pytest.param(
            lambda payload: next(
                case
                for case in payload["cases"]
                if case["caseId"] == "selected_kb_current_source_background_only"
            )["sourceMetadata"].update({"effectiveLongTermMemoryPolicy": "normal"}),
            id="current-source-policy-not-background-only",
        ),
        pytest.param(
            lambda payload: (
                next(
                    case
                    for case in payload["cases"]
                    if case["caseId"] == "attachment_current_source_background_only"
                ).update({"sourceAuthority": "long_term_allowed"}),
                next(
                    case
                    for case in payload["cases"]
                    if case["caseId"] == "attachment_current_source_background_only"
                )["sourceMetadata"].update({"effectiveLongTermMemoryPolicy": "normal"}),
            ),
            id="long-term-allowed-current-source-policy-normal",
        ),
        pytest.param(
            lambda payload: next(
                case
                for case in payload["cases"]
                if case["caseId"] == "qmd_active_with_continuation_overlap"
            )["continuityMetadata"].update({"tokenOverlap": False}),
            id="qmd-active-without-overlap",
        ),
        pytest.param(
            lambda payload: next(
                case
                for case in payload["cases"]
                if case["caseId"] == "stale_background_memory_retry_metadata"
            )["continuityMetadata"]["stalePromotionRetry"].update({"path": None}),
            id="retry-metadata-missing-path",
        ),
    ),
)
def test_memory_source_authority_fixture_rejects_live_flags_and_policy_bypasses(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        MemorySourceAuthorityGuardFixture.model_validate(payload)


def test_memory_source_authority_flags_remain_false_under_construct_and_copy() -> None:
    constructed = MemorySourceAuthorityAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        liveMemoryProviderCalled=True,
        promptInjected=True,
        memoryWritten=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"memoryWritten": True})


def test_memory_source_authority_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.memory_source_authority_contract import (
    load_memory_source_authority_guard_fixture,
    project_memory_source_authority_guard_fixture,
)

fixture_root = Path('tests/fixtures/memory_source_authority')
fixture = load_memory_source_authority_guard_fixture('policy_matrix.json', fixture_root=fixture_root)
project_memory_source_authority_guard_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'google.adk.memory',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.adk_bridge.tool_adapter',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.plugins.agentmemory',
    'magi_agent.memory',
    'magi_agent.services.memory',
    'magi_agent.hipocampus',
    'magi_agent.qmd',
    'magi_agent.app',
    'magi_agent.transport.chat',
    'magi_agent.routes',
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
