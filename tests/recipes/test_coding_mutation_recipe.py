from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.recipes.coding_mutation import (
    CodingMutationConfig,
    CodingMutationMaterialization,
    CodingMutationRecipe,
    CodingMutationRequest,
    materialize_coding_mutation_recipe,
)
from openmagi_core_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerConfig,
    workspace_content_digest,
)


def _ledger() -> ReadLedger:
    return ReadLedger(ReadLedgerConfig(enabled=True, localInMemoryEnabled=True))


def _recipe(
    *,
    enabled: bool = True,
    local_fake: bool = False,
    ledger: ReadLedger | None = None,
) -> CodingMutationRecipe:
    return CodingMutationRecipe(
        CodingMutationConfig(enabled=enabled, localFakeApplyEnabled=local_fake),
        read_ledger=ledger,
    )


def _record_read(
    ledger: ReadLedger,
    *,
    path: str = "src/app.py",
    content: str = "alpha\n",
) -> str:
    digest = workspace_content_digest(content)
    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path=path,
        digest=digest,
        size_bytes=len(content.encode("utf-8")),
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )
    return digest


def _edit_request(
    *,
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
        sessionId="session-1",
        workspaceRef="workspace:abc",
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
    content: str = "hello\n",
    explicit_approval: bool = False,
) -> CodingMutationRequest:
    return CodingMutationRequest(
        toolName="FileWrite",
        sessionId="session-1",
        workspaceRef="workspace:abc",
        path=path,
        currentDigest=None,
        newString=content,
        mutationKind="create",
        explicitApproval=explicit_approval,
    )


def _patch_request() -> CodingMutationRequest:
    return CodingMutationRequest(
        toolName="PatchApply",
        sessionId="session-1",
        workspaceRef="workspace:abc",
        path="src/app.py",
        currentDigest=workspace_content_digest("alpha\n"),
        mutationKind="patch",
        patch="*** Begin Patch\n*** Update File: src/app.py\n@@\n-alpha\n+beta\n*** End Patch\n",
    )


def test_coding_mutation_recipe_is_disabled_by_default() -> None:
    decision = CodingMutationRecipe().evaluate(_edit_request())

    assert decision.status == "disabled"
    assert decision.reason_codes == ("coding_mutation_recipe_disabled",)
    assert decision.public_projection()["authorityFlags"] == {
        "recipeEnabled": False,
        "localFakeApplyEnabled": False,
        "filesystemWriteAttempted": False,
        "productionWorkspaceMutationEnabled": False,
        "liveToolAttached": False,
        "routeAttached": False,
        "userVisibleOutputAllowed": False,
    }


def test_file_edit_requires_prior_fresh_full_read() -> None:
    decision = _recipe(ledger=_ledger()).evaluate(_edit_request())

    assert decision.status == "blocked"
    assert decision.reason_codes == ("no_prior_read",)
    assert decision.read_ledger["status"] == "blocked"


def test_file_edit_rejects_stale_read_digest() -> None:
    ledger = _ledger()
    _record_read(ledger, content="alpha\n")

    decision = _recipe(ledger=ledger).evaluate(
        _edit_request(current_text="changed\n", old_string="changed", new_string="next"),
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("stale_read_digest",)


def test_file_edit_rejects_current_text_digest_mismatch() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="alpha\n")

    decision = _recipe(ledger=ledger).evaluate(
        _edit_request(
            current_digest=digest,
            current_text="beta\n",
            old_string="beta",
            new_string="gamma",
        ),
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("current_text_digest_mismatch",)


def test_file_edit_rejects_malformed_current_digest() -> None:
    with pytest.raises(ValidationError):
        _edit_request(current_digest="sha256:" + ("z" * 64))


def test_file_edit_receipt_ref_is_bound_to_digest_only_mutation_payload() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="alpha\n")
    recipe = _recipe(ledger=ledger)

    beta = recipe.evaluate(
        _edit_request(
            current_digest=digest,
            current_text="alpha\n",
            new_string="beta",
        ),
    )
    gamma = recipe.evaluate(
        _edit_request(
            current_digest=digest,
            current_text="alpha\n",
            new_string="gamma",
        ),
    )

    assert beta.receipt_ref != gamma.receipt_ref
    assert "beta" not in beta.receipt_ref
    assert "gamma" not in gamma.receipt_ref


