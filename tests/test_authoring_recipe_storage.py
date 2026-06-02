from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.authoring.contracts import (
    GeneratedPluginProposal,
    RecipeBuilderSession,
    RecipePackDraft,
    RecipePackVersion,
)
from magi_agent import authoring as authoring_module
from magi_agent.authoring.storage import (
    CompiledSnapshotRef,
    EvalResultRef,
    GeneratedPluginProposalArtifactRef,
    LocalRecipePackStorage,
    RecipePackApprovalRef,
    RecipePackStorageError,
    digest_storage_content,
)

FIXTURES = Path(__file__).parent / "fixtures" / "authoring"


def _session_payload() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "compile_recipe_pack_success.json").read_text(encoding="utf-8")
    )


def _session() -> RecipeBuilderSession:
    return RecipeBuilderSession.model_validate(_session_payload())


def _scope_for_session(session_id: str) -> dict[str, object]:
    session = _session()
    return {
        "botId": session.bot_id,
        "ownerId": session.owner_id,
        "sessionId": session_id,
        "mode": "recipe_builder",
    }


def _draft(**overrides: object) -> RecipePackDraft:
    payload = _session_payload()["draft"]
    assert isinstance(payload, dict)
    payload = dict(payload)
    payload.update(overrides)
    return RecipePackDraft.model_validate(payload)


def _version(draft_record_digest: str) -> RecipePackVersion:
    draft = _draft()
    return RecipePackVersion(
        packId=draft.pack.pack_id,
        version="v1",
        sourceDraftId=draft.draft_id,
        status="candidate",
        sourceDigest=draft_record_digest,
    )


def test_storage_contracts_are_publicly_importable_from_authoring_package() -> None:
    assert authoring_module.LocalRecipePackStorage is LocalRecipePackStorage
    assert authoring_module.CompiledSnapshotRef is CompiledSnapshotRef
    assert authoring_module.GeneratedPluginProposalArtifactRef is (
        GeneratedPluginProposalArtifactRef
    )


def test_draft_save_read_list_are_current_bot_scoped_and_digest_stable() -> None:
    storage = LocalRecipePackStorage()
    scope = _session()
    draft = _draft()

    first = storage.save_draft(scope, draft)
    second = storage.save_draft(scope, draft)

    assert first.storage_status == "draft"
    assert first.bot_id == scope.bot_id
    assert first.owner_id == scope.owner_id
    assert first.draft_digest == second.draft_digest
    assert first.content_digest == digest_storage_content(draft)
    assert storage.read_draft(scope, draft.draft_id) == second
    assert storage.list_drafts(scope) == (second,)

    other_bot_scope = {
        "botId": "bot_recipe_builder_mode_other",
        "ownerId": scope.owner_id,
        "sessionId": scope.session_id,
        "mode": "recipe_builder",
    }
    other_owner_scope = {
        "botId": scope.bot_id,
        "ownerId": "owner_recipe_builder_mode_other",
        "sessionId": scope.session_id,
        "mode": "recipe_builder",
    }

    assert storage.read_draft(other_bot_scope, draft.draft_id) is None
    assert storage.read_draft(other_owner_scope, draft.draft_id) is None
    assert storage.list_drafts(other_bot_scope) == ()
    assert storage.list_drafts(other_owner_scope) == ()


