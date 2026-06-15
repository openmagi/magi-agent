"""Contract: create_app installs the session-transcript sink only behind the
MAGI_SESSION_TRANSCRIPT_ENABLED flag (default-OFF surface stays byte-identical)."""
from __future__ import annotations

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.observability.transcript import (
    get_active_transcript_sink,
    set_active_transcript_sink,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token="t",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def test_create_app_does_not_install_sink_by_default(monkeypatch):
    monkeypatch.delenv("MAGI_SESSION_TRANSCRIPT_ENABLED", raising=False)
    set_active_transcript_sink(None)
    create_app(_runtime())
    assert get_active_transcript_sink() is None


def test_create_app_installs_sink_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_SESSION_TRANSCRIPT_ENABLED", "1")
    monkeypatch.setenv("MAGI_OBS_HOME", str(tmp_path))
    set_active_transcript_sink(None)
    try:
        create_app(_runtime())
        assert get_active_transcript_sink() is not None
    finally:
        set_active_transcript_sink(None)
