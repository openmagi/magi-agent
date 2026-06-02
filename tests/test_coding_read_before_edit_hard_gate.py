"""PR4: Read Ledger And Stale Edit Hard Gates.

Tests that FileEdit and PatchApply require a prior read receipt for the same
relative path digest.  Stale read versions reject mutation.  Concurrent
read-only actions remain allowed.  Public projection does not reveal raw file
paths or file contents.
"""
from __future__ import annotations

from typing import Any, cast

import pytest

from openmagi_core_agent.recipes.coding_mutation import (
    CodingMutationConfig,
    CodingMutationRecipe,
    CodingMutationRequest,
)
from openmagi_core_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerConfig,
    ReadMode,
    WorkspaceMutationReadCheck,
    workspace_content_digest,
)
from openmagi_core_agent.workspace.read_ledger import (
    ReadLedgerHardGate,
    ReadLedgerHardGateConfig,
    ReadLedgerHardGateDecision,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ledger(*, enabled: bool = True) -> ReadLedger:
    return ReadLedger(ReadLedgerConfig(enabled=enabled, localInMemoryEnabled=enabled))


def _hard_gate(
    ledger: ReadLedger | None = None,
    *,
    enabled: bool = True,
) -> ReadLedgerHardGate:
    return ReadLedgerHardGate(
        ReadLedgerHardGateConfig(enabled=enabled),
        read_ledger=ledger or _ledger(enabled=enabled),
    )


def _record_read(
    ledger: ReadLedger,
    *,
    session_id: str = "session-1",
    workspace_ref: str = "workspace:abc",
    path: str = "src/app.py",
    content: str = "alpha\n",
    read_mode: ReadMode = "full",
) -> str:
    digest = workspace_content_digest(content)
    ledger.record_read(
        session_id=session_id,
        workspace_ref=workspace_ref,
        path=path,
        digest=digest,
        size_bytes=len(content.encode("utf-8")),
        mtime_ns=1,
        read_mode=read_mode,
        turn_id="turn-1",
        tool_use_id="tool-1",
    )
    return digest


def _edit_request(
    *,
    session_id: str = "session-1",
    workspace_ref: str = "workspace:abc",
    path: str = "src/app.py",
    current_text: str = "alpha\n",
    old_string: str = "alpha",
    new_string: str = "beta",
    current_digest: str | None = None,
    replace_all: bool = False,
    explicit_approval: bool = False,
) -> CodingMutationRequest:
    return CodingMutationRequest(
        toolName="FileEdit",
        sessionId=session_id,
        workspaceRef=workspace_ref,
        path=path,
        currentDigest=current_digest or workspace_content_digest(current_text),
        currentText=current_text,
        oldString=old_string,
        newString=new_string,
        replaceAll=replace_all,
        explicitApproval=explicit_approval,
    )


def _patch_request(
    *,
    session_id: str = "session-1",
    workspace_ref: str = "workspace:abc",
    path: str = "src/app.py",
    current_digest: str | None = None,
    patch: str = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-alpha\n+beta\n",
    explicit_approval: bool = False,
) -> CodingMutationRequest:
    return CodingMutationRequest(
        toolName="PatchApply",
        sessionId=session_id,
        workspaceRef=workspace_ref,
        path=path,
        currentDigest=current_digest,
        patch=patch,
        mutationKind="patch",
        explicitApproval=explicit_approval,
    )


def _recipe(
    ledger: ReadLedger,
    *,
    local_fake: bool = False,
) -> CodingMutationRecipe:
    return CodingMutationRecipe(
        CodingMutationConfig(enabled=True, localFakeApplyEnabled=local_fake),
        read_ledger=ledger,
    )


# ---------------------------------------------------------------------------
# Hard Gate unit tests
# ---------------------------------------------------------------------------


class TestReadLedgerHardGateConfig:
    def test_default_off(self) -> None:
        cfg = ReadLedgerHardGateConfig()
        assert cfg.enabled is False
        assert cfg.production_workspace_mutation_allowed is False

    def test_production_mutation_always_false(self) -> None:
        with pytest.raises(Exception):
            ReadLedgerHardGateConfig(
                enabled=True,
                productionWorkspaceMutationAllowed=True,  # type: ignore[call-arg]
            )


class TestHardGateMissingReadReceipt:
    """FileEdit and PatchApply must be blocked when there is no prior read."""

    def test_file_edit_blocked_without_prior_read(self) -> None:
        ledger = _ledger()
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=workspace_content_digest("alpha\n"),
        )
        assert decision.status == "blocked"
        assert "no_prior_read" in decision.reason_codes

    def test_patch_apply_blocked_without_prior_read(self) -> None:
        ledger = _ledger()
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="PatchApply",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=workspace_content_digest("alpha\n"),
        )
        assert decision.status == "blocked"
        assert "no_prior_read" in decision.reason_codes


