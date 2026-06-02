from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from pydantic import ValidationError

from magi_agent.recipes.coding_mutation import (
    CodingMutationConfig,
    CodingMutationRecipe,
    CodingMutationRequest,
    materialize_coding_mutation_recipe,
)
from magi_agent.tools import core_tool_manifests
from magi_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerConfig,
    ReadMode,
    WorkspaceMutationReadCheck,
    workspace_content_digest,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/coding_harness_consolidated_matrix.json"
PR2_TEST_REF = "tests/test_coding_read_before_edit_contracts.py"


def _ledger() -> ReadLedger:
    return ReadLedger(ReadLedgerConfig(enabled=True, localInMemoryEnabled=True))


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


def _write_request(
    *,
    path: str = "src/new.py",
    content: str = "created\n",
    current_digest: str | None = None,
    mutation_kind: Literal["create", "replace"] = "create",
    explicit_approval: bool = False,
) -> CodingMutationRequest:
    return CodingMutationRequest(
        toolName="FileWrite",
        sessionId="session-1",
        workspaceRef="workspace:abc",
        path=path,
        currentDigest=current_digest,
        newString=content,
        mutationKind=mutation_kind,
        explicitApproval=explicit_approval,
    )


def _enabled_recipe(
    ledger: ReadLedger,
    *,
    local_fake: bool = False,
) -> CodingMutationRecipe:
    return CodingMutationRecipe(
        CodingMutationConfig(enabled=True, localFakeApplyEnabled=local_fake),
        read_ledger=ledger,
    )


def _pr2_matrix_row() -> dict[str, object]:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    return {row["id"]: row for row in matrix["rows"]}["read_before_edit_and_stale_rejection"]


def test_full_read_authorizes_only_same_session_workspace_path_and_digest_preflight() -> None:
    ledger = _ledger()
    digest = _record_read(ledger)

    ok = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            mutationKind="edit",
        ),
    )
    assert ok.status == "ok"
    assert ok.reason_codes == ("fresh_full_read",)
    assert ok.public_projection()["authorityFlags"]["workspaceMutationAuthority"] is False

    wrong_path = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/other.py",
            currentDigest=digest,
            mutationKind="edit",
        ),
    )
    wrong_workspace = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:def",
            path="src/app.py",
            currentDigest=digest,
            mutationKind="edit",
        ),
    )
    wrong_session = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-2",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            mutationKind="edit",
        ),
    )

    assert wrong_path.status == "blocked"
    assert wrong_workspace.status == "blocked"
    assert wrong_session.status == "blocked"
    assert wrong_path.reason_codes == ("no_prior_read",)
    assert wrong_workspace.reason_codes == ("no_prior_read",)
    assert wrong_session.reason_codes == ("no_prior_read",)


@pytest.mark.parametrize("read_mode", ["partial", "metadata"])
def test_partial_and_metadata_reads_never_authorize_edit(read_mode: str) -> None:
    ledger = _ledger()
    digest = _record_read(ledger, read_mode=read_mode)

    decision = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            mutationKind="edit",
        ),
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("full_read_required",)
    assert decision.public_projection()["authorityFlags"]["workspaceMutationAuthority"] is False


def test_stale_digest_blocks_and_projection_uses_refs_without_raw_content_or_private_paths() -> None:
    ledger = _ledger()
    _record_read(ledger, content="token=secret\n")
    decision = _enabled_recipe(ledger).evaluate(
        _edit_request(
            current_text="changed-token=secret\n",
            old_string="changed",
            new_string="updated",
        ),
    )

    projection = decision.public_projection()
    read_ledger = cast(dict[str, Any], projection["readLedger"])
    projection_text = str(projection)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("stale_read_digest",)
    assert read_ledger["status"] == "blocked"
    assert "token=secret" not in projection_text
    assert "changed-token=secret" not in projection_text
    assert "/Users/kevin" not in projection_text
    assert str(projection["pathRef"]).startswith("path-ref:")
    assert str(read_ledger["digestRef"]).startswith("digest-ref:")


