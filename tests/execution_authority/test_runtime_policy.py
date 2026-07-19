from __future__ import annotations

from magi_agent.tools.execution_integrity import (
    ExecutionIntegrityBoundary,
    unclosed_execution_attempts,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.read_ledger import ReadLedger, ReadLedgerConfig
from magi_agent.tools.result import ToolResult
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.registry import ToolRegistry
import asyncio


def _manifest(*, mutates: bool = True) -> ToolManifest:
    return ToolManifest(
        name="FileEdit" if mutates else "FileRead",
        description="test",
        kind="core",
        source=ToolSource(kind="builtin", package="tests"),
        permission="write" if mutates else "read",
        inputSchema={"type": "object", "additionalProperties": True},
        mutatesWorkspace=mutates,
        sideEffectClass="local_workspace" if mutates else "none",
        timeoutMs=1_000,
    )


def _context(*, ledger: object | None = None, tool_use_id: str = "call-1") -> ToolContext:
    return ToolContext(
        botId="bot",
        sessionId="session",
        turnId="turn",
        toolUseId=tool_use_id,
        workspaceRef="workspace:test",
        readLedger=ledger,
    )


def _grant(boundary, manifest, arguments, context, *, read_ok: bool):
    metadata = {"preflight": {"readLedger": {"status": "ok"}}} if read_ok else {}
    return boundary.issue_grant(manifest, arguments, context, permission_metadata=metadata)


def test_off_and_readonly_paths_do_not_initialize_authority_db(tmp_path, monkeypatch) -> None:
    db = tmp_path / "authority.db"
    monkeypatch.setenv("MAGI_EXECUTION_AUTHORITY_DB", str(db))
    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "off")
    boundary = ExecutionIntegrityBoundary()

    assert boundary.preflight(_manifest(), {"path": "a.py"}, _context()).blocked is False
    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "enforce")
    assert (
        boundary.preflight(_manifest(mutates=False), {"path": "a.py"}, _context()).blocked is False
    )
    assert not db.exists()


def test_audit_records_missing_read_ledger_but_allows_dispatch(tmp_path, monkeypatch) -> None:
    db = tmp_path / "authority.db"
    monkeypatch.setenv("MAGI_EXECUTION_AUTHORITY_DB", str(db))
    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "audit")
    boundary = ExecutionIntegrityBoundary()

    manifest = _manifest()
    arguments = {"path": "a.py"}
    context = _context()
    decision = boundary.preflight(
        manifest,
        arguments,
        context,
        grant=_grant(boundary, manifest, arguments, context, read_ok=False),
    )
    assert decision.blocked is False
    assert "fresh_full_read_not_authorized" in decision.reason_codes
    assert db.exists()

    result = boundary.observe(
        decision,
        ToolResult(status="ok", output="done"),
    )
    assert result.metadata["executionIntegrity"]["mode"] == "audit"
    assert result.metadata["executionIntegrity"]["requestDigest"].startswith("sha256:")


def test_enforce_blocks_missing_read_ledger_and_replayed_attempt(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EXECUTION_AUTHORITY_DB", str(tmp_path / "authority.db"))
    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "enforce")
    boundary = ExecutionIntegrityBoundary()

    manifest = _manifest()
    arguments = {"path": "a.py"}
    missing_context = _context()
    missing = boundary.preflight(
        manifest,
        arguments,
        missing_context,
        grant=_grant(boundary, manifest, arguments, missing_context, read_ok=False),
    )
    assert missing.blocked is True
    assert missing.error_code == "execution_integrity_read_required"

    ledger = ReadLedger(ReadLedgerConfig(enabled=True, localInMemoryEnabled=True))
    context = _context(ledger=ledger)
    first = boundary.preflight(
        manifest,
        arguments,
        context,
        grant=_grant(boundary, manifest, arguments, context, read_ok=True),
    )
    assert first.blocked is False
    restarted = ExecutionIntegrityBoundary()
    replay = restarted.preflight(
        manifest,
        arguments,
        context,
        grant=_grant(restarted, manifest, arguments, context, read_ok=True),
    )
    assert replay.blocked is True
    assert replay.error_code == "execution_integrity_authority_consumed"

    identity_context = _context(ledger=ledger).model_copy(update={"tool_use_id": None})
    missing_identity = boundary.preflight(
        manifest,
        arguments,
        identity_context,
        grant=_grant(boundary, manifest, arguments, identity_context, read_ok=True),
    )
    assert missing_identity.blocked is True
    assert missing_identity.error_code == "execution_integrity_identity_required"


def test_enforce_requires_grant_bound_to_exact_arguments(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EXECUTION_AUTHORITY_DB", str(tmp_path / "authority.db"))
    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "enforce")
    boundary = ExecutionIntegrityBoundary()
    manifest = _manifest()
    context = _context()
    grant = _grant(boundary, manifest, {"path": "a.py"}, context, read_ok=True)

    missing = boundary.preflight(manifest, {"path": "a.py"}, context)
    rebound = boundary.preflight(manifest, {"path": "other.py"}, context, grant=grant)
    assert missing.error_code == "execution_integrity_authority_required"
    assert rebound.error_code == "execution_integrity_authority_required"


def test_denied_admission_does_not_create_an_unclosed_attempt(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EXECUTION_AUTHORITY_DB", str(tmp_path / "authority.db"))
    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "enforce")
    boundary = ExecutionIntegrityBoundary()

    denied = boundary.preflight(_manifest(), {"path": "a.py"}, _context())

    assert denied.blocked is True
    assert unclosed_execution_attempts("session", "turn") == ()