class TestHardGateStaleReadReceipt:
    """Stale read versions must reject mutation."""

    def test_file_edit_blocked_on_stale_digest(self) -> None:
        ledger = _ledger()
        _record_read(ledger, content="old-content\n")
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=workspace_content_digest("new-content\n"),
        )
        assert decision.status == "blocked"
        assert "stale_read_digest" in decision.reason_codes

    def test_patch_apply_blocked_on_stale_digest(self) -> None:
        ledger = _ledger()
        _record_read(ledger, content="old-content\n")
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="PatchApply",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=workspace_content_digest("new-content\n"),
        )
        assert decision.status == "blocked"
        assert "stale_read_digest" in decision.reason_codes


class TestHardGateMismatchedPathDigest:
    """Read receipt for path X does not authorize editing path Y."""

    def test_file_edit_wrong_path_blocked(self) -> None:
        ledger = _ledger()
        digest = _record_read(ledger, path="src/app.py")
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/other.py",
            current_digest=digest,
        )
        assert decision.status == "blocked"
        assert "no_prior_read" in decision.reason_codes

    def test_patch_apply_wrong_path_blocked(self) -> None:
        ledger = _ledger()
        digest = _record_read(ledger, path="src/app.py")
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="PatchApply",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/other.py",
            current_digest=digest,
        )
        assert decision.status == "blocked"
        assert "no_prior_read" in decision.reason_codes


class TestHardGateValidCurrentRead:
    """Valid, current read receipt authorizes mutation."""

    def test_file_edit_allowed_with_fresh_read(self) -> None:
        ledger = _ledger()
        digest = _record_read(ledger)
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=digest,
        )
        assert decision.status == "ok"
        assert "fresh_full_read" in decision.reason_codes

    def test_patch_apply_allowed_with_fresh_read(self) -> None:
        ledger = _ledger()
        digest = _record_read(ledger)
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="PatchApply",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=digest,
        )
        assert decision.status == "ok"
        assert "fresh_full_read" in decision.reason_codes


class TestConcurrentReadOnlyAllowed:
    """Concurrent read-only actions remain allowed."""

    def test_multiple_reads_same_file_no_conflict(self) -> None:
        ledger = _ledger()
        digest1 = _record_read(ledger, session_id="session-1")
        digest2 = _record_read(ledger, session_id="session-2")
        assert digest1 == digest2

        gate = _hard_gate(ledger)
        d1 = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=digest1,
        )
        d2 = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-2",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=digest2,
        )
        assert d1.status == "ok"
        assert d2.status == "ok"

    def test_read_does_not_block_another_session_read(self) -> None:
        ledger = _ledger()
        _record_read(ledger, session_id="session-1")
        _record_read(ledger, session_id="session-2")
        # Both sessions have read receipts, both should be able to check
        gate = _hard_gate(ledger)
        for sid in ("session-1", "session-2"):
            decision = gate.check_mutation(
                tool_name="FileEdit",
                session_id=sid,
                workspace_ref="workspace:abc",
                path="src/app.py",
                current_digest=workspace_content_digest("alpha\n"),
            )
            assert decision.status == "ok"


