from __future__ import annotations

from types import SimpleNamespace

from magi_agent.observability.config import ObservabilityConfig
from magi_agent.observability.core import ObservabilityCore


def test_disabled_core_is_inert(tmp_path):
    cfg = ObservabilityConfig(enabled=False, db_path=tmp_path / "o.db")
    core = ObservabilityCore(cfg, runtime=SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b")))
    assert core.router is None
    core.record_from_hook("beforeToolUse", SimpleNamespace(tool_name="read", session_id="s1"))
    assert core.store is None


def test_enabled_core_records_and_exposes_router(tmp_path):
    cfg = ObservabilityConfig(enabled=True, db_path=tmp_path / "o.db")
    core = ObservabilityCore(cfg, runtime=SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b")))
    assert core.router is not None
    core.record_from_hook("beforeToolUse", SimpleNamespace(tool_name="read", session_id="s1", run_id="r1"))
    rows = core.store.list_events()
    assert rows and rows[0]["kind"] == "tool_start"
    core.close()


def test_record_is_fail_open(tmp_path):
    cfg = ObservabilityConfig(enabled=True, db_path=tmp_path / "o.db")
    core = ObservabilityCore(cfg, runtime=SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b")))
    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("nope")
    core.record_from_hook("beforeToolUse", Boom())  # must not raise
    core.close()


def test_record_publishes_to_bus_on_running_loop(tmp_path):
    import asyncio

    from magi_agent.observability.config import ObservabilityConfig
    from magi_agent.observability.core import ObservabilityCore

    cfg = ObservabilityConfig(enabled=True, db_path=tmp_path / "o.db")
    core = ObservabilityCore(cfg, runtime=SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b")))

    async def run():
        sub = core.bus.subscribe(channel="*")
        core.record_from_hook("beforeToolUse", SimpleNamespace(tool_name="read", session_id="s1", run_id="r1"))
        ev = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert ev["kind"] == "tool_start"
        await sub.aclose()

    asyncio.run(run())
    core.close()


# --- record_public_event tests ---

def test_record_public_event_stores_tool_start(tmp_path):
    cfg = ObservabilityConfig(enabled=True, db_path=tmp_path / "o.db")
    core = ObservabilityCore(cfg, runtime=SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b")))
    payload = {"type": "tool_start", "toolName": "bash", "toolUseId": "u1"}
    core.record_public_event(payload, "ses1", "turn1")
    rows = core.store.list_events()
    assert rows and rows[0]["kind"] == "tool_start"
    assert rows[0]["tool_name"] == "bash"
    core.close()


def test_record_public_event_publishes_to_bus_on_running_loop(tmp_path):
    import asyncio

    cfg = ObservabilityConfig(enabled=True, db_path=tmp_path / "o.db")
    core = ObservabilityCore(cfg, runtime=SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b")))

    async def run():
        sub = core.bus.subscribe(channel="*")
        payload = {"type": "tool_start", "toolName": "read", "toolUseId": "u2"}
        core.record_public_event(payload, "ses1", "turn1")
        ev = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert ev["kind"] == "tool_start"
        await sub.aclose()

    asyncio.run(run())
    core.close()


def test_record_public_event_malformed_does_not_raise(tmp_path):
    cfg = ObservabilityConfig(enabled=True, db_path=tmp_path / "o.db")
    core = ObservabilityCore(cfg, runtime=SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b")))
    # missing "type" key — project_public_event returns None, must not raise
    core.record_public_event({"toolName": "bash"}, "ses1", "turn1")
    # non-dict — must not raise
    core.record_public_event(None, "ses1", "turn1")  # type: ignore[arg-type]
    core.close()


def test_ensure_retention_started_safe_without_loop(tmp_path):
    from magi_agent.observability.config import ObservabilityConfig
    from magi_agent.observability.core import ObservabilityCore
    from types import SimpleNamespace

    cfg = ObservabilityConfig(enabled=True, db_path=tmp_path / "o.db")
    core = ObservabilityCore(cfg, runtime=SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b")))
    core.ensure_retention_started()  # no running loop -> must not raise, must not mark started
    assert core._retention_started is False
    core.close()