def test_corrupt_journal_fails_closed_only_in_enforce(tmp_path, monkeypatch) -> None:
    db = tmp_path / "authority.db"
    db.write_text("not sqlite", encoding="utf-8")
    monkeypatch.setenv("MAGI_EXECUTION_AUTHORITY_DB", str(db))
    boundary = ExecutionIntegrityBoundary()

    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "audit")
    manifest = _manifest()
    arguments = {"path": "a.py"}
    context = _context()
    audited = boundary.preflight(
        manifest,
        arguments,
        context,
        grant=_grant(boundary, manifest, arguments, context, read_ok=False),
    )
    assert audited.blocked is False
    assert "authority_journal_unavailable" in audited.reason_codes

    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "enforce")
    enforcing = ExecutionIntegrityBoundary()
    enforcing_context = _context(ledger=object())
    blocked = enforcing.preflight(
        manifest,
        arguments,
        enforcing_context,
        grant=_grant(enforcing, manifest, arguments, enforcing_context, read_ok=True),
    )
    assert blocked.blocked is True
    assert blocked.error_code == "execution_integrity_journal_unavailable"


def test_dispatcher_skips_handler_on_integrity_block_and_observes_success() -> None:
    calls: list[str] = []

    class Boundary:
        def __init__(self, *, blocked: bool) -> None:
            self.blocked = blocked

        def issue_grant(self, manifest, arguments, context, *, permission_metadata):
            return object()

        def preflight(self, manifest, arguments, context, *, grant):
            return type(
                "Decision",
                (),
                {
                    "blocked": self.blocked,
                    "error_code": "integrity_block" if self.blocked else None,
                    "reason_codes": ("blocked",) if self.blocked else (),
                    "mode": "enforce",
                },
            )()

        def observe(self, decision, result):
            calls.append("observe")
            return result

        def observe_exception(self, decision, exception):
            calls.append(f"exception:{type(exception).__name__}")

    registry = ToolRegistry()
    manifest = _manifest(mutates=False).model_copy(update={"enabled_by_default": True})

    def handler(arguments, context):
        calls.append("handler")
        return ToolResult(status="ok")

    registry.register(manifest, handler=handler)
    blocked_dispatcher = ToolDispatcher(
        registry, execution_integrity_boundary=Boundary(blocked=True)
    )
    blocked = asyncio.run(blocked_dispatcher.dispatch(manifest.name, {}, _context(), mode="act"))
    assert blocked.status == "blocked"
    assert blocked.error_code == "integrity_block"
    assert calls == []

    allowed_dispatcher = ToolDispatcher(
        registry, execution_integrity_boundary=Boundary(blocked=False)
    )
    allowed = asyncio.run(allowed_dispatcher.dispatch(manifest.name, {}, _context(), mode="act"))
    assert allowed.status == "ok"
    assert calls == ["handler", "observe"]


def test_dispatcher_observes_handler_exception_before_reraising() -> None:
    calls: list[str] = []

    class Boundary:
        def issue_grant(self, manifest, arguments, context, *, permission_metadata):
            return object()

        def preflight(self, manifest, arguments, context, *, grant):
            return type(
                "Decision",
                (),
                {"blocked": False, "error_code": None, "reason_codes": (), "mode": "audit"},
            )()

        def observe_exception(self, decision, exception):
            calls.append(type(exception).__name__)

    registry = ToolRegistry()
    manifest = _manifest(mutates=False).model_copy(update={"enabled_by_default": True})

    def handler(arguments, context):
        raise RuntimeError("boom")

    registry.register(manifest, handler=handler)
    dispatcher = ToolDispatcher(registry, execution_integrity_boundary=Boundary())

    try:
        asyncio.run(dispatcher.dispatch(manifest.name, {}, _context(), mode="act"))
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:  # pragma: no cover - explicit assertion keeps the exception contract visible
        raise AssertionError("handler exception was not reraised")
    assert calls == ["RuntimeError"]


def test_completion_closure_reports_open_then_observed_attempt(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EXECUTION_AUTHORITY_DB", str(tmp_path / "authority.db"))
    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "audit")
    boundary = ExecutionIntegrityBoundary()
    manifest = _manifest()
    arguments = {"path": "a.py"}
    context = _context()
    decision = boundary.preflight(
        manifest,
        arguments,
        context,
        grant=_grant(boundary, manifest, arguments, context, read_ok=True),
    )

    assert unclosed_execution_attempts("session", "turn") == (decision.attempt_key,)
    boundary.observe(decision, ToolResult(status="ok"))
    assert unclosed_execution_attempts("session", "turn") == ()


def test_handler_exception_closes_consumed_attempt(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EXECUTION_AUTHORITY_DB", str(tmp_path / "authority.db"))
    monkeypatch.setenv("MAGI_EXECUTION_INTEGRITY_MODE", "audit")
    boundary = ExecutionIntegrityBoundary()
    manifest = _manifest()
    arguments = {"path": "a.py"}
    context = _context()
    decision = boundary.preflight(
        manifest,
        arguments,
        context,
        grant=_grant(boundary, manifest, arguments, context, read_ok=True),
    )
    boundary.observe_exception(decision, RuntimeError("boom"))
    assert unclosed_execution_attempts("session", "turn") == ()
