"""SlackSocketModeProvider (PR3) — concrete live inbound provider.

slack_sdk is an optional extra (``magi-agent[slack]``), imported lazily INSIDE
start(); importing this module must not pull it.  The pure pieces (events_api
payload -> message event, queue drain, trust marker) are tested here; the Socket
Mode websocket glue needs slack_sdk + an app token and is manual-smoke only.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

from magi_agent.channels.providers.slack_socketmode import (
    SlackSocketModeProvider,
    _extract_message_event,
)
from magi_agent.channels.slack_live import _project_slack_event


def test_provider_is_not_the_audit_fake() -> None:
    provider = SlackSocketModeProvider(app_token="xapp-1")
    assert provider.openmagi_local_fake_provider is False


def test_extract_message_event_pulls_inner_message() -> None:
    payload = {
        "event": {
            "type": "message",
            "channel": "C1",
            "user": "U1",
            "text": "hi",
            "ts": "169.1",
        }
    }
    event = _extract_message_event(payload)
    assert event is not None
    # output must be consumable by the slack projection
    ci = _project_slack_event(event)
    assert ci is not None
    assert ci.channel_id == "C1"
    assert ci.text == "hi"


def test_extract_message_event_ignores_non_message() -> None:
    assert _extract_message_event({"event": {"type": "reaction_added"}}) is None
    assert _extract_message_event({}) is None


def test_read_events_drains_queue_without_starting_socket() -> None:
    provider = SlackSocketModeProvider(app_token="xapp-1")
    provider._started = True  # bypass slack_sdk socket start
    provider._queue.put({"type": "message", "channel": "C1", "user": "U1", "text": "hi", "ts": "1"})

    first = provider.read_events(_req())
    second = provider.read_events(_req())

    assert len(first) == 1
    assert first[0]["channel"] == "C1"
    assert second == []


def test_module_does_not_import_slack_sdk_at_top() -> None:
    code = (
        "import sys\n"
        "import magi_agent.channels.providers.slack_socketmode  # noqa: F401\n"
        "bad = {'slack_sdk','slack_bolt'} & set(sys.modules)\n"
        "assert not bad, f'slack sdk imported at top: {sorted(bad)}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _req() -> Any:
    return None
