from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# username normalization + token extraction
# ---------------------------------------------------------------------------

def test_normalize_username_basic() -> None:
    from magi_agent.channels.telegram_easy import normalize_bot_username

    assert normalize_bot_username("My Agent") == "my_agent_bot"


def test_normalize_username_with_suffix() -> None:
    from magi_agent.channels.telegram_easy import normalize_bot_username

    assert normalize_bot_username("My Agent", suffix="x7") == "my_agent_x7_bot"


def test_normalize_username_already_ends_with_bot() -> None:
    from magi_agent.channels.telegram_easy import normalize_bot_username

    assert normalize_bot_username("Helper Bot") == "helper_bot"


def test_normalize_username_strips_invalid_and_caps_length() -> None:
    from magi_agent.channels.telegram_easy import normalize_bot_username

    out = normalize_bot_username("Ünïcödé!! 한글 name@#$")
    assert out.endswith("bot")
    assert len(out) <= 32
    assert all(c.isalnum() or c == "_" for c in out)


def test_extract_token() -> None:
    from magi_agent.channels.telegram_easy import extract_bot_token

    msg = "Done! Use this token to access the HTTP API:\n123456789:AAFhqd-ABCdef_GHIjklMNOpqrStuvwxyz12\nKeep it safe."
    assert extract_bot_token(msg) == "123456789:AAFhqd-ABCdef_GHIjklMNOpqrStuvwxyz12"


def test_extract_token_absent() -> None:
    from magi_agent.channels.telegram_easy import extract_bot_token

    assert extract_bot_token("Sorry, this username is already taken.") is None


# ---------------------------------------------------------------------------
# BotFather conversation
# ---------------------------------------------------------------------------

class _FakeBotFather:
    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.sent: list[str] = []

    def converse(self, text: str) -> str:
        self.sent.append(text)
        return self._replies[len(self.sent) - 1]


def test_create_bot_succeeds_first_username() -> None:
    from magi_agent.channels.telegram_easy import create_bot_via_botfather

    channel = _FakeBotFather(
        [
            "Alright, a new bot. How are we going to call it?",  # after /newbot
            "Good. Now let's choose a username for your bot.",  # after name
            "Done! 111:AAToken_aaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # after username
        ]
    )

    token, username = create_bot_via_botfather(
        channel, "My Agent", username_suffixes=["a1", "a2"]
    )

    assert token == "111:AAToken_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert username == "my_agent_bot"
    assert channel.sent[0] == "/newbot"
    assert channel.sent[1] == "My Agent"
    assert channel.sent[2] == "my_agent_bot"


