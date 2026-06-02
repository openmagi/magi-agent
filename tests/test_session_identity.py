from openmagi_core_agent.runtime.session_identity import (
    MemoryMode,
    parse_session_identity,
)


def test_parse_session_identity_prefers_core_header_and_applies_reset_counter() -> None:
    identity = parse_session_identity(
        {
            "x-core-agent-session-key": "agent:main:app:general",
            "x-openclaw-session-key": "agent:main:app:legacy",
            "x-core-agent-memory-mode": "incognito",
        },
        bot_id="bot-abcdef1234",
        reset_counter=2,
    )

    assert identity.session_key == "agent:main:app:general"
    assert identity.effective_session_key == "agent:main:app:general:2"
    assert identity.channel.type == "app"
    assert identity.channel.channel_id == "general"
    assert identity.memory_mode is MemoryMode.INCOGNITO


def test_parse_session_identity_uses_openclaw_fallback_and_default_memory_mode() -> None:
    identity = parse_session_identity(
        {"x-openclaw-session-key": "agent:main:telegram:777"},
        bot_id="bot-abcdef1234",
    )

    assert identity.session_key == "agent:main:telegram:777"
    assert identity.effective_session_key == "agent:main:telegram:777"
    assert identity.channel.type == "telegram"
    assert identity.channel.channel_id == "777"
    assert identity.memory_mode is MemoryMode.NORMAL


def test_parse_session_identity_creates_default_key_from_bot_prefix() -> None:
    identity = parse_session_identity({}, bot_id="bot-abcdef1234")

    assert identity.session_key == "agent:main:app:default:bot-abcd"
    assert identity.channel.type == "app"
    assert identity.channel.channel_id == "default"
