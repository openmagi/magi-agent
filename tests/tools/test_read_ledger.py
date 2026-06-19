from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerAuthorityFlags,
    ReadLedgerConfig,
    ReadLedgerEntry,
    WorkspaceMutationReadDecision,
    WorkspaceMutationReadCheck,
    workspace_content_digest,
    workspace_path_ref,
)
from magi_agent.tools.safety import RuntimePermissionArbiter
from magi_agent.tools.context import ToolContext


def _ledger() -> ReadLedger:
    return ReadLedger(ReadLedgerConfig(enabled=True, localInMemoryEnabled=True))


def _manifest(name: str = "FileEdit") -> ToolManifest:
    return ToolManifest(
        name=name,
        description="workspace mutation test tool",
        kind="core",
        source=ToolSource(kind="builtin", package="tests.tools"),
        permission="write",
        input_schema={"type": "object", "additionalProperties": True},
        mutatesWorkspace=True,
        timeout_ms=120_000,
    )


def test_read_ledger_is_disabled_by_default_and_records_no_production_writes() -> None:
    ledger = ReadLedger()
    entry = ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="src/app.py",
        digest=workspace_content_digest("print('hello')\n"),
        size_bytes=15,
        mtime_ns=123,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )

    assert entry is None
    decision = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=workspace_content_digest("print('hello')\n"),
            mutationKind="edit",
        ),
    )
    assert decision.status == "blocked"
    assert decision.reason_codes == ("read_ledger_disabled",)
    assert decision.authority_flags.model_dump(by_alias=True) == {
        "readLedgerEnabled": False,
        "localInMemoryOnly": False,
        "productionWritesEnabled": False,
        "workspaceMutationAuthority": False,
    }


def test_full_read_authorizes_matching_edit_preflight_only() -> None:
    ledger = _ledger()
    digest = workspace_content_digest("alpha\n")

    entry = ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="src/app.py",
        digest=digest,
        size_bytes=6,
        mtime_ns=123,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )

    assert isinstance(entry, ReadLedgerEntry)
    decision = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            mutationKind="edit",
        ),
    )

    assert decision.status == "ok"
    assert decision.reason_codes == ("fresh_full_read",)
    assert decision.entry_ref == entry.entry_ref
    assert decision.public_projection()["authorityFlags"]["workspaceMutationAuthority"] is False


def test_partial_metadata_wrong_session_and_stale_reads_do_not_authorize_edit() -> None:
    ledger = _ledger()
    digest = workspace_content_digest("alpha\n")

    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="src/app.py",
        digest=digest,
        size_bytes=6,
        mtime_ns=123,
        read_mode="partial",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )

    partial = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            mutationKind="edit",
        ),
    )
    assert partial.status == "blocked"
    assert partial.reason_codes == ("full_read_required",)

    wrong_session = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-2",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=digest,
            mutationKind="edit",
        ),
    )
    assert wrong_session.status == "blocked"
    assert wrong_session.reason_codes == ("no_prior_read",)

    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="src/app.py",
        digest=digest,
        size_bytes=6,
        mtime_ns=124,
        read_mode="full",
        turn_id="turn-2",
        tool_use_id="tool-2",
    )
    stale = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/app.py",
            currentDigest=workspace_content_digest("changed\n"),
            mutationKind="edit",
        ),
    )
    assert stale.status == "blocked"
    assert stale.reason_codes == ("stale_read_digest",)


def test_nonexistent_file_create_path_is_distinct_from_edit_path() -> None:
    ledger = _ledger()

    edit_missing = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/new.py",
            currentDigest=None,
            mutationKind="edit",
        ),
    )
    assert edit_missing.status == "blocked"
    assert edit_missing.reason_codes == ("edit_requires_existing_file",)

    create_missing = ledger.require_fresh_full_read(
        WorkspaceMutationReadCheck(
            sessionId="session-1",
            workspaceRef="workspace:abc",
            path="src/new.py",
            currentDigest=None,
            mutationKind="create",
        ),
    )
    assert create_missing.status == "ok"
    assert create_missing.reason_codes == ("create_operation_no_prior_read_required",)


