"""Per-channel inbound projections -> normalised ChannelInbound (PR1).

Each channel projects its native boundary event into the shared ``ChannelInbound``
so the channel-agnostic turn bridge can drive a turn without knowing the channel.
"""
from __future__ import annotations

from magi_agent.channels.discord_adapter import DiscordInboundEvent
from magi_agent.channels.discord_live import to_channel_inbound as discord_to_channel
from magi_agent.channels.telegram_adapter import TelegramInboundUpdate
from magi_agent.channels.telegram_live import to_channel_inbound as telegram_to_channel
from magi_agent.channels.turn_bridge import ChannelInbound


def test_telegram_inbound_projects_to_channel_inbound() -> None:
    update = TelegramInboundUpdate(
        chatId="chat-7",
        userId="user-3",
        text="hello there",
        messageId="msg-42",
        rawUpdateRef="ref-1",
    )

    result = telegram_to_channel(update)

    assert result == ChannelInbound(
        channel_type="telegram",
        channel_id="chat-7",
        text="hello there",
        message_id="msg-42",
        user_id="user-3",
    )


def test_discord_inbound_projects_to_channel_inbound() -> None:
    event = DiscordInboundEvent(
        channelId="chan-9",
        userId="user-5",
        text="yo",
        messageId="msg-99",
        rawEventRef="ref-2",
    )

    result = discord_to_channel(event)

    assert result == ChannelInbound(
        channel_type="discord",
        channel_id="chan-9",
        text="yo",
        message_id="msg-99",
        user_id="user-5",
    )