def test_storage_records_are_session_scoped_for_same_owner_and_bot() -> None:
    storage = LocalRecipePackStorage()
    session_a = _session()
    session_b = _scope_for_session("builder.session.source-review-b")
    draft_a = _draft()
    draft_b = _draft(authoringSessionId="builder.session.source-review-b")

    record_a = storage.save_draft(session_a, draft_a)
    storage.promote_draft_to_staging_candidate(
        session_a,
        draft_a.draft_id,
        reason="session A staging",
    )
    version_a = storage.save_version(session_a, _version(record_a.draft_digest))
    snapshot_a = storage.save_compiled_snapshot_ref(
        session_a,
        CompiledSnapshotRef(
            refId="compiled-snapshot-ref-session-a",
            packId=draft_a.pack.pack_id,
            version="v1",
            sourceDraftId=draft_a.draft_id,
            compiledSnapshotDigest="sha256:" + "6" * 64,
            status="compiled",
        ),
    )
    eval_a = storage.save_eval_result_ref(
        session_a,
        EvalResultRef(
            evalResultId="eval-result-session-a",
            draftId=draft_a.draft_id,
            resultDigest="sha256:" + "7" * 64,
            artifactRef="eval.result.session-a.001",
        ),
    )
    approval_a = storage.save_approval_ref(
        session_a,
        RecipePackApprovalRef(
            approvalRefId="approval-ref-session-a",
            draftId=draft_a.draft_id,
            authorityRef="authority:owner-human@1",
            approvalDigest="sha256:" + "8" * 64,
            status="required",
        ),
    )
    draft_with_proposal = _draft(
        draftId="draft.source-review.proposal-session-a",
        generatedPluginProposals=(
            GeneratedPluginProposal(
                proposalId="proposal.session-a-helper",
                status="proposed",
                name="Session A helper",
                reason="Review session A metadata after human review.",
            ).model_dump(by_alias=True),
        ),
    )
    storage.save_draft(session_a, draft_with_proposal)
    proposal_a = storage.save_generated_plugin_proposal_artifact_ref(
        session_a,
        GeneratedPluginProposalArtifactRef(
            proposalId="proposal.session-a-helper",
            draftId=draft_with_proposal.draft_id,
            artifactRef="proposal.session-a-helper.bundle-metadata",
            artifactDigest="sha256:" + "9" * 64,
        ),
    )

    assert storage.read_draft(session_b, draft_a.draft_id) is None
    assert storage.read_version(session_b, draft_a.pack.pack_id, "v1") is None
    assert storage.read_compiled_snapshot_ref(session_b, snapshot_a.ref_id) is None
    assert storage.read_eval_result_ref(session_b, eval_a.eval_result_id) is None
    assert storage.read_approval_ref(session_b, approval_a.approval_ref_id) is None
    assert storage.read_generated_plugin_proposal_artifact_ref(
        session_b,
        proposal_a.proposal_id,
    ) is None
    assert storage.list_drafts(session_b) == ()
    assert storage.list_versions(session_b) == ()
    assert storage.list_compiled_snapshot_refs(session_b) == ()
    assert storage.list_eval_result_refs(session_b) == ()
    assert storage.list_approval_refs(session_b) == ()
    assert storage.list_generated_plugin_proposal_artifact_refs(session_b) == ()
    assert storage.list_promotion_history(session_b) == ()

    record_b = storage.save_draft(session_b, draft_b)
    assert record_b.authoring_session_id == "builder.session.source-review-b"
    assert storage.read_draft(session_a, draft_a.draft_id).storage_status == (
        "staging_candidate"
    )
    assert storage.read_draft(session_b, draft_b.draft_id) == record_b
    assert storage.list_drafts(session_b) == (record_b,)
    assert storage.list_promotion_history(session_a, draft_a.draft_id)[0].authoring_session_id == (
        session_a.session_id
    )
    assert version_a.authoring_session_id == session_a.session_id


def test_draft_save_rejects_cross_bot_scope_and_raw_secrets() -> None:
    storage = LocalRecipePackStorage()
    scope = _session()

    with pytest.raises(RecipePackStorageError, match="scope botId"):
        storage.save_draft(
            scope,
            _draft(botId="bot_recipe_builder_mode_other"),
        )

    with pytest.raises(ValidationError, match="raw credential fields"):
        RecipePackDraft.model_validate(
            {
                **_session_payload()["draft"],
                "rawCredentials": {"api" + "Key": "synthetic-" + "token-value"},
            }
        )

    with pytest.raises(RecipePackStorageError, match="raw secrets"):
        storage.save_eval_result_ref(
            scope,
            EvalResultRef(
                evalResultId="eval-result-001",
                draftId=_draft().draft_id,
                resultDigest="sha256:" + "a" * 64,
                artifactRef="eval-result?token=secret-value",
            ),
        )


@pytest.mark.parametrize(
    "unsafe_ref",
    (
        "../.env",
        "./secret",
        "nested/secret",
        "nested\\secret",
        "https://user" + ":pass@example.com/x",
        "postgres://user" + ":pass@example.com/db",
        "supabase://project/ref",
        "vault://secret/data/openmagi",
        "gcs://private-bucket/object",
        "eval.result.source-review.001?X-Amz-Signature=abc123",
    ),
)
def test_storage_rejects_private_paths_and_credential_bearing_refs(
    unsafe_ref: str,
) -> None:
    storage = LocalRecipePackStorage()
    scope = _session()
    draft = _draft()
    storage.save_draft(scope, draft)

    with pytest.raises(RecipePackStorageError, match="private|raw secrets"):
        storage.save_eval_result_ref(
            scope,
            EvalResultRef(
                evalResultId=f"eval-result-{abs(hash(unsafe_ref))}",
                draftId=draft.draft_id,
                resultDigest="sha256:" + "3" * 64,
                artifactRef=unsafe_ref,
            ),
        )

    public_ref = storage.save_eval_result_ref(
        scope,
        EvalResultRef(
            evalResultId="eval-result-public-dotted-ref",
            draftId=draft.draft_id,
            resultDigest="sha256:" + "4" * 64,
            artifactRef="eval.result.source-review.001",
        ),
    )
    assert public_ref.artifact_ref == "eval.result.source-review.001"