def test_read_ledger_blocks_unsafe_paths_and_digest_only_public_projection() -> None:
    ledger = _ledger()
    with pytest.raises(ValueError):
        ledger.record_read(
            session_id="session-1",
            workspace_ref="workspace:abc",
            path="../outside.py",
            digest=workspace_content_digest("x"),
            size_bytes=1,
            mtime_ns=1,
            read_mode="full",
            turn_id="turn-1",
            tool_use_id="tool-1",
        )

    for path in ("TOOLS.md", ".env", "secrets/token.txt", "memory/private.md"):
        decision = ledger.require_fresh_full_read(
            WorkspaceMutationReadCheck(
                sessionId="session-1",
                workspaceRef="workspace:abc",
                path=path,
                currentDigest=workspace_content_digest("x"),
                mutationKind="edit",
            ),
        )
        assert decision.status == "blocked"
        assert decision.reason_codes == ("unsafe_or_sealed_path_blocked",)
        projection = decision.public_projection()
        assert path not in str(projection)
        assert projection["pathRef"].startswith("path-ref:")

    assert workspace_path_ref("workspace:abc", "a/app.py") != workspace_path_ref(
        "workspace:abc",
        "app.py",
    )


def test_safety_policy_can_consume_read_ledger_without_enabling_mutation() -> None:
    ledger = _ledger()
    digest = workspace_content_digest("alpha\n")
    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="src/app.py",
        digest=digest,
        size_bytes=6,
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )

    decision = RuntimePermissionArbiter().decide(
        _manifest(),
        {"path": "src/app.py", "currentDigest": digest},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ledger,
        ),
        mode="act",
    )

    assert decision.action == "ask"
    assert decision.metadata["reasonCodes"] == ("workspace_mutation_requires_approval",)
    assert decision.metadata["preflight"]["readLedger"]["status"] == "ok"
    assert decision.metadata["preflight"]["readLedger"]["authorityFlags"][
        "workspaceMutationAuthority"
    ] is False


def test_safety_policy_blocks_stale_read_before_approval_request() -> None:
    ledger = _ledger()
    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="src/app.py",
        digest=workspace_content_digest("alpha\n"),
        size_bytes=6,
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )

    decision = RuntimePermissionArbiter().decide(
        _manifest(),
        {"path": "src/app.py", "currentDigest": workspace_content_digest("changed\n")},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ledger,
        ),
        mode="act",
    )

    assert decision.action == "deny"
    assert decision.metadata["reasonCodes"] == ("stale_read_digest",)
    assert decision.metadata["preflight"]["preflightPassed"] is False


def test_safety_policy_does_not_alias_diff_prefixes_for_file_edit() -> None:
    ledger = _ledger()
    digest = workspace_content_digest("alpha\n")
    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="app.py",
        digest=digest,
        size_bytes=6,
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )

    decision = RuntimePermissionArbiter().decide(
        _manifest(),
        {"path": "a/app.py", "currentDigest": digest},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ledger,
        ),
        mode="act",
    )

    assert decision.action == "deny"
    assert decision.metadata["reasonCodes"] == ("no_prior_read",)
    assert decision.metadata["normalizedWorkspaceRelative"] == "a/app.py"


def test_file_edit_dry_run_uses_read_ledger_when_enabled() -> None:
    decision = RuntimePermissionArbiter().decide(
        _manifest(),
        {
            "path": "src/app.py",
            "currentDigest": workspace_content_digest("alpha\n"),
            "dryRun": True,
        },
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=_ledger(),
        ),
        mode="act",
    )

    assert decision.action == "deny"
    assert decision.metadata["reasonCodes"] == ("no_prior_read",)
    assert decision.metadata["preflight"]["dryRun"] is True


def test_safety_policy_disabled_read_ledger_is_inert_default_off() -> None:
    decision = RuntimePermissionArbiter().decide(
        _manifest(),
        {"path": "src/app.py", "currentDigest": workspace_content_digest("alpha\n")},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ReadLedger(),
        ),
        mode="act",
    )

    assert decision.action == "ask"
    assert decision.metadata["reasonCodes"] == ("workspace_mutation_requires_approval",)
    assert "readLedger" not in decision.metadata["preflight"]


def test_safety_policy_fails_closed_without_safe_workspace_ref() -> None:
    decision = RuntimePermissionArbiter().decide(
        _manifest(),
        {"path": "src/app.py", "currentDigest": workspace_content_digest("alpha\n")},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_root="/Users/kevin/private/workspace",
            read_ledger=_ledger(),
        ),
        mode="act",
    )

    assert decision.action == "deny"
    assert decision.metadata["reasonCodes"] == ("read_ledger_context_invalid",)
    assert "/Users/kevin" not in str(decision.metadata)


