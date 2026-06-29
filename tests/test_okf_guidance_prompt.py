"""Gated OKF system-prompt guidance block (induces knowledge/okf discovery).

The block must be byte-absent on the default-OFF path (no OKF flags) and appear
exactly once when both the OKF master + lookup flags are enabled.  All env reads
go through explicit injected ``env=`` dicts so the tests are hermetic and never
depend on the polluting process environment.
"""
from __future__ import annotations

from datetime import datetime

from magi_agent.runtime import message_builder as builder


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_OKF_ON_ENV = {
    "MAGI_KNOWLEDGE_OKF_ENABLED": "1",
    "MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED": "1",
}


def test_okf_guidance_block_empty_when_flags_off() -> None:
    assert builder._okf_guidance_block(env={}) == ""


def test_okf_guidance_block_nonempty_when_both_flags_on() -> None:
    block = builder._okf_guidance_block(env=_OKF_ON_ENV)
    assert block
    assert "<knowledge_okf>" in block
    assert "</knowledge_okf>" in block
    assert "OkfLookup" in block
    assert "knowledge/okf" in block


def test_okf_guidance_block_empty_when_master_on_but_lookup_explicitly_off() -> None:
    block = builder._okf_guidance_block(
        env={
            "MAGI_KNOWLEDGE_OKF_ENABLED": "1",
            "MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED": "0",
        }
    )
    assert block == ""


def test_build_system_prompt_omits_okf_guidance_by_default() -> None:
    # Default-OFF path: no okf_guidance_block passed -> byte-absent from output.
    out = builder.build_system_prompt(
        session_key="s",
        turn_id="t",
        identity={},
        user_message={},
        now=_utc("2026-06-16T00:00:00.000Z"),
    )
    assert "knowledge_okf" not in out
    assert "OkfLookup" not in out


def test_build_system_prompt_includes_okf_guidance_once_when_passed() -> None:
    block = builder._okf_guidance_block(env=_OKF_ON_ENV)
    out = builder.build_system_prompt(
        session_key="s",
        turn_id="t",
        identity={},
        user_message={},
        now=_utc("2026-06-16T00:00:00.000Z"),
        okf_guidance_block=block,
    )
    assert out.count("<knowledge_okf>") == 1
    assert "OkfLookup" in out


def test_build_system_prompt_blocks_threads_okf_guidance_once() -> None:
    block = builder._okf_guidance_block(env=_OKF_ON_ENV)
    blocks = builder.build_system_prompt_blocks(
        session_key="s",
        turn_id="t",
        identity={},
        user_message={},
        now=_utc("2026-06-16T00:00:00.000Z"),
        okf_guidance_block=block,
    )
    text = "\n\n".join(str(b.get("text", "")) for b in blocks)
    assert text.count("<knowledge_okf>") == 1

    off = builder.build_system_prompt_blocks(
        session_key="s",
        turn_id="t",
        identity={},
        user_message={},
        now=_utc("2026-06-16T00:00:00.000Z"),
    )
    off_text = "\n\n".join(str(b.get("text", "")) for b in off)
    assert "knowledge_okf" not in off_text
