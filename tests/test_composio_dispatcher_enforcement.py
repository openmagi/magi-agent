from __future__ import annotations

import asyncio

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers — fakes mirroring the ADK McpToolset / McpTool surface used at
# runtime, without importing google.adk (kept network/dependency free).
# ---------------------------------------------------------------------------


class FakeMcpTool:
    """Minimal ADK-tool stand-in: a name + async run_async(args, tool_context)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, object]] = []

    async def run_async(self, *, args: dict[str, object], tool_context: object = None):
        self.calls.append(dict(args))
        return {"status": "ok", "tool": self.name, "echo": dict(args)}


class FakeMcpToolset:
    """Minimal ADK-toolset stand-in exposing async get_tools()."""

    def __init__(self, tools: list[FakeMcpTool]) -> None:
        self._tools = tools
        self.tool_name_prefix = "composio"

    async def get_tools(self, readonly_context: object = None) -> list[FakeMcpTool]:
        return list(self._tools)


class FakeAgent:
    def __init__(self) -> None:
        self.tools: list[object] = []


class FakeRunner:
    def __init__(self) -> None:
        self.agent = FakeAgent()


def _ctx() -> ToolContext:
    return ToolContext(
        botId="bot-1",
        sessionId="sess-1",
        turnId="turn-1",
        workspaceRef="ws-1",
    )


# ---------------------------------------------------------------------------
# env flag
# ---------------------------------------------------------------------------


def test_composio_dispatch_enforced_defaults_off() -> None:
    from magi_agent.config.env import composio_dispatch_enforced

    assert composio_dispatch_enforced({}) is False
    assert composio_dispatch_enforced({"MAGI_COMPOSIO_DISPATCH_ENFORCED": "off"}) is False


def test_composio_dispatch_enforced_strict_truthy_opt_in() -> None:
    from magi_agent.config.env import composio_dispatch_enforced

    for value in ("1", "true", "yes", "on"):
        assert composio_dispatch_enforced({"MAGI_COMPOSIO_DISPATCH_ENFORCED": value}) is True
    for value in ("0", "false", "no", "maybe", ""):
        assert composio_dispatch_enforced({"MAGI_COMPOSIO_DISPATCH_ENFORCED": value}) is False


# ---------------------------------------------------------------------------
# arbiter hard-safety for manifest-less external MCP tool calls
# ---------------------------------------------------------------------------


def test_arbiter_denies_secret_path_for_external_mcp_call() -> None:
    from magi_agent.tools.safety import RuntimePermissionArbiter

    arbiter = RuntimePermissionArbiter()
    decision = arbiter.decide_external_mcp_call(
        "composio-GMAIL_SEND",
        {"path": ".env"},
        _ctx(),
        mode="act",
    )
    assert decision.action == "deny"
    # secret path must be redacted in the public preview
    assert ".env" not in str(decision.metadata)


def test_arbiter_denies_sealed_path_for_external_mcp_call() -> None:
    from magi_agent.tools.safety import RuntimePermissionArbiter

    arbiter = RuntimePermissionArbiter()
    decision = arbiter.decide_external_mcp_call(
        "composio-FILE_WRITE",
        {"file": "SOUL.md"},
        _ctx(),
        mode="act",
    )
    assert decision.action == "deny"


def test_arbiter_denies_workspace_escape_for_external_mcp_call() -> None:
    from magi_agent.tools.safety import RuntimePermissionArbiter

    arbiter = RuntimePermissionArbiter()
    decision = arbiter.decide_external_mcp_call(
        "composio-FILE_WRITE",
        {"target": "../../etc/passwd"},
        _ctx(),
        mode="act",
    )
    assert decision.action == "deny"


def test_arbiter_allows_safe_path_for_external_mcp_call() -> None:
    from magi_agent.tools.safety import RuntimePermissionArbiter

    arbiter = RuntimePermissionArbiter()
    decision = arbiter.decide_external_mcp_call(
        "composio-GMAIL_SEND",
        {"to": "x@example.com", "path": "drafts/note.md"},
        _ctx(),
        mode="act",
    )
    assert decision.action == "allow"


def test_arbiter_allows_external_mcp_call_without_path_args() -> None:
    from magi_agent.tools.safety import RuntimePermissionArbiter

    arbiter = RuntimePermissionArbiter()
    decision = arbiter.decide_external_mcp_call(
        "composio-GMAIL_SEND",
        {"to": "x@example.com", "subject": "hi"},
        _ctx(),
        mode="act",
    )
    assert decision.action == "allow"


# ---------------------------------------------------------------------------
# dispatcher-aware composio attach
# ---------------------------------------------------------------------------


def test_dispatcher_attach_blocks_secret_path_composio_call() -> None:
    from magi_agent.composio.mcp import (
        ComposioToolsetBundle,
        attach_composio_toolsets_through_dispatcher,
    )
    from magi_agent.tools.safety import RuntimePermissionArbiter

    inner_tool = FakeMcpTool("composio-FILE_WRITE")
    toolset = FakeMcpToolset([inner_tool])
    bundle = ComposioToolsetBundle(active=True, status="ready", toolsets=(toolset,))
    runner = FakeRunner()

    attached = attach_composio_toolsets_through_dispatcher(
        runner,
        bundle,
        arbiter=RuntimePermissionArbiter(),
        mode="act",
        context_factory=lambda **_kw: _ctx(),
    )
    assert attached is True

    guarded_toolset = runner.agent.tools[0]
    guarded_tools = asyncio.run(guarded_toolset.get_tools())
    guarded = guarded_tools[0]

    result = asyncio.run(
        guarded.run_async(args={"path": ".env"}, tool_context=None)
    )
    # Hard-safety deny → tool body must NOT have executed.
    assert inner_tool.calls == []
    assert result.get("status") == "blocked"


def test_dispatcher_attach_allows_safe_composio_call_through() -> None:
    from magi_agent.composio.mcp import (
        ComposioToolsetBundle,
        attach_composio_toolsets_through_dispatcher,
    )
    from magi_agent.tools.safety import RuntimePermissionArbiter

    inner_tool = FakeMcpTool("composio-GMAIL_SEND")
    toolset = FakeMcpToolset([inner_tool])
    bundle = ComposioToolsetBundle(active=True, status="ready", toolsets=(toolset,))
    runner = FakeRunner()

    attach_composio_toolsets_through_dispatcher(
        runner,
        bundle,
        arbiter=RuntimePermissionArbiter(),
        mode="act",
        context_factory=lambda **_kw: _ctx(),
    )
    guarded = asyncio.run(runner.agent.tools[0].get_tools())[0]

    result = asyncio.run(
        guarded.run_async(args={"to": "a@b.com"}, tool_context=None)
    )
    assert inner_tool.calls == [{"to": "a@b.com"}]
    assert result.get("status") == "ok"


# ---------------------------------------------------------------------------
# receipt 적재 — guarded composio calls must record a dispatcher-style receipt
# (completion definition, plan 09 PR2: prove receipt recording by test)
# ---------------------------------------------------------------------------


def test_guarded_composio_call_records_allow_receipt() -> None:
    from magi_agent.composio.mcp import (
        ComposioReceiptLedger,
        ComposioToolsetBundle,
        attach_composio_toolsets_through_dispatcher,
    )
    from magi_agent.tools.safety import RuntimePermissionArbiter

    inner_tool = FakeMcpTool("composio-GMAIL_SEND")
    toolset = FakeMcpToolset([inner_tool])
    bundle = ComposioToolsetBundle(active=True, status="ready", toolsets=(toolset,))
    runner = FakeRunner()
    ledger = ComposioReceiptLedger()

    attach_composio_toolsets_through_dispatcher(
        runner,
        bundle,
        arbiter=RuntimePermissionArbiter(),
        mode="act",
        context_factory=lambda **_kw: _ctx(),
        receipt_ledger=ledger,
    )
    guarded = asyncio.run(runner.agent.tools[0].get_tools())[0]

    asyncio.run(guarded.run_async(args={"to": "a@b.com"}, tool_context=None))

    receipts = ledger.receipts()
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.tool == "composio-GMAIL_SEND"
    assert receipt.action == "allow"
    # payload digest must be present and must NOT leak the raw argument value.
    assert receipt.payload_digest
    assert "a@b.com" not in receipt.payload_digest


def test_guarded_composio_call_records_deny_receipt() -> None:
    from magi_agent.composio.mcp import (
        ComposioReceiptLedger,
        ComposioToolsetBundle,
        attach_composio_toolsets_through_dispatcher,
    )
    from magi_agent.tools.safety import RuntimePermissionArbiter

    inner_tool = FakeMcpTool("composio-FILE_WRITE")
    toolset = FakeMcpToolset([inner_tool])
    bundle = ComposioToolsetBundle(active=True, status="ready", toolsets=(toolset,))
    runner = FakeRunner()
    ledger = ComposioReceiptLedger()

    attach_composio_toolsets_through_dispatcher(
        runner,
        bundle,
        arbiter=RuntimePermissionArbiter(),
        mode="act",
        context_factory=lambda **_kw: _ctx(),
        receipt_ledger=ledger,
    )
    guarded = asyncio.run(runner.agent.tools[0].get_tools())[0]

    result = asyncio.run(guarded.run_async(args={"path": ".env"}, tool_context=None))

    # body blocked AND a deny receipt was recorded.
    assert inner_tool.calls == []
    assert result.get("status") == "blocked"
    receipts = ledger.receipts()
    assert len(receipts) == 1
    assert receipts[0].action == "deny"
    assert receipts[0].tool == "composio-FILE_WRITE"
    # secret path must not appear anywhere in the recorded receipt.
    assert ".env" not in str(receipts[0])


def test_guarded_composio_ledger_default_created_when_none() -> None:
    """A ledger is always available even when the caller passes none."""
    from magi_agent.composio.mcp import (
        ComposioToolsetBundle,
        attach_composio_toolsets_through_dispatcher,
        guarded_toolset_receipt_ledger,
    )
    from magi_agent.tools.safety import RuntimePermissionArbiter

    inner_tool = FakeMcpTool("composio-GMAIL_SEND")
    toolset = FakeMcpToolset([inner_tool])
    bundle = ComposioToolsetBundle(active=True, status="ready", toolsets=(toolset,))
    runner = FakeRunner()

    attach_composio_toolsets_through_dispatcher(
        runner,
        bundle,
        arbiter=RuntimePermissionArbiter(),
        mode="act",
        context_factory=lambda **_kw: _ctx(),
    )
    guarded_toolset = runner.agent.tools[0]
    guarded = asyncio.run(guarded_toolset.get_tools())[0]
    asyncio.run(guarded.run_async(args={"to": "a@b.com"}, tool_context=None))

    ledger = guarded_toolset_receipt_ledger(guarded_toolset)
    assert ledger is not None
    assert len(ledger.receipts()) == 1


def test_dispatcher_attach_noop_for_inactive_bundle() -> None:
    from magi_agent.composio.mcp import (
        ComposioToolsetBundle,
        attach_composio_toolsets_through_dispatcher,
    )
    from magi_agent.tools.safety import RuntimePermissionArbiter

    runner = FakeRunner()
    attached = attach_composio_toolsets_through_dispatcher(
        runner,
        ComposioToolsetBundle(active=False, status="inactive"),
        arbiter=RuntimePermissionArbiter(),
        mode="act",
        context_factory=lambda **_kw: _ctx(),
    )
    assert attached is False
    assert runner.agent.tools == []


# ---------------------------------------------------------------------------
# wiring gate: ON routes through dispatcher, OFF preserves legacy attach
# ---------------------------------------------------------------------------


def test_wiring_routes_composio_through_dispatcher_when_enforced(monkeypatch) -> None:
    import magi_agent.cli.wiring as wiring

    monkeypatch.setenv("MAGI_COMPOSIO_DISPATCH_ENFORCED", "on")

    captured: dict[str, object] = {}

    def fake_through_dispatcher(runner, bundle, **kwargs):
        captured["dispatcher"] = True
        return True

    def fake_legacy(runner, bundle):
        captured["legacy"] = True
        return True

    monkeypatch.setattr(
        wiring,
        "attach_composio_toolsets_through_dispatcher",
        fake_through_dispatcher,
        raising=False,
    )
    monkeypatch.setattr(wiring, "attach_composio_toolsets_to_runner", fake_legacy)
    monkeypatch.setattr(
        wiring,
        "build_composio_toolset_bundle",
        lambda _cfg: wiring.ComposioToolsetBundle(
            active=True, status="ready", toolsets=("ts",)
        ),
    )

    runner = FakeRunner()
    wiring._build_composio_bundle_for_mode(runner, mode="act")

    assert captured.get("dispatcher") is True
    assert "legacy" not in captured


def test_wiring_uses_legacy_attach_when_not_enforced(monkeypatch) -> None:
    import magi_agent.cli.wiring as wiring

    monkeypatch.delenv("MAGI_COMPOSIO_DISPATCH_ENFORCED", raising=False)

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        wiring,
        "attach_composio_toolsets_through_dispatcher",
        lambda *a, **k: captured.__setitem__("dispatcher", True) or True,
        raising=False,
    )
    monkeypatch.setattr(
        wiring,
        "attach_composio_toolsets_to_runner",
        lambda runner, bundle: captured.__setitem__("legacy", True) or True,
    )
    monkeypatch.setattr(
        wiring,
        "build_composio_toolset_bundle",
        lambda _cfg: wiring.ComposioToolsetBundle(
            active=True, status="ready", toolsets=("ts",)
        ),
    )

    runner = FakeRunner()
    wiring._build_composio_bundle_for_mode(runner, mode="act")

    assert captured.get("legacy") is True
    assert "dispatcher" not in captured