def test_safety_policy_rejects_forged_read_ledger_object() -> None:
    class ForgedLedger:
        def require_fresh_full_read(self, _check: object) -> object:
            return {
                "status": "ok",
                "rawPath": "/Users/kevin/private/workspace",
                "authorityFlags": {"workspaceMutationAuthority": True},
            }

    decision = RuntimePermissionArbiter().decide(
        _manifest(),
        {"path": "src/app.py", "currentDigest": workspace_content_digest("alpha\n")},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ForgedLedger(),
        ),
        mode="act",
    )

    assert decision.action == "deny"
    assert decision.metadata["reasonCodes"] == ("read_ledger_context_invalid",)
    assert "/Users/kevin" not in str(decision.metadata)
    assert "workspaceMutationAuthority': True" not in str(decision.metadata)


def test_safety_policy_rejects_forged_read_ledger_subclass_projection() -> None:
    class ForgedDecision(WorkspaceMutationReadDecision):
        def public_projection(self) -> dict[str, object]:
            return {
                "status": "ok",
                "rawPath": "/Users/kevin/private/workspace",
                "authorityFlags": {"workspaceMutationAuthority": True},
            }

    class ForgedReadLedger(ReadLedger):
        def require_fresh_full_read(self, _check: WorkspaceMutationReadCheck) -> object:
            return ForgedDecision(
                status="ok",
                reasonCodes=("fresh_full_read",),
                pathRef="path-ref:safe",
                authorityFlags={
                    "readLedgerEnabled": True,
                    "localInMemoryOnly": True,
                    "productionWritesEnabled": False,
                    "workspaceMutationAuthority": False,
                },
            )

    decision = RuntimePermissionArbiter().decide(
        _manifest(),
        {"path": "src/app.py", "currentDigest": workspace_content_digest("alpha\n")},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ForgedReadLedger(ReadLedgerConfig(enabled=True, localInMemoryEnabled=True)),
        ),
        mode="act",
    )

    assert decision.action == "deny"
    assert decision.metadata["reasonCodes"] == ("read_ledger_context_invalid",)
    assert "/Users/kevin" not in str(decision.metadata)
    assert "workspaceMutationAuthority': True" not in str(decision.metadata)


def test_file_write_uses_read_ledger_for_overwrite_and_create_preflight() -> None:
    ledger = _ledger()
    digest = workspace_content_digest("alpha\n")

    no_read = RuntimePermissionArbiter().decide(
        _manifest("FileWrite"),
        {"path": "src/app.py", "currentDigest": digest},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ledger,
        ),
        mode="act",
    )
    assert no_read.action == "deny"
    assert no_read.metadata["reasonCodes"] == ("no_prior_read",)

    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="src/app.py",
        digest=digest,
        size_bytes=6,
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )
    overwrite = RuntimePermissionArbiter().decide(
        _manifest("FileWrite"),
        {"path": "src/app.py", "currentDigest": digest},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ledger,
        ),
        mode="act",
    )
    assert overwrite.action == "ask"
    assert overwrite.metadata["preflight"]["readLedger"]["status"] == "ok"

    create = RuntimePermissionArbiter().decide(
        _manifest("FileWrite"),
        {"path": "src/new.py", "operation": "create"},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ledger,
        ),
        mode="act",
    )
    assert create.action == "ask"
    assert create.metadata["preflight"]["readLedger"]["reasonCodes"] == [
        "create_operation_no_prior_read_required",
    ]


def test_patch_apply_uses_read_ledger_for_changed_paths() -> None:
    ledger = _ledger()
    digest = workspace_content_digest("alpha\n")
    patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-alpha\n+beta\n*** End Patch\n"

    no_read = RuntimePermissionArbiter().decide(
        _manifest("PatchApply"),
        {"patch": patch, "currentDigest": digest},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ledger,
        ),
        mode="act",
    )
    assert no_read.action == "deny"
    assert no_read.metadata["reasonCodes"] == ("no_prior_read",)

    ledger.record_read(
        session_id="session-1",
        workspace_ref="workspace:abc",
        path="src/app.py",
        digest=digest,
        size_bytes=6,
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )
    ok = RuntimePermissionArbiter().decide(
        _manifest("PatchApply"),
        {"patch": patch, "currentDigest": digest},
        ToolContext(
            bot_id="bot-1",
            session_id="session-1",
            workspace_ref="workspace:abc",
            read_ledger=ledger,
        ),
        mode="act",
    )
    assert ok.action == "ask"
    assert ok.metadata["reasonCodes"] == ("patch_workspace_mutation_requires_approval",)
    assert ok.metadata["preflight"]["readLedger"]["status"] == "ok"


