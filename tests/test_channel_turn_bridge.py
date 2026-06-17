"""Shared channel turn bridge (PR1).

The bridge is the channel-agnostic seam that turns a normalised inbound message
into an agent turn and delivers the reply back on the SAME channel.  It is pure:
``run_turn`` and ``deliver`` are injected (sync), so no engine or event loop is
needed to test it.  The real async-engine wrapper is a separate concern.
"""
from __future__ import annotations

from magi_agent.channels.turn_bridge import (
    ChannelInbound,
    channel_session_key,
    make_inbound_handler,
)
from magi_agent.runtime.session_identity import _channel_from_session_key


def test_channel_session_key_round_trips_with_channel_parser() -> None:
    """The session key must encode (channel_type, channel_id) so the parser the
    rest of the runtime uses recovers the same channel — this is the foundation
    of the source=delivery invariant (a reply routes back to where it came)."""
    key = channel_session_key("discord", "999")

    assert key == "agent:main:discord:999"

    ref = _channel_from_session_key(key)
    assert ref.type == "discord"
    assert ref.channel_id == "999"


def test_handler_runs_turn_and_delivers_reply_on_same_channel() -> None:
    """The core path: inbound -> session_key -> turn -> deliver reply on the same
    channel, threaded under the originating message."""
    seen: dict[str, object] = {}

    def run_turn(session_key: str, inbound: ChannelInbound) -> str:
        seen["session_key"] = session_key
        seen["text"] = inbound.text
        return "the reply"

    delivered: list[tuple[str, str, str | None]] = []

    def deliver(channel_id: str, text: str, reply_to: str | None) -> bool:
        delivered.append((channel_id, text, reply_to))
        return True

    handler = make_inbound_handler(
        channel_type="discord",
        run_turn=run_turn,
        deliver=deliver,
        evidence={},
    )

    handler(
        ChannelInbound(
            channel_type="discord",
            channel_id="c1",
            text="hi",
            message_id="m1",
        )
    )

    assert seen["session_key"] == "agent:main:discord:c1"
    assert seen["text"] == "hi"
    # Reply goes back on the SAME channel, threaded under the inbound message.
    assert delivered == [("c1", "the reply", "m1")]


def _inbound(text: str = "hi") -> ChannelInbound:
    return ChannelInbound(
        channel_type="discord", channel_id="c1", text=text, message_id="m1"
    )


def test_empty_reply_is_not_delivered() -> None:
    """A blank/whitespace-only turn result must not produce an empty channel
    message — the bridge skips delivery entirely."""
    delivered: list[object] = []

    def deliver(channel_id: str, text: str, reply_to: str | None) -> bool:
        delivered.append((channel_id, text, reply_to))
        return True

    handler = make_inbound_handler(
        channel_type="discord",
        run_turn=lambda _key, _inb: "   \n  ",
        deliver=deliver,
        evidence={},
    )

    handler(_inbound())

    assert delivered == []


def test_run_turn_error_is_isolated_and_does_not_deliver() -> None:
    """One failing turn must not raise out of the handler (that would abort the
    rest of the poll batch) nor deliver a reply."""
    delivered: list[object] = []

    def boom(_key: str, _inb: ChannelInbound) -> str:
        raise RuntimeError("turn blew up")

    handler = make_inbound_handler(
        channel_type="discord",
        run_turn=boom,
        deliver=lambda *a: delivered.append(a) or True,
        evidence={},
    )

    handler(_inbound())  # must not raise

    assert delivered == []


def test_evidence_never_stores_raw_text() -> None:
    """Evidence is an audit accumulator — it must record digests/flags only,
    never the raw inbound text or the reply body."""
    evidence: dict[str, object] = {}
    secret_in = "user secret question"
    secret_out = "secret answer body"

    handler = make_inbound_handler(
        channel_type="telegram",
        run_turn=lambda _key, _inb: secret_out,
        deliver=lambda *_a: True,
        evidence=evidence,
    )

    handler(_inbound(text=secret_in))

    blob = repr(evidence)
    assert secret_in not in blob
    assert secret_out not in blob
    assert evidence["turnInvoked"] is True
    assert evidence["delivered"] is True
    assert "sessionKeyDigest" in evidence