class TestPublicProjectionSafety:
    """Public projection does not reveal raw file paths or file contents."""

    def test_projection_hides_raw_paths(self) -> None:
        ledger = _ledger()
        _record_read(ledger, content="secret_token=abc123\n")
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=workspace_content_digest("different\n"),
        )
        projection = decision.public_projection()
        projection_text = str(projection)
        assert "secret_token=abc123" not in projection_text
        assert "/Users/" not in projection_text
        assert decision.status == "blocked"

    def test_ok_projection_has_refs_not_paths(self) -> None:
        ledger = _ledger()
        digest = _record_read(ledger)
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=digest,
        )
        projection = decision.public_projection()
        assert projection["status"] == "ok"
        assert str(projection["pathRef"]).startswith("path-ref:")
        assert projection["productionWorkspaceMutationAllowed"] is False


class TestHardGateDefaultOff:
    """Hard gate is default-off."""

    def test_disabled_gate_passes_through(self) -> None:
        ledger = _ledger(enabled=False)
        gate = _hard_gate(ledger, enabled=False)
        decision = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=workspace_content_digest("alpha\n"),
        )
        assert decision.status == "disabled"
        assert "hard_gate_disabled" in decision.reason_codes


class TestPartialReadNeverAuthorizesMutation:
    """Partial or metadata reads cannot authorize edit/patch."""

    @pytest.mark.parametrize("read_mode", ["partial", "metadata"])
    def test_partial_read_blocks_edit(self, read_mode: str) -> None:
        ledger = _ledger()
        digest = _record_read(ledger, read_mode=read_mode)  # type: ignore[arg-type]
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="FileEdit",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=digest,
        )
        assert decision.status == "blocked"
        assert "full_read_required" in decision.reason_codes

    @pytest.mark.parametrize("read_mode", ["partial", "metadata"])
    def test_partial_read_blocks_patch(self, read_mode: str) -> None:
        ledger = _ledger()
        digest = _record_read(ledger, read_mode=read_mode)  # type: ignore[arg-type]
        gate = _hard_gate(ledger)
        decision = gate.check_mutation(
            tool_name="PatchApply",
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="src/app.py",
            current_digest=digest,
        )
        assert decision.status == "blocked"
        assert "full_read_required" in decision.reason_codes


class TestPatchApplyThroughCodingMutationRecipe:
    """PatchApply should be gated by read ledger via CodingMutationRecipe."""

    def test_patch_apply_blocked_without_read_receipt(self) -> None:
        ledger = _ledger()
        recipe = _recipe(ledger)
        decision = recipe.evaluate(
            _patch_request(current_digest=workspace_content_digest("alpha\n")),
        )
        assert decision.status == "blocked"
        assert "no_prior_read" in decision.reason_codes

    def test_patch_apply_blocked_with_stale_digest(self) -> None:
        ledger = _ledger()
        _record_read(ledger, content="old\n")
        recipe = _recipe(ledger)
        decision = recipe.evaluate(
            _patch_request(current_digest=workspace_content_digest("new\n")),
        )
        assert decision.status == "blocked"
        assert "stale_read_digest" in decision.reason_codes

    def test_patch_apply_allowed_with_fresh_read_and_approval(self) -> None:
        ledger = _ledger()
        digest = _record_read(ledger)
        recipe = _recipe(ledger, local_fake=True)
        decision = recipe.evaluate(
            _patch_request(current_digest=digest, explicit_approval=True),
        )
        assert decision.status == "applied_local_fake"
        projection = decision.public_projection()
        assert projection["authorityFlags"]["filesystemWriteAttempted"] is False
        assert projection["authorityFlags"]["productionWorkspaceMutationEnabled"] is False