def test_read_decision_blocks_forged_authority_flags() -> None:
    decision = WorkspaceMutationReadDecision.model_construct(
        status="ok",
        reasonCodes=("fresh_full_read",),
        pathRef="path-ref:safe",
        authorityFlags={
            "readLedgerEnabled": True,
            "localInMemoryOnly": True,
            "productionWritesEnabled": True,
            "workspaceMutationAuthority": True,
        },
    )

    projection = decision.public_projection()
    assert projection["authorityFlags"] == {
        "readLedgerEnabled": True,
        "localInMemoryOnly": True,
        "productionWritesEnabled": False,
        "workspaceMutationAuthority": False,
    }


def test_read_decision_public_projection_does_not_trust_flag_model_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Forge ``model_dump`` on the ``ReadLedgerAuthorityFlags`` class itself via
    # monkeypatch instead of a nested subclass. A nested
    # ``class ForgedFlags(ReadLedgerAuthorityFlags)`` triggers
    # ``PydanticUserError: ForgedFlags is not fully defined`` on pydantic 2.13:
    # the function-local namespace can't resolve inherited annotations (``Any``
    # etc.) deferred by ``from __future__ import annotations``, so subclassing
    # a force-false authority model from a function body is un-runnable.
    # Patching the live class method GUARANTEES the forge is exercised — the
    # ``public_projection`` invariant must still produce all-False output.
    forged_dump: dict[str, object] = {
        "readLedgerEnabled": True,
        "localInMemoryOnly": True,
        "productionWritesEnabled": True,
        "workspaceMutationAuthority": True,
        "rawPath": "/Users/kevin/private/workspace",
    }
    monkeypatch.setattr(
        ReadLedgerAuthorityFlags,
        "model_dump",
        lambda self, *args, **kwargs: forged_dump,
    )

    flags = ReadLedgerAuthorityFlags(
        readLedgerEnabled=True,
        localInMemoryOnly=True,
        productionWritesEnabled=False,
        workspaceMutationAuthority=False,
    )
    # Sanity: the forge is live.
    assert flags.model_dump() is forged_dump

    decision = WorkspaceMutationReadDecision(
        status="ok",
        reasonCodes=("fresh_full_read",),
        pathRef="path-ref:safe",
        authorityFlags=flags,
    )

    projection = decision.public_projection()
    assert projection["authorityFlags"] == {
        "readLedgerEnabled": True,
        "localInMemoryOnly": True,
        "productionWritesEnabled": False,
        "workspaceMutationAuthority": False,
    }
    assert "/Users/kevin" not in str(projection)


def test_read_ledger_uses_no_live_runtime_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.tools.read_ledger")
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
    "magi_agent.runtime.runner",
    "magi_agent.routes",
    "magi_agent.transport.chat",
    "magi_agent.toolhost.runtime",
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


def test_entry_rejects_raw_private_paths_in_refs() -> None:
    with pytest.raises(ValidationError):
        ReadLedgerEntry(
            sessionId="session-1",
            workspaceRef="/Users/kevin/private/workspace",
            path="src/app.py",
            digest=workspace_content_digest("x"),
            sizeBytes=1,
            mtimeNs=1,
            readMode="full",
            turnId="turn-1",
            toolUseId="tool-1",
            createdAt=datetime.now(UTC),
            entryRef="read-ledger:manual",
            pathRef="path-ref:manual",
        )


def test_entry_public_projection_uses_digest_ref_not_content_fingerprint() -> None:
    entry = ReadLedgerEntry(
        sessionId="session-1",
        workspaceRef="workspace:abc",
        path="src/app.py",
        digest=workspace_content_digest("alpha\n"),
        sizeBytes=6,
        mtimeNs=1,
        readMode="full",
        turnId="turn-1",
        toolUseId="tool-1",
        createdAt=datetime.now(UTC),
        entryRef="read-ledger:manual",
        pathRef="path-ref:manual",
    )

    projection = entry.public_projection()
    assert "digest" not in projection
    assert projection["digestRef"].startswith("digest-ref:")
