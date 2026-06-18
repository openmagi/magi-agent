"""DiscordGatewayProvider (PR2) — concrete live provider.

discord.py is an optional extra (``magi-agent[discord]``), so it is imported
lazily INSIDE methods; importing this module must not pull it.  The pure pieces
(message -> raw-event dict, queue drain, trust marker) are tested here with a
fake message and an injected queue; the gateway/thread glue needs discord.py and
a live token and is covered only by manual smoke.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

from magi_agent.channels.discord_adapter import _project_event
from magi_agent.channels.providers.discord_gateway import (
    DiscordGatewayProvider,
    _message_to_raw,
)


class _FakeAuthor:
    def __init__(self, user_id: str, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class _FakeChannel:
    def __init__(self, channel_id: str) -> None:
        self.id = channel_id


class _FakeMessage:
    def __init__(
        self,
        *,
        message_id: str,
        channel_id: str,
        author: _FakeAuthor,
        content: str,
        guild: object | None = None,
        mentions: list[_FakeAuthor] | None = None,
    ) -> None:
        self.id = message_id
        self.channel = _FakeChannel(channel_id)
        self.author = author
        self.content = content
        self.guild = guild  # None => DM
        self.mentions = mentions or []
        self.reference = None
        self.attachments = []


def test_provider_is_not_the_audit_fake() -> None:
    provider = DiscordGatewayProvider(token="tok")
    assert provider.openmagi_local_fake_provider is False


def test_message_to_raw_is_consumable_by_project_event() -> None:
    msg = _FakeMessage(
        message_id="m1",
        channel_id="c1",
        author=_FakeAuthor("u1"),
        content="hello",
    )  # DM (guild=None)

    raw = _message_to_raw(msg)
    event = _project_event(raw, bot_user_id=None)

    assert event is not None
    assert event.channel_id == "c1"
    assert event.user_id == "u1"
    assert event.message_id == "m1"
    assert event.text == "hello"


def test_message_to_raw_marks_bot_authors() -> None:
    msg = _FakeMessage(
        message_id="m2",
        channel_id="c1",
        author=_FakeAuthor("bot1", bot=True),
        content="hi",
    )
    raw = _message_to_raw(msg)
    # _project_event drops bot authors -> proves the bot flag is carried through.
    assert raw["author"]["bot"] is True
    assert _project_event(raw, bot_user_id=None) is None


def test_read_events_drains_queue_without_starting_client() -> None:
    provider = DiscordGatewayProvider(token="tok")
    provider._started = True  # bypass the discord.py client/thread start

    raw = _message_to_raw(
        _FakeMessage(
            message_id="m1", channel_id="c1", author=_FakeAuthor("u1"), content="hi"
        )
    )
    provider._queue.put(raw)

    first = provider.read_events(_req())
    second = provider.read_events(_req())

    assert len(first) == 1
    assert first[0]["id"] == "m1"
    assert second == []  # queue drained


def test_module_does_not_import_discord_at_top() -> None:
    code = (
        "import sys\n"
        "import magi_agent.channels.providers.discord_gateway  # noqa: F401\n"
        "assert 'discord' not in sys.modules, 'discord imported at module top'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _req() -> Any:
    class _R:
        bot_user_id = None

    return _R()