def test_tool_name_and_mutation_kind_must_match() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="alpha\n")
    recipe = _recipe(ledger=ledger)

    edit_create = recipe.evaluate(
        CodingMutationRequest(
            toolName="FileEdit",
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            currentText="alpha\n",
            oldString="alpha",
            newString="beta",
            mutationKind="create",
        ),
    )
    assert edit_create.status == "blocked"
    assert edit_create.reason_codes == ("mutation_kind_tool_mismatch",)

    write_edit = recipe.evaluate(
        CodingMutationRequest(
            toolName="FileWrite",
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            newString="new\n",
            mutationKind="edit",
        ),
    )
    assert write_edit.status == "blocked"
    assert write_edit.reason_codes == ("mutation_kind_tool_mismatch",)

    patch_edit = recipe.evaluate(
        CodingMutationRequest(
            toolName="PatchApply",
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            mutationKind="edit",
            patch="*** Begin Patch\n*** End Patch\n",
        ),
    )
    assert patch_edit.status == "blocked"
    assert patch_edit.reason_codes == ("mutation_kind_tool_mismatch",)


def test_file_edit_exact_single_match_returns_approval_required_without_writing() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="alpha\n")

    decision = _recipe(ledger=ledger).evaluate(
        _edit_request(current_digest=digest, current_text="alpha\n"),
    )

    assert decision.status == "approval_required"
    assert decision.reason_codes == ("coding_mutation_requires_explicit_approval",)
    assert decision.diff_summary == {
        "changedFiles": 1,
        "replacements": 1,
        "oldDigestRef": decision.old_digest_ref,
        "newDigestRef": decision.new_digest_ref,
    }
    projection = decision.public_projection()
    assert "alpha" not in str(projection)
    assert "beta" not in str(projection)
    assert projection["authorityFlags"]["filesystemWriteAttempted"] is False


def test_file_edit_local_fake_apply_requires_explicit_approval() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="alpha\n")

    unapproved = _recipe(ledger=ledger, local_fake=True).evaluate(
        _edit_request(current_digest=digest, current_text="alpha\n"),
    )
    assert unapproved.status == "approval_required"

    approved = _recipe(ledger=ledger, local_fake=True).evaluate(
        _edit_request(
            current_digest=digest,
            current_text="alpha\n",
            explicit_approval=True,
        ),
    )
    assert approved.status == "applied_local_fake"
    assert approved.reason_codes == ("local_fake_mutation_receipt_only",)
    assert approved.public_projection()["authorityFlags"]["filesystemWriteAttempted"] is False


def test_file_write_create_is_approval_gated_and_local_fake_receipt_only() -> None:
    recipe = _recipe(local_fake=True, ledger=_ledger())

    unapproved = recipe.evaluate(_write_request())
    assert unapproved.status == "approval_required"
    assert unapproved.reason_codes == ("coding_mutation_requires_explicit_approval",)
    assert unapproved.diff_summary["createdFiles"] == 1

    approved = recipe.evaluate(_write_request(explicit_approval=True))
    assert approved.status == "applied_local_fake"
    assert approved.reason_codes == ("local_fake_mutation_receipt_only",)
    projection = approved.public_projection()
    assert "hello" not in str(projection)
    assert projection["authorityFlags"]["filesystemWriteAttempted"] is False


def test_file_write_overwrite_uses_read_ledger_before_approval() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, path="src/app.py", content="old\n")

    decision = _recipe(ledger=ledger).evaluate(
        CodingMutationRequest(
            toolName="FileWrite",
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            newString="new\n",
            mutationKind="replace",
        )
    )

    assert decision.status == "approval_required"
    assert decision.reason_codes == ("coding_mutation_requires_explicit_approval",)
    assert decision.read_ledger["status"] == "ok"


def test_patch_apply_requires_read_ledger_receipt() -> None:
    decision = _recipe(ledger=_ledger()).evaluate(_patch_request())

    assert decision.status == "blocked"
    assert decision.reason_codes == ("no_prior_read",)
    assert decision.public_projection()["authorityFlags"]["filesystemWriteAttempted"] is False