def test_status_transition_requires_explicit_staging_method_and_has_no_active_transition() -> None:
    storage = LocalRecipePackStorage()
    scope = _session()
    draft = _draft()
    saved = storage.save_draft(scope, draft)

    assert saved.storage_status == "draft"
    assert not hasattr(storage, "activate_draft")
    assert not hasattr(storage, "activate_version")
    assert not hasattr(storage, "activate_compiled_snapshot")

    staged = storage.promote_draft_to_staging_candidate(
        scope,
        draft.draft_id,
        reason="ready for human review",
    )

    assert staged.storage_status == "staging_candidate"
    assert staged.activation_enabled is False
    assert staged.activation_eligibility is False

    with pytest.raises(RecipePackStorageError, match="draft -> staging_candidate"):
        storage.promote_draft_to_staging_candidate(
            scope,
            draft.draft_id,
            reason="repeat transition is not allowed",
        )

    with pytest.raises(RecipePackStorageError, match="cannot overwrite"):
        storage.save_draft(scope, draft)

    history = storage.list_promotion_history(scope, draft.draft_id)
    assert len(history) == 1
    assert history[0].from_status == "draft"
    assert history[0].to_status == "staging_candidate"
    assert history[0].activation_enabled is False


def test_versions_snapshots_eval_and_approvals_are_refs_only_and_activation_disabled() -> None:
    storage = LocalRecipePackStorage()
    scope = _session()
    draft = _draft()
    draft_record = storage.save_draft(scope, draft)
    storage.promote_draft_to_staging_candidate(
        scope,
        draft.draft_id,
        reason="ready for version candidate",
    )

    version_record = storage.save_version(scope, _version(draft_record.draft_digest))
    snapshot = storage.save_compiled_snapshot_ref(
        scope,
        CompiledSnapshotRef(
            refId="compiled-snapshot-ref-001",
            packId=draft.pack.pack_id,
            version="v1",
            sourceDraftId=draft.draft_id,
            compiledSnapshotDigest="sha256:" + "b" * 64,
            status="compiled",
        ),
    )
    eval_ref = storage.save_eval_result_ref(
        scope,
        EvalResultRef(
            evalResultId="eval-result-001",
            draftId=draft.draft_id,
            resultDigest="sha256:" + "c" * 64,
            artifactRef="eval.result.source-review.001",
        ),
    )
    approval_ref = storage.save_approval_ref(
        scope,
        RecipePackApprovalRef(
            approvalRefId="approval-ref-001",
            draftId=draft.draft_id,
            authorityRef="authority:owner-human@1",
            approvalDigest="sha256:" + "d" * 64,
            status="required",
        ),
    )

    assert version_record.activation_enabled is False
    assert snapshot.snapshot_kind == "compiled_snapshot"
    assert snapshot.activation_enabled is False
    assert eval_ref.local_only is True
    assert approval_ref.activation_enabled is False
    assert storage.read_version(scope, draft.pack.pack_id, "v1") == version_record
    assert storage.read_compiled_snapshot_ref(scope, snapshot.ref_id) == snapshot
    assert storage.read_eval_result_ref(scope, eval_ref.eval_result_id) == eval_ref
    assert storage.read_approval_ref(scope, approval_ref.approval_ref_id) == approval_ref
    assert storage.list_versions(scope) == (version_record,)
    assert storage.list_compiled_snapshot_refs(scope) == (snapshot,)
    assert storage.list_eval_result_refs(scope, draft.draft_id) == (eval_ref,)
    assert storage.list_approval_refs(scope, draft.draft_id) == (approval_ref,)

    other_scope = {
        "botId": "bot_recipe_builder_mode_other",
        "ownerId": scope.owner_id,
        "sessionId": scope.session_id,
        "mode": "recipe_builder",
    }
    assert storage.read_version(other_scope, draft.pack.pack_id, "v1") is None
    assert storage.read_compiled_snapshot_ref(other_scope, snapshot.ref_id) is None
    assert storage.read_eval_result_ref(other_scope, eval_ref.eval_result_id) is None
    assert storage.read_approval_ref(other_scope, approval_ref.approval_ref_id) is None

    with pytest.raises(ValidationError, match="status cannot be active"):
        CompiledSnapshotRef(
            refId="compiled-snapshot-ref-active",
            packId=draft.pack.pack_id,
            version="v1",
            sourceDraftId=draft.draft_id,
            compiledSnapshotDigest="sha256:" + "e" * 64,
            status="active",
        )


