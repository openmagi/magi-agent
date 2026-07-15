from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from magi_agent.execution_authority.workspace_writer import (
    DurableLocalWorkspaceJournal,
    LocalWorkspaceLeaseManager,
    MutationEntry,
    MutationOperation,
    ProofContext,
    WorkspaceExecutionToken,
    WorkspaceWriter,
)


class _FixedClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class _TokenVerifier:
    def verify(self, token: WorkspaceExecutionToken) -> bool:
        return token.token_id == "token-1"


def _proof_context() -> ProofContext:
    return ProofContext(
        task_contract_id="task-1",
        task_version=1,
        task_contract_digest="sha256:" + "1" * 64,
        completion_epoch_id="epoch-1",
        evidence_id="evidence-1",
        evidence_digest="sha256:" + "2" * 64,
        evidence_root="sha256:" + "3" * 64,
        producer_id="workspace-reader",
        producer_version="1.0.0",
        producer_liveness="live",
    )


def _writer(tmp_path: Path) -> tuple[WorkspaceWriter, WorkspaceExecutionToken]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    authority = tmp_path / ".workspace-authority"
    journal = DurableLocalWorkspaceJournal(authority / "journal")
    leases = LocalWorkspaceLeaseManager(authority / "leases")
    clock = _FixedClock()
    writer = WorkspaceWriter(
        workspace_root=workspace,
        authority_root=authority,
        journal=journal,
        leases=leases,
        clock=clock,
        token_verifier=_TokenVerifier(),
    )
    leases.set_current_fence(writer.workspace_ref, 1)
    token = WorkspaceExecutionToken(
        token_id="token-1",
        action_id="action-1",
        attempt_id="attempt-1",
        workspace_ref=writer.workspace_ref,
        fencing_token=1,
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
    )
    return writer, token


def test_stage_materializes_complete_plan_without_touching_live_targets(
    tmp_path: Path,
) -> None:
    writer, token = _writer(tmp_path)
    target = writer.workspace_root / "a.txt"
    target.write_bytes(b"before")
    writer.refresh_initial_state_for_tests()
    proof = writer.observe_read("a.txt", context=_proof_context())
    plan = writer.build_plan(
        transaction_id="txn-1",
        action_id="action-1",
        attempt_id="attempt-1",
        entries=(
            MutationEntry(
                operation=MutationOperation.REPLACE_FILE,
                resource_ref=proof.resource_ref,
                after_content=b"after",
                proof_digest=proof.proof_digest,
            ),
        ),
        read_proofs=(proof,),
        absence_proofs=(),
    )

    staged = writer.stage(plan=plan, execution_token=token)

    assert target.read_bytes() == b"before"
    assert staged.manifest_path.is_file()
    assert staged.entries[0].staged_path is not None
    assert staged.entries[0].staged_path.read_bytes() == b"after"
    assert staged.entries[0].resource_ref == proof.resource_ref
    assert staged.manifest_digest.startswith("sha256:")
    assert staged.fencing_token == 1