def test_file_edit_rejects_missing_multi_match_and_noop_edits() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="alpha\nalpha\n")
    recipe = _recipe(ledger=ledger)

    no_match = recipe.evaluate(
        _edit_request(
            current_digest=digest,
            current_text="alpha\nalpha\n",
            old_string="missing",
            new_string="beta",
        ),
    )
    assert no_match.status == "blocked"
    assert no_match.reason_codes == ("no_match",)

    empty_old = recipe.evaluate(
        _edit_request(
            current_digest=digest,
            current_text="alpha\nalpha\n",
            old_string="",
            new_string="beta",
            replace_all=True,
        ),
    )
    assert empty_old.status == "blocked"
    assert empty_old.reason_codes == ("old_string_required",)

    multi = recipe.evaluate(
        _edit_request(
            current_digest=digest,
            current_text="alpha\nalpha\n",
            old_string="alpha",
            new_string="beta",
        ),
    )
    assert multi.status == "blocked"
    assert multi.reason_codes == ("multiple_matches",)

    replace_all = recipe.evaluate(
        _edit_request(
            current_digest=digest,
            current_text="alpha\nalpha\n",
            old_string="alpha",
            new_string="beta",
            replace_all=True,
        ),
    )
    assert replace_all.status == "approval_required"
    assert replace_all.diff_summary["replacements"] == 2

    noop = recipe.evaluate(
        _edit_request(
            current_digest=digest,
            current_text="alpha\nalpha\n",
            old_string="alpha",
            new_string="alpha",
            replace_all=True,
        ),
    )
    assert noop.status == "blocked"
    assert noop.reason_codes == ("no_op_edit",)


def test_file_edit_blocks_sealed_secret_and_escaped_paths() -> None:
    ledger = _ledger()
    for path in ("TOOLS.md", ".env", "secrets/token.txt"):
        decision = _recipe(ledger=ledger).evaluate(
            _edit_request(path=path, current_text="alpha\n"),
        )
        assert decision.status == "blocked"
        assert decision.reason_codes == ("unsafe_or_sealed_path_blocked",)
        assert path not in str(decision.public_projection())

    with pytest.raises(ValidationError):
        _edit_request(path="../outside.py")


def test_recipe_blocks_raw_content_and_forged_authority_in_projection() -> None:
    ledger = _ledger()
    digest = _record_read(ledger, content="token=secret\n")
    decision = _recipe(ledger=ledger).evaluate(
        _edit_request(
            current_digest=digest,
            current_text="token=secret\n",
            old_string="token=secret",
            new_string="token=redacted",
        ),
    ).model_copy(
        update={
            "authorityFlags": {
                "filesystemWriteAttempted": True,
                "productionWorkspaceMutationEnabled": True,
                "liveToolAttached": True,
                "routeAttached": True,
                "userVisibleOutputAllowed": True,
            }
        }
    )

    projection = decision.public_projection()
    assert "token=secret" not in str(projection)
    assert "token=redacted" not in str(projection)
    assert projection["authorityFlags"]["filesystemWriteAttempted"] is False
    assert projection["authorityFlags"]["productionWorkspaceMutationEnabled"] is False
    assert projection["authorityFlags"]["liveToolAttached"] is False


def test_coding_mutation_recipe_materializes_default_off_without_live_attachment() -> None:
    materialization = materialize_coding_mutation_recipe()

    assert isinstance(materialization, CodingMutationMaterialization)
    assert materialization.recipe_id == "openmagi.dev-coding.mutation"
    assert materialization.tool_names == ("FileWrite", "FileEdit", "PatchApply")
    assert materialization.ledger_required is True
    assert materialization.attachment_flags == {
        "liveToolAttached": False,
        "filesystemWriteAttempted": False,
        "productionWorkspaceMutationEnabled": False,
        "routeAttached": False,
        "userVisibleOutputAllowed": False,
    }
    assert materialization.public_projection()["attachmentFlags"]["liveToolAttached"] is False


def test_coding_mutation_recipe_has_no_live_runtime_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.recipes.coding_mutation")
forbidden = (
    "subprocess",
    "git",
    "fastapi",
    "kubernetes",
    "supabase",
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.models",
    "google.genai",
    "openmagi_core_agent.runtime.runner",
    "openmagi_core_agent.toolhost.runtime",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
