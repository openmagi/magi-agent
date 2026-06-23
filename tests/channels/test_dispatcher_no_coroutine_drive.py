"""J-9 — ``channels/dispatcher.py`` must not manually drive coroutines.

Pre-J-9 ``ChannelDispatcher.dispatch`` called
``_run_provider_execution(coro)`` which did ``coro.send(None)`` (with
``# type: ignore[attr-defined]``) to extract the first yielded value
from ``ProviderExecutionBoundary.execute(...)``. This **only worked
because** the dispatcher's provider port is synchronous and the
boundary's ``await raw_response if awaitable else raw_response`` branch
never fires — meaning the coroutine completes after the very first
step, raising ``StopIteration(value)``. The moment a real async I/O
landed on this path, a pending future would be returned (or raised) and
the dispatcher would silently misbehave.

J-9 replaces the ``coro.send(None)`` hack with a proper sync entry
point ``ProviderExecutionBoundary.execute_sync(...)``. The dispatcher
calls that. ``coro.send(None)`` is gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.channels.dispatcher import (
    ChannelDispatchConfig,
    ChannelDispatchRequest,
    ChannelDispatcher,
    ChannelDispatchProviderPort,
)


# ---------------------------------------------------------------------------
# Source-level meta-test: forbid ``coro.send(None)`` from re-appearing in
# the dispatcher module.
# ---------------------------------------------------------------------------


def test_dispatcher_module_has_no_coroutine_send_hack() -> None:
    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    target = package_root / "channels" / "dispatcher.py"
    text = target.read_text(encoding="utf-8")
    assert "coro.send(None)" not in text, (
        "channels/dispatcher.py reintroduced the ``coro.send(None)`` "
        "coroutine-drive hack. Use "
        "``ProviderExecutionBoundary.execute_sync(...)`` instead (J-9)."
    )
    assert "_run_provider_execution" not in text, (
        "channels/dispatcher.py still references "
        "``_run_provider_execution``. The helper should be removed in "
        "favor of ``execute_sync(...)`` (J-9)."
    )
    assert "# type: ignore[attr-defined]" not in text, (
        "channels/dispatcher.py still carries the ``# type: ignore"
        "[attr-defined]`` that papered over the ``coro.send`` hack. "
        "Remove it (J-9)."
    )


# ---------------------------------------------------------------------------
# Behavior: dispatcher still produces the same decision shape with a sync
# provider. Locks the post-J-9 wiring against drift.
# ---------------------------------------------------------------------------


class _SyncEchoProvider:
    """A synchronous local-fake provider — what the dispatcher actually
    receives in production."""

    openmagi_local_fake_provider = True

    def execute(self, request) -> dict[str, object]:  # noqa: ANN001
        return {"echoed": True, "operation": request.operation}


def _channel(**kwargs):
    from magi_agent.channels.contract import ChannelRef

    return ChannelRef(
        type=kwargs.get("type", "discord"),
        channelId=kwargs.get("channel_id", "discord-chat-1"),
    )


def _request(**overrides) -> ChannelDispatchRequest:
    base = dict(
        operation="dispatch.message",
        requestId="req-1",
        channel=_channel(),
        providerName="echo-provider",
        botIdDigest="sha256:" + "b" * 64,
        userIdDigest="sha256:" + "c" * 64,
        sessionKeyDigest="sha256:" + "d" * 64,
        text="hello",
    )
    base.update(overrides)
    return ChannelDispatchRequest(**base)


def _config_enabled() -> ChannelDispatchConfig:
    return ChannelDispatchConfig(
        enabled=True,
        localFakeProviderEnabled=True,
        selectedChannelRoutes=("discord",),
        providerAllowlist=("echo-provider",),
    )


def test_dispatch_with_sync_provider_returns_recorded_receipt() -> None:
    dispatcher = ChannelDispatcher(_config_enabled())
    decision = dispatcher.dispatch(_request(), provider=_SyncEchoProvider())
    assert decision.status == "recorded_local_fake"
    assert decision.receipt is not None


def test_dispatch_with_sync_provider_idempotent_replay() -> None:
    """Same request digest → cached receipt + idempotent reason code."""

    dispatcher = ChannelDispatcher(_config_enabled())
    req = _request()
    first = dispatcher.dispatch(req, provider=_SyncEchoProvider())
    second = dispatcher.dispatch(req, provider=_SyncEchoProvider())
    assert first.status == "recorded_local_fake"
    assert second.status == "recorded_local_fake"
    assert "channel_dispatch_idempotent_receipt" in second.reason_codes


def test_dispatch_disabled_short_circuits() -> None:
    dispatcher = ChannelDispatcher(
        ChannelDispatchConfig(enabled=False)
    )
    decision = dispatcher.dispatch(_request(), provider=_SyncEchoProvider())
    assert decision.status == "disabled"
    assert "channel_dispatch_disabled" in decision.reason_codes


def test_dispatch_validation_error_short_circuits() -> None:
    """Pre-J-9 path still works: a request that fails validation
    returns 'blocked' before any provider execution."""

    dispatcher = ChannelDispatcher(_config_enabled())
    bad_request = _request(botIdDigest="")  # validation: bot_id_digest required
    decision = dispatcher.dispatch(bad_request, provider=_SyncEchoProvider())
    assert decision.status == "blocked"
    assert "bot_id_digest_required" in decision.reason_codes


# ---------------------------------------------------------------------------
# ProviderExecutionBoundary.execute_sync semantics
# ---------------------------------------------------------------------------


def test_provider_execution_boundary_exposes_execute_sync() -> None:
    """The boundary must expose a synchronous entry point that returns
    the result directly (no coroutine drive)."""

    from magi_agent.runtime.provider_execution import ProviderExecutionBoundary

    assert hasattr(ProviderExecutionBoundary, "execute_sync"), (
        "ProviderExecutionBoundary must expose a sync ``execute_sync`` "
        "method (J-9) so the dispatcher does not manually drive the "
        "async ``execute`` coroutine."
    )


def test_execute_sync_returns_result_directly_with_sync_provider() -> None:
    """A sync provider returning a dict is the dispatcher's actual call
    shape. ``execute_sync`` must return the typed result without any
    coroutine bookkeeping."""

    from magi_agent.runtime.provider_execution import (
        ProviderExecutionBoundary,
        ProviderExecutionConfig,
        ProviderExecutionRequest,
        ProviderExecutionScope,
    )

    boundary = ProviderExecutionBoundary(
        ProviderExecutionConfig(enabled=True, localFakeProviderEnabled=True)
    )
    request = ProviderExecutionRequest(
        providerName="echo-provider",
        operation="channel.dispatch.message",
        payload={"k": "v"},
        scope=ProviderExecutionScope(
            environment="local-test",
            botIdDigest="sha256:" + "b" * 64,
            ownerIdDigest="sha256:" + "c" * 64,
            selectedScope=True,
            sessionIdDigest="sha256:" + "d" * 64,
        ),
    )

    class _SyncProvider:
        openmagi_local_fake_provider = True

        def execute(self, _req):  # noqa: ANN001
            return {"ok": True}

    result = boundary.execute_sync(request, provider=_SyncProvider())
    assert result.status == "ok"


def test_execute_sync_rejects_async_provider() -> None:
    """If a provider returns an awaitable, ``execute_sync`` must NOT
    silently lose the value (the pre-J-9 ``coro.send(None)`` hack would
    have)."""

    from magi_agent.runtime.provider_execution import (
        ProviderExecutionBoundary,
        ProviderExecutionConfig,
        ProviderExecutionRequest,
        ProviderExecutionScope,
    )

    boundary = ProviderExecutionBoundary(
        ProviderExecutionConfig(enabled=True, localFakeProviderEnabled=True)
    )
    request = ProviderExecutionRequest(
        providerName="echo-provider",
        operation="channel.dispatch.message",
        payload={"k": "v"},
        scope=ProviderExecutionScope(
            environment="local-test",
            botIdDigest="sha256:" + "b" * 64,
            ownerIdDigest="sha256:" + "c" * 64,
            selectedScope=True,
            sessionIdDigest="sha256:" + "d" * 64,
        ),
    )

    class _AsyncProvider:
        openmagi_local_fake_provider = True

        async def execute(self, _req):  # noqa: ANN001
            return {"ok": True}

    result = boundary.execute_sync(request, provider=_AsyncProvider())
    # The boundary classifies an awaitable response on the sync entry as
    # a provider error (rather than silently consuming the awaitable).
    assert result.status == "error"
