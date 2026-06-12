"""C1 kernel seams: pack-registered workspace handlers + injectable dispatch policies.

Dual-load contract: a host constructed WITHOUT the new kwargs behaves
byte-identically to the legacy host (the gate5b golden oracle proves it); a host
WITH them routes tool bodies through ``(args, WorkspaceHostView)`` handlers and
memory-mode/permission enforcement through ctx-callable policies.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolHost,
    Gate5BFullToolHostConfig,
)
from magi_agent.packs.context import WorkspaceHostView


def _host(tmp_path: Path, **kw):
    return Gate5BFullToolHost(
        config=Gate5BFullToolHostConfig.model_validate(
            {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
             "environment": "local", "environmentAllowlist": ["local"],
             "maxToolCallsPerTurn": 8}
        ),
        workspace_root=tmp_path,
        exposed_tool_names=("Clock", "FileRead"),
        now_ms=lambda: 1_700_000_000_000,
        **kw,
    )


def test_injected_workspace_handler_takes_precedence(tmp_path: Path):
    def fake_clock(args, view):
        assert isinstance(view, WorkspaceHostView)
        return {"nowMs": view.now_ms(), "viaPack": True}

    host = _host(tmp_path, workspace_handlers={"Clock": fake_clock})
    outcome = asyncio.run(
        host.dispatch("Clock", {}, request_digest="r", tool_call_id="c")
    )
    assert outcome.status == "ok"
    # The envelope (counter/receipts) is unchanged; the handler produced output.
    assert outcome.receipt.tool_name == "Clock"
    assert outcome.output_preview == {"nowMs": 1_700_000_000_000, "viaPack": True}


def test_view_resolve_path_enforces_workspace_confinement(tmp_path: Path):
    from magi_agent.gates.gate5b_full_toolhost import Gate5BFullToolPathPolicyError

    host = _host(tmp_path)
    view = WorkspaceHostView(host=host)

    with pytest.raises(Gate5BFullToolPathPolicyError):
        view.resolve_path("../outside.txt")


def test_dispatch_policy_deny_maps_to_blocked(tmp_path: Path):
    def deny_clock(ctx):
        # ContextDispatcher convention: duck-typed on the hook ctx.
        if hasattr(ctx, "decide") and ctx.tool_name == "Clock":
            ctx.decide("deny", reason="test_policy_block")

    host = _host(tmp_path, dispatch_policies=(deny_clock,))
    outcome = asyncio.run(
        host.dispatch("Clock", {}, request_digest="r", tool_call_id="c")
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "test_policy_block"


def test_dispatch_policy_can_override_output(tmp_path: Path):
    def rewrite_clock_output(ctx):
        if hasattr(ctx, "override") and ctx.tool_name == "Clock":
            ctx.override({"nowMs": 0, "filtered": True})

    host = _host(tmp_path, dispatch_policies=(rewrite_clock_output,))
    outcome = asyncio.run(
        host.dispatch("Clock", {}, request_digest="r", tool_call_id="c")
    )
    assert outcome.status == "ok"
    assert outcome.output_preview == {"nowMs": 0, "filtered": True}


def test_bundle_builder_defaults_to_pack_loaded_runtime(tmp_path: Path):
    """C1 default-ON flip: the LIVE path (transport health/chat_routes call this
    builder) consumes the bundled packs when neither kwarg is supplied — same
    precedent as build_default_plugin's pack-loaded control plane."""
    from magi_agent.gates.gate5b_full_toolhost import build_gate5b_full_toolhost_bundle

    bundle = build_gate5b_full_toolhost_bundle(
        config={"enabled": True, "killSwitchEnabled": False,
                "routeAttachmentEnabled": True, "environment": "local",
                "environmentAllowlist": ["local"], "maxToolCallsPerTurn": 8,
                "allowedToolNames": ["Clock"]},
        scope={"selectedBotDigest": "", "selectedOwnerDigest": "", "environment": "local"},
        workspace_root=tmp_path,
    )
    assert bundle.status == "ready"
    assert bundle.host._workspace_handlers.get("Clock") is not None
    assert len(bundle.host._dispatch_policies) >= 2


def test_bundle_builder_explicit_empty_keeps_legacy_runtime(tmp_path: Path):
    """Dual-load escape hatch: explicitly-empty kwargs keep the legacy
    in-module enforcement (no pack loading)."""
    from magi_agent.gates.gate5b_full_toolhost import build_gate5b_full_toolhost_bundle

    bundle = build_gate5b_full_toolhost_bundle(
        config={"enabled": True, "killSwitchEnabled": False,
                "routeAttachmentEnabled": True, "environment": "local",
                "environmentAllowlist": ["local"], "maxToolCallsPerTurn": 8,
                "allowedToolNames": ["Clock"]},
        scope={"selectedBotDigest": "", "selectedOwnerDigest": "", "environment": "local"},
        workspace_root=tmp_path,
        workspace_handlers={},
        dispatch_policies=(),
    )
    assert bundle.status == "ready"
    assert bundle.host._workspace_handlers == {}
    assert bundle.host._dispatch_policies == ()