def test_mutation_receipts_are_blocked_approval_or_local_fake_without_live_authority() -> None:
    ledger = _ledger()
    digest = _record_read(ledger)
    recipe = _enabled_recipe(ledger, local_fake=True)

    blocked = recipe.evaluate(
        _edit_request(current_digest=digest, old_string="missing", new_string="beta"),
    )
    approval = recipe.evaluate(_edit_request(current_digest=digest))
    applied = recipe.evaluate(_edit_request(current_digest=digest, explicit_approval=True))

    assert blocked.status == "blocked"
    assert approval.status == "approval_required"
    assert applied.status == "applied_local_fake"
    for decision in (blocked, approval, applied):
        projection = decision.public_projection()
        flags = cast(dict[str, bool], projection["authorityFlags"])
        assert flags["filesystemWriteAttempted"] is False
        assert flags["liveToolAttached"] is False
        assert flags["routeAttached"] is False
        assert flags["userVisibleOutputAllowed"] is False
        assert flags["productionWorkspaceMutationEnabled"] is False


def test_multimatch_noop_sealed_external_and_traversal_paths_are_denied() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="alpha\nalpha\n")
    recipe = _enabled_recipe(ledger)

    multi = recipe.evaluate(
        _edit_request(
            current_text="alpha\nalpha\n",
            current_digest=digest,
            old_string="alpha",
            new_string="beta",
        ),
    )
    noop = recipe.evaluate(
        _edit_request(
            current_text="alpha\nalpha\n",
            current_digest=digest,
            old_string="alpha",
            new_string="alpha",
            replace_all=True,
        ),
    )
    sealed = recipe.evaluate(_edit_request(path="TOOLS.md"))
    secret = recipe.evaluate(_edit_request(path=".env.local"))

    assert multi.status == "blocked"
    assert multi.reason_codes == ("multiple_matches",)
    assert noop.status == "blocked"
    assert noop.reason_codes == ("no_op_edit",)
    assert sealed.status == "blocked"
    assert sealed.reason_codes == ("unsafe_or_sealed_path_blocked",)
    assert secret.status == "blocked"
    assert secret.reason_codes == ("unsafe_or_sealed_path_blocked",)
    with pytest.raises(ValidationError):
        _edit_request(path="../outside.py")
    with pytest.raises(ValidationError):
        _edit_request(path="/tmp/outside.py")


def test_file_write_create_and_replace_are_receipt_only_and_read_checked_when_replacing() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="old\n")
    recipe = _enabled_recipe(ledger, local_fake=True)

    create = recipe.evaluate(_write_request(explicit_approval=True))
    replace = recipe.evaluate(
        _write_request(
            path="src/app.py",
            content="new\n",
            current_digest=digest,
            mutation_kind="replace",
            explicit_approval=True,
        ),
    )
    stale_replace = recipe.evaluate(
        _write_request(
            path="src/app.py",
            content="new\n",
            current_digest=workspace_content_digest("stale\n"),
            mutation_kind="replace",
        ),
    )

    assert create.status == "applied_local_fake"
    assert create.read_ledger is None
    assert replace.status == "applied_local_fake"
    assert replace.read_ledger["status"] == "ok"  # type: ignore[index]
    assert stale_replace.status == "blocked"
    assert stale_replace.reason_codes == ("stale_read_digest",)
    for decision in (create, replace, stale_replace):
        assert decision.public_projection()["authorityFlags"]["filesystemWriteAttempted"] is False


def test_mutation_materialization_is_default_off_and_exposes_file_tool_metadata_only() -> None:
    materialization = materialize_coding_mutation_recipe()
    manifests = {manifest.name: manifest for manifest in core_tool_manifests()}

    assert materialization.tool_names == ("FileWrite", "FileEdit", "PatchApply")
    assert materialization.ledger_required is True
    assert materialization.public_projection()["attachmentFlags"] == {
        "filesystemWriteAttempted": False,
        "liveToolAttached": False,
        "productionWorkspaceMutationEnabled": False,
        "routeAttached": False,
        "userVisibleOutputAllowed": False,
    }
    for name in ("FileRead", "FileEdit", "FileWrite"):
        manifest = manifests[name]
        assert manifest.kind == "core"
        assert manifest.adk_tool_type == "FunctionTool"
        assert manifest.enabled_by_default is False
        assert manifest.input_schema == {"type": "object", "additionalProperties": True}
    assert manifests["FileRead"].mutates_workspace is False
    assert manifests["FileEdit"].mutates_workspace is True
    assert manifests["FileWrite"].mutates_workspace is True


def test_pr2_matrix_row_records_gap_test_and_no_live_authority_contract() -> None:
    row = _pr2_matrix_row()

    assert PR2_TEST_REF in row["coveredByTests"]
    assert row["missingImplementation"] == ["complete"]
    assert row["defaultOff"] is True
    assert row["liveAuthorityAllowed"] is False
    assert row["coreTouchAllowed"] is False