def test_compiled_snapshot_ref_must_match_version_source_draft_provenance() -> None:
    storage = LocalRecipePackStorage()
    scope = _session()
    draft_a = _draft(draftId="draft.source-review.snapshot-a")
    draft_b = _draft(draftId="draft.source-review.snapshot-b")
    record_a = storage.save_draft(scope, draft_a)
    storage.save_draft(scope, draft_b)
    storage.promote_draft_to_staging_candidate(
        scope,
        draft_a.draft_id,
        reason="stage draft A for version",
    )
    storage.promote_draft_to_staging_candidate(
        scope,
        draft_b.draft_id,
        reason="stage draft B independently",
    )
    storage.save_version(
        scope,
        RecipePackVersion(
            packId=draft_a.pack.pack_id,
            version="v-provenance",
            sourceDraftId=draft_a.draft_id,
            status="candidate",
            sourceDigest=record_a.draft_digest,
        ),
    )

    with pytest.raises(RecipePackStorageError, match="sourceDraftId"):
        storage.save_compiled_snapshot_ref(
            scope,
            CompiledSnapshotRef(
                refId="compiled-snapshot-ref-wrong-source",
                packId=draft_a.pack.pack_id,
                version="v-provenance",
                sourceDraftId=draft_b.draft_id,
                compiledSnapshotDigest="sha256:" + "5" * 64,
                status="compiled",
            ),
        )


def test_generated_plugin_proposals_are_artifact_refs_only_and_non_executable() -> None:
    storage = LocalRecipePackStorage()
    scope = _session()
    draft = _draft(
        generatedPluginProposals=(
            GeneratedPluginProposal(
                proposalId="proposal.source-review-helper",
                status="proposed",
                name="Source review helper",
                reason="Could help review source metadata after human review.",
            ).model_dump(by_alias=True),
        )
    )

    storage.save_draft(scope, draft)
    stored = storage.save_generated_plugin_proposal_artifact_ref(
        scope,
        GeneratedPluginProposalArtifactRef(
            proposalId="proposal.source-review-helper",
            draftId=draft.draft_id,
            artifactRef="proposal.source-review-helper.bundle-metadata",
            artifactDigest="sha256:" + "f" * 64,
        ),
    )

    assert stored.executable is False
    assert stored.activation_enabled is False
    assert stored.runtime_entrypoint is None
    assert "code" not in stored.model_dump(by_alias=True)
    assert storage.read_generated_plugin_proposal_artifact_ref(
        scope,
        stored.proposal_id,
    ) == stored
    assert storage.list_generated_plugin_proposal_artifact_refs(
        scope,
        draft.draft_id,
    ) == (stored,)

    other_scope = {
        "botId": "bot_recipe_builder_mode_other",
        "ownerId": scope.owner_id,
        "sessionId": scope.session_id,
        "mode": "recipe_builder",
    }
    assert storage.read_generated_plugin_proposal_artifact_ref(
        other_scope,
        stored.proposal_id,
    ) is None

    with pytest.raises(ValidationError, match="extra_forbidden"):
        GeneratedPluginProposalArtifactRef.model_validate(
            {
                "proposalId": "proposal.source-review-helper",
                "draftId": draft.draft_id,
                "artifactRef": "proposal.source-review-helper.bundle-metadata",
                "artifactDigest": "sha256:" + "1" * 64,
                "generatedCode": "print('not allowed')",
            }
        )

    with pytest.raises(ValidationError, match="executable"):
        GeneratedPluginProposalArtifactRef(
            proposalId="proposal.source-review-helper",
            draftId=draft.draft_id,
            artifactRef="proposal.source-review-helper.bundle-metadata",
            artifactDigest="sha256:" + "2" * 64,
            executable=True,
        )


def test_disable_and_delete_semantics_are_explicit_tombstones() -> None:
    storage = LocalRecipePackStorage()
    scope = _session()
    first = _draft(draftId="draft.source-review.disable")
    second = _draft(draftId="draft.source-review.delete")

    storage.save_draft(scope, first)
    storage.save_draft(scope, second)

    disabled = storage.disable_draft(
        scope,
        first.draft_id,
        reason="superseded by newer proposal",
    )
    deleted = storage.delete_draft(
        scope,
        second.draft_id,
        reason="authoring session discarded",
    )

    assert disabled.disabled is True
    assert disabled.deleted is False
    assert disabled.storage_status == "disabled"
    assert deleted.deleted is True
    assert deleted.storage_status == "deleted"
    assert storage.read_draft(scope, first.draft_id) is None
    assert storage.read_draft(scope, first.draft_id, include_disabled=True) == disabled
    assert storage.read_draft(scope, second.draft_id) is None
    assert storage.read_draft(scope, second.draft_id, include_deleted=True) == deleted
    assert storage.list_drafts(scope) == ()
    assert storage.list_drafts(scope, include_disabled=True, include_deleted=True) == (
        disabled,
        deleted,
    )