def test_create_bot_retries_on_taken_username() -> None:
    from magi_agent.channels.telegram_easy import create_bot_via_botfather

    channel = _FakeBotFather(
        [
            "How are we going to call it?",
            "Now let's choose a username.",
            "Sorry, this username is already taken. Try something different.",
            "Done! 222:BBToken_bbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ]
    )

    token, username = create_bot_via_botfather(
        channel, "My Agent", username_suffixes=["x7", "x8"]
    )

    assert token == "222:BBToken_bbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert username == "my_agent_x7_bot"
    assert channel.sent[2] == "my_agent_bot"
    assert channel.sent[3] == "my_agent_x7_bot"


def test_create_bot_fails_when_all_taken() -> None:
    from magi_agent.channels.telegram_easy import BotCreationFailed, create_bot_via_botfather

    channel = _FakeBotFather(
        [
            "name?",
            "username?",
            "Sorry, this username is already taken.",
            "Sorry, this username is already taken.",
        ]
    )

    with pytest.raises(BotCreationFailed):
        create_bot_via_botfather(channel, "My Agent", username_suffixes=["x7"])


# ---------------------------------------------------------------------------
# session store
# ---------------------------------------------------------------------------

def test_session_store_create_get_delete() -> None:
    from magi_agent.channels.telegram_easy import EasySessionStore

    store = EasySessionStore(ttl_seconds=300)
    sid = store.create(session="s0", phone="+1", phone_code_hash="h0", now=1000.0)
    got = store.get(sid, now=1100.0)
    assert got is not None
    assert got.session == "s0"
    assert got.step == "code"
    store.delete(sid)
    assert store.get(sid, now=1100.0) is None


def test_session_store_expires() -> None:
    from magi_agent.channels.telegram_easy import EasySessionStore

    store = EasySessionStore(ttl_seconds=300)
    sid = store.create(session="s0", phone="+1", phone_code_hash="h0", now=1000.0)
    assert store.get(sid, now=1000.0 + 301) is None


# ---------------------------------------------------------------------------
# orchestration over the auth port
# ---------------------------------------------------------------------------

class _FakeAuthPort:
    def __init__(self, *, two_factor: bool = False) -> None:
        self._two_factor = two_factor
        self.logged_out: list[str] = []
        self.botfather_channel = _FakeBotFather(
            ["name?", "username?", "Done! 333:CCToken_cccccccccccccccccccccccccccc"]
        )

    def send_code(self, phone: str) -> tuple[str, str]:
        return ("sess-0", "hash-0")

    def sign_in(self, *, session: str, phone: str, code: str, phone_code_hash: str) -> str:
        from magi_agent.channels.telegram_easy import TwoFactorRequired

        if self._two_factor:
            raise TwoFactorRequired()
        return "sess-auth"

    def check_password(self, *, session: str, password: str) -> str:
        return "sess-auth-2fa"

    def botfather(self, session: str):
        return self.botfather_channel

    def log_out(self, session: str) -> None:
        self.logged_out.append(session)


def test_begin_login_and_submit_code_no_2fa() -> None:
    from magi_agent.channels.telegram_easy import (
        EasySessionStore,
        begin_login,
        submit_code,
    )

    store = EasySessionStore()
    port = _FakeAuthPort(two_factor=False)
    sid = begin_login(store, port, "+15551234567", now=10.0)
    needs_2fa = submit_code(store, port, sid, "00000", now=11.0)
    assert needs_2fa is False
    assert store.get(sid, now=12.0).step == "authenticated"


def test_submit_code_triggers_2fa_then_password() -> None:
    from magi_agent.channels.telegram_easy import (
        EasySessionStore,
        begin_login,
        submit_code,
        submit_password,
    )

    store = EasySessionStore()
    port = _FakeAuthPort(two_factor=True)
    sid = begin_login(store, port, "+1", now=10.0)
    assert submit_code(store, port, sid, "00000", now=11.0) is True
    assert store.get(sid, now=12.0).step == "2fa"
    submit_password(store, port, sid, "hunter2", now=13.0)
    assert store.get(sid, now=14.0).step == "authenticated"


def test_finish_create_bot_persists_and_cleans_up() -> None:
    from magi_agent.channels.telegram_easy import (
        EasySessionStore,
        begin_login,
        finish_create_bot,
        submit_code,
    )

    store = EasySessionStore()
    port = _FakeAuthPort(two_factor=False)
    sid = begin_login(store, port, "+1", now=10.0)
    submit_code(store, port, sid, "00000", now=11.0)

    persisted: list[str] = []

    def persist(token: str) -> dict[str, object]:
        persisted.append(token)
        return {"configured": True, "label": "@my_agent_bot"}

    result = finish_create_bot(
        store,
        port,
        sid,
        "My Agent",
        now=12.0,
        persist=persist,
        username_suffixes=["x7"],
    )

    assert persisted == ["333:CCToken_cccccccccccccccccccccccccccc"]
    assert result == {"configured": True, "label": "@my_agent_bot"}
    assert port.logged_out == ["sess-auth"]  # MTProto session discarded
    assert store.get(sid, now=13.0) is None  # ephemeral session deleted
