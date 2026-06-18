"""Slack inbound projection -> ChannelInbound (PR3).

Mirrors the discord/telegram real-path approach: a normalised raw Slack event is
projected straight into the channel-agnostic ChannelInbound so the shared turn
bridge can drive a turn.  Pure, no slack_sdk.
"""
from __future__ import annotations

from magi_agent.channels.slack_live import _project_slack_event
from magi_agent.channels.turn_bridge import ChannelInbound


def test_projects_plain_message() -> None:
    raw = {
        "type": "message",
        "channel": "C1",
        "user": "U1",
        "text": "hi",
        "ts": "169.1",
        "thread_ts": None,
    }
    assert _project_slack_event(raw) == ChannelInbound(
        channel_type="slack",
        channel_id="C1",
        text="hi",
        message_id="169.1",
        user_id="U1",
    )


def test_threaded_message_targets_the_existing_thread() -> None:
    raw = {
        "type": "message",
        "channel": "C1",
        "user": "U1",
        "text": "hi",
        "ts": "169.2",
        "thread_ts": "169.1",
    }
    # reply target (message_id) is the thread root so the bot replies in-thread.
    assert _project_slack_event(raw).message_id == "169.1"


def test_skips_bot_messages() -> None:
    raw = {"type": "message", "channel": "C1", "user": "U1", "text": "hi", "ts": "1", "bot_id": "B1"}
    assert _project_slack_event(raw) is None


def test_skips_empty_text() -> None:
    raw = {"type": "message", "channel": "C1", "user": "U1", "text": "  ", "ts": "1"}
    assert _project_slack_event(raw) is None


def test_skips_non_message_events() -> None:
    assert _project_slack_event({"type": "reaction_added", "user": "U1"}) is None
