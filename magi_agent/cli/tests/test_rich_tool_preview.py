"""Security + behavior tests for MAGI_RICH_TOOL_PREVIEW (rich tool-arg previews).

The flag lets the public activity timeline surface human-readable tool-call
argument summaries (e.g. the SpawnAgent task) instead of an opaque digest, while
keeping system/user/raw prompts redacted and always passing the secret/PII
sanitizer. Default-OFF must be byte-identical to the digesting behavior.
"""

import os
from unittest import mock

from magi_agent.adk_bridge import event_adapter as ea


def _preview(args: dict, tool: str, *, enabled: bool) -> str:
    env = {**os.environ}
    if enabled:
        env["MAGI_RICH_TOOL_PREVIEW"] = "1"
    else:
        env.pop("MAGI_RICH_TOOL_PREVIEW", None)
    with mock.patch.dict(os.environ, env, clear=True):
        return ea._public_preview(args, safe_keys=ea._rich_preview_safe_keys(tool))


def test_flag_off_digests_spawn_prompt_byte_identical() -> None:
    args = {"prompt": "Analyze the Tesla 10-K bullish case"}
    out = _preview(args, "SpawnAgent", enabled=False)
    assert "Digest" in out  # the "prompt" arg is digested (key itself redacts to [redacted-private]Digest)
    assert "Tesla" not in out
    # No-flag path equals calling _public_preview with no safe_keys at all.
    assert out == ea._public_preview(args)


def test_flag_on_surfaces_spawn_task() -> None:
    args = {"prompt": "Analyze the Tesla 10-K bullish case", "persona": "bull"}
    out = _preview(args, "SpawnAgent", enabled=True)
    assert "Analyze the Tesla 10-K bullish case" in out
    assert "bull" in out
    assert "promptDigest" not in out


def test_flag_on_still_redacts_system_and_raw_prompts() -> None:
    # These are NOT in any allowlist — they must stay digested even when ON.
    args = {
        "systemPrompt": "you are a helpful assistant with secret context",
        "rawProviderPayload": {"k": "v"},
        "childTranscript": "hidden child reasoning",
    }
    out = _preview(args, "SpawnAgent", enabled=True)
    assert "secret context" not in out
    assert "hidden child reasoning" not in out
    assert "systemPromptDigest" in out
    assert "rawProviderPayloadDigest" in out
    assert "childTranscriptDigest" in out


def test_flag_on_only_surfaces_top_level_nested_private_stays_redacted() -> None:
    # An allowlisted top-level key whose VALUE embeds a private "prompt" key:
    # the nested prompt must NOT be surfaced (top-level un-redaction only).
    args = {"task": {"summary": "do the thing", "systemPrompt": "leak me"}}
    out = _preview(args, "SpawnAgent", enabled=True)
    assert "do the thing" in out
    assert "leak me" not in out
    assert "systemPromptDigest" in out


def test_flag_on_sanitizes_secrets_inside_surfaced_arg() -> None:
    # Even a surfaced (allowlisted) value passes the secret sanitizer.
    token = "gh" + "p_" + ("x" * 36)  # synthetic GitHub-PAT shape, not a real secret
    args = {"prompt": f"use this token {token} to push"}
    out = _preview(args, "SpawnAgent", enabled=True)
    assert token not in out
    assert "use this token" in out  # surrounding task text still shown


def test_flag_on_non_allowlisted_tool_keeps_prompt_digested() -> None:
    args = {"prompt": "some task text"}
    out = _preview(args, "UnknownTool", enabled=True)
    assert "Digest" in out
    assert "some task text" not in out
