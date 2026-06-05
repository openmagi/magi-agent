import json
import math
import time
from pathlib import Path

from magi_agent.transport.sse import InMemorySseWriter


def _data_payloads(sse_body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in sse_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def test_sse_writer_matches_simple_text_golden_stream() -> None:
    writer = InMemorySseWriter()
    receipt_ref = "receipt:sha256:" + ("b" * 64)

    writer.start()
    writer.agent({"type": "turn_start", "turnId": "turn-1", "declaredRoute": "direct"})
    writer.agent({"type": "text_delta", "delta": "hi"})
    writer.legacy_delta("hi")
    writer.agent(
        {
            "type": "turn_end",
            "turnId": "turn-1",
            "status": "committed",
            "stopReason": "end_turn",
            "receiptRef": receipt_ref,
        }
    )
    writer.legacy_finish()

    expected = (Path(__file__).parent / "fixtures" / "sse" / "simple_text.txt").read_text(
        encoding="utf-8"
    )
    assert writer.body == expected + "\n"


def test_sse_writer_matches_tool_call_golden_stream() -> None:
    writer = InMemorySseWriter()
    receipt_ref = "receipt:sha256:" + ("c" * 64)

    writer.start()
    writer.agent({"type": "turn_start", "turnId": "turn-tool-1", "declaredRoute": "direct"})
    writer.agent(
        {
            "type": "tool_start",
            "id": "call-weather-1",
            "name": "get_weather",
            "input_preview": '{"city":"Seoul"}',
        }
    )
    writer.agent(
        {
            "type": "tool_progress",
            "id": "call-weather-1",
            "label": "Fetching weather",
        }
    )
    writer.agent(
        {
            "type": "tool_end",
            "id": "call-weather-1",
            "status": "ok",
            "output_preview": '{"temperatureC":21}',
            "durationMs": 37,
        }
    )
    writer.agent({"type": "text_delta", "delta": "It is 21C in Seoul."})
    writer.legacy_delta("It is 21C in Seoul.")
    writer.agent(
            {
                "type": "turn_end",
                "turnId": "turn-tool-1",
                "status": "committed",
                "stopReason": "end_turn",
                "receiptRef": receipt_ref,
            }
        )
    writer.legacy_finish()

    expected = (Path(__file__).parent / "fixtures" / "sse" / "tool_call.txt").read_text(
        encoding="utf-8"
    )
    assert writer.body == expected + "\n"


def test_sse_writer_live_compatible_agent_text_keeps_utf8_and_legacy_done_only() -> None:
    writer = InMemorySseWriter()

    writer.start()
    writer.agent({"type": "response_clear", "turnId": "turn-utf8", "reason": "retry"})
    writer.agent({"type": "text_delta", "delta": "안녕, stream 🌊"})
    writer.agent({"type": "turn_end", "turnId": "turn-utf8", "status": "committed"})
    writer.legacy_finish()

    assert "안녕, stream 🌊" in writer.body
    assert "\\uc548" not in writer.body
    assert "data: [DONE]\n\n" in writer.body
    assert '"content":"안녕, stream 🌊"' not in writer.body

    response_clear_index = writer.body.index('"type":"response_clear"')
    text_delta_index = writer.body.index('"type":"text_delta"')
    turn_end_index = writer.body.index('"type":"turn_end"')
    done_index = writer.body.index("data: [DONE]")
    assert response_clear_index < text_delta_index < turn_end_index < done_index

    payloads = _data_payloads(writer.body)
    assert payloads[-1] == {
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    }


def test_sse_writer_drops_hidden_thinking_delta_events(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_STREAM_THINKING", raising=False)
    writer = InMemorySseWriter()

    writer.agent({"type": "thinking_delta", "delta": "private reasoning"})

    assert writer.body == ""


def test_sse_writer_emits_thinking_delta_when_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    writer = InMemorySseWriter()

    writer.agent({"type": "thinking_delta", "delta": "the model is thinking"})

    assert writer.body != ""
    payloads = _data_payloads(writer.body)
    assert len(payloads) == 1
    assert payloads[0]["type"] == "thinking_delta"
    assert "delta" in payloads[0]
    assert payloads[0]["delta"] == "the model is thinking"


def test_sse_writer_drops_unknown_private_agent_events() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "raw_provider_event",
            "prompt": "hidden prompt",
            "rawOutput": "provider secret",
            "pythonResponseAuthority": True,
        }
    )

    assert writer.body == ""


def test_sse_writer_redacts_private_marker_text_fields() -> None:
    writer = InMemorySseWriter()

    writer.agent({"type": "text_delta", "delta": "rawPayload: SECRET"})
    writer.agent(
        {
            "type": "tool_start",
            "id": "tool-private",
            "name": "PrivateTool",
            "input_preview": "rawToolUseInput: SECRET",
        }
    )
    writer.agent(
        {
            "type": "tool_end",
            "id": "tool-private",
            "status": "ok",
            "output_preview": "toolUseResponse: SECRET",
        }
    )
    writer.agent(
        {
            "type": "llm_progress",
            "turnId": "turn-private",
            "stage": "waiting",
            "detail": "hiddenReasoning: SECRET",
        }
    )
    writer.agent(
        {
            "type": "runtime_trace",
            "turnId": "turn-private",
            "phase": "retry_scheduled",
            "severity": "warning",
            "detail": "toolResult: SECRET",
        }
    )

    payloads = _data_payloads(writer.body)

    assert payloads == [
        {"type": "text_delta", "delta": "[redacted-private]"},
        {
            "type": "tool_start",
            "id": "tool-private",
            "name": "PrivateTool",
            "input_preview": "[redacted-private]",
        },
        {
            "type": "tool_end",
            "id": "tool-private",
            "status": "ok",
            "output_preview": "[redacted-private]",
        },
        {
            "type": "llm_progress",
            "turnId": "turn-private",
            "stage": "waiting",
            "detail": "[redacted-private]",
        },
        {
            "type": "runtime_trace",
            "turnId": "turn-private",
            "phase": "retry_scheduled",
            "severity": "warning",
            "detail": "[redacted-private]",
        },
    ]
    assert "SECRET" not in writer.body
    assert "rawPayload" not in writer.body
    assert "rawToolUseInput" not in writer.body
    assert "toolUseResponse" not in writer.body
    assert "hiddenReasoning" not in writer.body
    assert "toolResult" not in writer.body


def test_sse_writer_redacts_logs_marker_text_fields() -> None:
    markers = ("toolLogs", "rawToolLogs", "functionLogs", "rawFunctionLogs")

    for marker in markers:
        writer = InMemorySseWriter()
        value = f"{marker}: customer-123"

        writer.agent({"type": "text_delta", "delta": value})
        writer.agent(
            {
                "type": "tool_start",
                "id": "tool-private",
                "name": "PrivateTool",
                "input_preview": value,
            }
        )
        writer.agent(
            {
                "type": "tool_end",
                "id": "tool-private",
                "status": "ok",
                "output_preview": value,
            }
        )
        writer.agent(
            {
                "type": "llm_progress",
                "turnId": "turn-private",
                "stage": "waiting",
                "detail": value,
            }
        )
        writer.agent(
            {
                "type": "runtime_trace",
                "turnId": "turn-private",
                "phase": "retry_scheduled",
                "severity": "warning",
                "detail": value,
            }
        )

        payloads = _data_payloads(writer.body)

        assert payloads[0]["delta"] == "[redacted-private]"
        assert payloads[1]["input_preview"] == "[redacted-private]"
        assert payloads[2]["output_preview"] == "[redacted-private]"
        assert payloads[3]["detail"] == "[redacted-private]"
        assert payloads[4]["detail"] == "[redacted-private]"
        assert marker not in writer.body
        assert "customer-123" not in writer.body


def test_sse_writer_turn_end_requires_receipt_and_bounded_usage() -> None:
    writer = InMemorySseWriter()
    receipt_ref = "receipt:sha256:" + ("e" * 64)

    writer.agent(
        {
            "type": "turn_end",
            "turnId": "turn-missing-receipt",
            "status": "committed",
            "usage": {
                "inputTokens": 2,
                "outputTokens": 3,
                "costUsd": 0.01,
            },
        }
    )
    writer.agent(
        {
            "type": "turn_end",
            "turnId": "turn-oversized-usage",
            "status": "committed",
            "receiptRef": receipt_ref,
            "usage": {
                "inputTokens": 10_000_001,
                "outputTokens": 3,
                "costUsd": 0.01,
            },
        }
    )
    writer.agent(
        {
            "type": "turn_end",
            "turnId": "turn-overflow-usage",
            "status": "committed",
            "receiptRef": receipt_ref,
            "usage": {
                "inputTokens": 10**400,
                "outputTokens": 3,
                "costUsd": 0.01,
            },
        }
    )

    assert _data_payloads(writer.body) == [
        {
            "type": "turn_end",
            "turnId": "turn-missing-receipt",
            "status": "aborted",
            "reason": "missing_runtime_receipt",
        },
        {
            "type": "turn_end",
            "turnId": "turn-oversized-usage",
            "status": "committed",
            "receiptRef": receipt_ref,
        },
        {
            "type": "turn_end",
            "turnId": "turn-overflow-usage",
            "status": "committed",
            "receiptRef": receipt_ref,
        },
    ]


def test_sse_writer_redacts_session_assignment_and_refs_in_public_text() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "text_delta",
            "delta": "session=unsafe-token session/turn-123 memory/ROOT.md",
        }
    )

    body = writer.body
    assert "session=[redacted]" in body
    assert "session/turn-123" not in body
    assert "memory/ROOT.md" not in body
    assert "unsafe-token" not in body


def test_sse_writer_redacts_composio_tool_previews() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "tool_start",
            "id": "tool-composio",
            "name": "Composio",
            "input_preview": (
                "open https://connect.composio.dev/link/ln_secret "
                "connectedAccountId: acct_live_12345 "
                "x-composio-session: sess_123"
            ),
        }
    )
    writer.agent(
        {
            "type": "tool_end",
            "id": "tool-composio",
            "status": "ok",
            "output_preview": (
                "done https://connect.composio.dev/link/ln_secret "
                "connectedAccountId: acct_live_12345 "
                "x-composio-session: sess_123"
            ),
        }
    )

    body = writer.body
    assert "ln_secret" not in body
    assert "acct_live_12345" not in body
    assert "sess_123" not in body
    assert "[redacted-composio-connect-url]" in body
    assert "[redacted-composio-id]" in body
    assert "[redacted-composio-secret]" in body


def test_sse_writer_preserves_only_valid_bounded_browser_frame_images() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "browser_frame",
            "action": "observe",
            "url": "https://example.test/frame",
            "imageBase64": "dGlueS1mcmFtZQ==",
            "contentType": "image/png",
            "capturedAt": 1710000200,
            "sessionId": "browser-session-secret",
            "cdpToken": "browser-session-secret",
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "observe",
            "url": "https://example.test/missing-image",
            "contentType": "image/png",
            "capturedAt": 1710000201,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "observe",
            "url": "https://example.test/malformed-image",
            "imageBase64": "not base64?",
            "contentType": "image/png",
            "capturedAt": 1710000202,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "observe",
            "url": "https://example.test/oversized-image",
            "imageBase64": "A" * 1_000_001,
            "contentType": "image/png",
            "capturedAt": 1710000203,
        }
    )

    assert _data_payloads(writer.body) == [
        {
            "type": "browser_frame",
            "action": "observe",
            "url": "https://example.test/frame",
            "imageBase64": "dGlueS1mcmFtZQ==",
            "contentType": "image/png",
            "capturedAt": 1710000200,
        }
    ]
    assert "browser-session-secret" not in writer.body
    assert "missing-image" not in writer.body
    assert "malformed-image" not in writer.body
    assert "oversized-image" not in writer.body


def test_sse_writer_defaults_browser_frame_metadata_for_image_only_event() -> None:
    writer = InMemorySseWriter()

    before_ms = int(time.time() * 1000)
    writer.agent(
        {
            "type": "browser_frame",
            "imageBase64": "aW1hZ2VPbmx5",
        }
    )
    after_ms = int(time.time() * 1000)

    payloads = _data_payloads(writer.body)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["type"] == "browser_frame"
    assert payload["action"] == "browser"
    assert payload["imageBase64"] == "aW1hZ2VPbmx5"
    assert payload["contentType"] == "image/png"
    captured_at = payload.get("capturedAt")
    assert isinstance(captured_at, int | float)
    assert before_ms <= captured_at <= after_ms + 1000


def test_sse_writer_truncates_browser_frame_action_like_ts_safe_agent_event() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "browser_frame",
            "action": "a" * 80,
            "imageBase64": "dHJ1bmNhdGVkQWN0aW9u",
            "contentType": "image/png",
            "capturedAt": 1710000205,
        }
    )

    assert _data_payloads(writer.body) == [
        {
            "type": "browser_frame",
            "action": ("a" * 61) + "...",
            "imageBase64": "dHJ1bmNhdGVkQWN0aW9u",
            "contentType": "image/png",
            "capturedAt": 1710000205,
        }
    ]


def test_sse_writer_redacts_browser_frame_action_before_public_emit() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "browser_frame",
            "action": "observe /Users/kevin/.ssh/id_rsa Authorization: Bearer unsafe-token",
            "imageBase64": "cmVkYWN0QWN0aW9u",
            "contentType": "image/png",
            "capturedAt": 1710000206,
        }
    )

    body = writer.body
    payloads = _data_payloads(body)
    assert len(payloads) == 1
    assert payloads[0]["action"] == "observe [redacted-path] Authorization: Bearer [redacted]"
    assert "/Users/kevin/.ssh/id_rsa" not in body
    assert "unsafe-token" not in body


def test_sse_writer_drops_unknown_nested_control_event_types() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "control_event",
            "seq": 1,
            "event": {
                "type": "raw_provider_event",
                "rawOutput": "provider secret",
            },
        }
    )

    assert writer.body == ""


def test_sse_writer_sanitizes_text_delta_and_legacy_delta_public_text() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "text_delta",
            "delta": (
                "model saw Authorization: Bearer live.SECRET "
                "token=ghp_abcdefghijklmnopqrstuvwxyz "
                "key=sk-live-secret path=/workspace/private/project"
            ),
        }
    )
    writer.legacy_delta(
        "legacy saw Authorization: Bearer legacy.SECRET "
        "token=ghp_legacysecret key=sk-legacy-secret path=/data/bots/bot-secret"
    )

    payloads = _data_payloads(writer.body)

    assert "live.SECRET" not in writer.body
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in writer.body
    assert "sk-live-secret" not in writer.body
    assert "/workspace/private/project" not in writer.body
    assert "legacy.SECRET" not in writer.body
    assert "ghp_legacysecret" not in writer.body
    assert "sk-legacy-secret" not in writer.body
    assert "/data/bots/bot-secret" not in writer.body
    assert payloads[0]["delta"] == (
        "model saw Authorization: Bearer [redacted] "
        "token=[redacted] key=[redacted] path=[redacted-path]"
    )
    assert payloads[1]["choices"][0]["delta"]["content"] == (
        "legacy saw Authorization: Bearer [redacted] "
        "token=[redacted] key=[redacted] path=[redacted-path]"
    )


def test_sse_writer_preserves_long_assistant_and_legacy_deltas_after_redaction() -> None:
    writer = InMemorySseWriter()
    assistant_tail = "A" * 470
    legacy_tail = "B" * 470
    assistant_delta = (
        "assistant Authorization: Bearer live.SECRET "
        "path=/workspace/private/project "
        f"tail={assistant_tail}"
    )
    legacy_delta = (
        "legacy token=ghp_abcdefghijklmnopqrstuvwxyz "
        "path=/data/bots/bot-secret "
        f"tail={legacy_tail}"
    )

    writer.agent({"type": "text_delta", "delta": assistant_delta})
    writer.legacy_delta(legacy_delta)

    payloads = _data_payloads(writer.body)
    text_delta = payloads[0]["delta"]
    legacy_content = payloads[1]["choices"][0]["delta"]["content"]
    expected_text_delta = (
        "assistant Authorization: Bearer [redacted] "
        "path=[redacted-path] "
        f"tail={assistant_tail}"
    )
    expected_legacy_content = (
        "legacy token=[redacted] "
        "path=[redacted-path] "
        f"tail={legacy_tail}"
    )

    assert text_delta == expected_text_delta
    assert legacy_content == expected_legacy_content
    assert len(text_delta) > 450
    assert len(legacy_content) > 450
    assert not text_delta.endswith("...")
    assert not legacy_content.endswith("...")
    assert "live.SECRET" not in writer.body
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in writer.body
    assert "/workspace/private/project" not in writer.body
    assert "/data/bots/bot-secret" not in writer.body


def test_sse_writer_normalizes_and_sanitizes_camel_preview_aliases() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "tool_start",
            "id": "call-camel-1",
            "name": "read_secret",
            "inputPreview": (
                "Authorization: Bearer input.SECRET "
                "key=sk-input-secret path=/workspace/private/input.txt"
            ),
        }
    )
    writer.agent(
        {
            "type": "tool_end",
            "id": "call-camel-1",
            "status": "ok",
            "outputPreview": (
                "token=ghp_outputsecret "
                "path=/data/bots/bot-secret/output.txt"
            ),
        }
    )

    payloads = _data_payloads(writer.body)

    assert "input.SECRET" not in writer.body
    assert "sk-input-secret" not in writer.body
    assert "/workspace/private/input.txt" not in writer.body
    assert "ghp_outputsecret" not in writer.body
    assert "/data/bots/bot-secret/output.txt" not in writer.body
    assert "inputPreview" not in payloads[0]
    assert "outputPreview" not in payloads[1]
    assert payloads[0]["input_preview"] == (
        "Authorization: Bearer [redacted] key=[redacted] path=[redacted-path]"
    )
    assert payloads[1]["output_preview"] == "token=[redacted] path=[redacted-path]"


def test_sse_writer_drops_raw_output_aliases_on_public_events() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "tool_end",
            "id": "call-raw-1",
            "status": "ok",
            "output_preview": "visible redacted summary",
            "rawOutput": "Authorization: Bearer raw.CAMEL token=ghp_rawcamel",
            "raw_output": "Authorization: Bearer raw.SNAKE token=ghp_rawsnake",
        }
    )

    payloads = _data_payloads(writer.body)

    assert payloads == [
        {
            "type": "tool_end",
            "id": "call-raw-1",
            "status": "ok",
            "output_preview": "visible redacted summary",
        }
    ]
    assert "rawOutput" not in writer.body
    assert "raw_output" not in writer.body
    assert "raw.CAMEL" not in writer.body
    assert "raw.SNAKE" not in writer.body
    assert "ghp_rawcamel" not in writer.body
    assert "ghp_rawsnake" not in writer.body


def test_sse_writer_sanitizes_direct_tool_preview_events() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "tool_start",
            "id": "call-secret-1",
            "name": "get_secret",
            "input_preview": (
                '{"authorization":"Bearer direct.SECRET","api_key":"direct-secret",'
                f'"payload":"{"x" * 450}"}}'
            ),
        }
    )
    writer.agent(
        {
            "type": "tool_end",
            "id": "call-secret-1",
            "status": "ok",
            "output_preview": (
                '{"github":"ghr_abcdefghijklmnopqrstuvwxyz0123456789",'
                f'"openai":"sk-direct-secret","payload":"{"y" * 450}"}}'
            ),
            "durationMs": 1,
        }
    )

    agent_payloads = _data_payloads(writer.body)

    assert "Bearer direct.SECRET" not in writer.body
    assert "direct-secret" not in writer.body
    assert "ghr_abcdefghijklmnopqrstuvwxyz0123456789" not in writer.body
    assert "sk-direct-secret" not in writer.body
    assert '\\"authorization\\":\\"[redacted]\\"' in writer.body
    assert "[redacted]" in writer.body
    assert len(agent_payloads[0]["input_preview"]) == 400
    assert agent_payloads[0]["input_preview"].endswith("...")
    assert len(agent_payloads[1]["output_preview"]) == 400
    assert agent_payloads[1]["output_preview"].endswith("...")


def test_sse_writer_redacts_quoted_secret_values_with_spaces_and_escapes() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "tool_start",
            "id": "call-secret-2",
            "name": "get_secret",
            "input_preview": (
                '{"password":"alpha beta gamma",'
                '"secret":"alpha \\"beta\\" gamma",'
                '"github_oauth":"gho_abcdefghijklmnopqrstuvwxyz0123456789",'
                '"github_user":"ghu_abcdefghijklmnopqrstuvwxyz0123456789"}'
            ),
        }
    )
    writer.agent(
        {
            "type": "tool_end",
            "id": "call-secret-2",
            "status": "ok",
            "output_preview": (
                '{"token": "delta epsilon zeta",'
                '"api_key": "delta \\"epsilon\\" zeta"}'
            ),
            "durationMs": 1,
        }
    )

    agent_payloads = _data_payloads(writer.body)

    input_preview = agent_payloads[0]["input_preview"]
    output_preview = agent_payloads[1]["output_preview"]
    assert "alpha beta gamma" not in input_preview
    assert 'alpha \\"beta\\" gamma' not in input_preview
    assert "beta gamma" not in input_preview
    assert '\\"beta\\" gamma' not in input_preview
    assert "gho_abcdefghijklmnopqrstuvwxyz0123456789" not in input_preview
    assert "ghu_abcdefghijklmnopqrstuvwxyz0123456789" not in input_preview
    assert "delta epsilon zeta" not in output_preview
    assert 'delta \\"epsilon\\" zeta' not in output_preview
    assert "epsilon zeta" not in output_preview
    assert '\\"epsilon\\" zeta' not in output_preview
    assert input_preview.count("[redacted]") >= 4
    assert output_preview.count("[redacted]") == 2


def test_sse_writer_redacts_unquoted_secret_values_with_spaces() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "tool_start",
            "id": "call-secret-3",
            "name": "get_secret",
            "input_preview": "token=alpha beta gamma, api_key: delta epsilon zeta",
        }
    )

    agent_payloads = _data_payloads(writer.body)

    input_preview = agent_payloads[0]["input_preview"]
    assert "alpha beta gamma" not in input_preview
    assert "beta gamma" not in input_preview
    assert "delta epsilon zeta" not in input_preview
    assert "epsilon zeta" not in input_preview
    assert input_preview == "token=[redacted], api_key: [redacted]"


def test_sse_writer_redacts_error_detail_and_message_public_fields() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "runtime_trace",
            "turnId": "turn-1",
            "phase": "terminal_abort",
            "severity": "error",
            "title": "ADK event error",
            "detail": (
                "failed with SUPABASE_SERVICE_ROLE_KEY=supabase-service-role "
                "STRIPE_SECRET_KEY=stripe-live-secret, "
                + ("x" * 500)
            ),
        }
    )
    writer.agent(
        {
            "type": "error",
            "code": "bad",
            "message": (
                "failed with ANTHROPIC_API_KEY=anthropic-live-secret "
                "refresh_token=refresh-token-value, "
                + ("y" * 500)
            ),
        }
    )

    agent_payloads = _data_payloads(writer.body)

    assert "supabase-service-role" not in writer.body
    assert "stripe-live-secret" not in writer.body
    assert "anthropic-live-secret" not in writer.body
    assert "refresh-token-value" not in writer.body
    assert len(agent_payloads[0]["detail"]) == 400
    assert len(agent_payloads[1]["message"]) == 400
    assert agent_payloads[0]["detail"].endswith("...")
    assert agent_payloads[1]["message"].endswith("...")


def test_sse_writer_context_end_drops_private_summary_payload() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "context_end",
            "eventId": "evt-context-1",
            "turnId": "turn-1",
            "reason": "compacted",
            "summaryText": (
                "private memory summary token=ghp_contextsecret "
                "path=/data/bots/bot-secret/context"
            ),
            "memoryProviderPayload": "private memory payload",
        }
    )

    agent_payloads = _data_payloads(writer.body)

    assert agent_payloads == [{"type": "context_end"}]
    assert "summaryText" not in writer.body
    assert "private memory summary" not in writer.body
    assert "ghp_contextsecret" not in writer.body
    assert "/data/bots/bot-secret/context" not in writer.body
    assert "private memory payload" not in writer.body


def test_sse_writer_removes_compaction_boundary_private_summary_payload() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "compaction_boundary",
            "eventId": "evt-compact-1",
            "turnId": "turn-1",
            "boundaryId": "compact-1",
            "summaryHash": "sha256:public-hash",
            "beforeTokenCount": 2048,
            "afterTokenCount": 512,
            "createdAt": 1779000030,
            "summaryText": (
                "private compaction memory token=ghp_abcdefghijklmnopqrstuvwxyz "
                "path=/data/bots/bot-secret"
            ),
            "summary": "private summary",
            "memoryProviderPayload": "private memory payload",
        }
    )

    agent_payloads = _data_payloads(writer.body)

    assert agent_payloads == [
        {
            "type": "compaction_boundary",
            "eventId": "evt-compact-1",
            "turnId": "turn-1",
            "boundaryId": "compact-1",
            "summaryHash": "sha256:public-hash",
            "beforeTokenCount": 2048,
            "afterTokenCount": 512,
            "createdAt": 1779000030,
        }
    ]
    assert "summaryText" not in writer.body
    assert "private compaction memory" not in writer.body
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in writer.body
    assert "/data/bots/bot-secret" not in writer.body
    assert "private summary" not in writer.body
    assert "private memory payload" not in writer.body


def test_sse_writer_drops_non_finite_numeric_fields_from_public_events() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "tool_end",
            "id": "tool-non-finite",
            "status": "ok",
            "durationMs": math.nan,
            "createdAt": math.inf,
        }
    )
    writer.agent(
        {
            "type": "llm_progress",
            "turnId": "turn-non-finite",
            "label": "Calling model",
            "stage": "started",
            "iter": math.nan,
            "elapsedMs": math.inf,
        }
    )
    writer.agent(
        {
            "type": "research_artifact_delta",
            "claims": [
                {
                    "claimId": "claim-non-finite",
                    "text": "Public claim",
                    "confidence": math.nan,
                }
            ],
        }
    )
    writer.agent(
        {
            "type": "tournament_result",
            "winnerIndex": math.inf,
            "variants": [
                {"variantIndex": 0, "score": math.nan},
                {"variantIndex": math.inf, "score": 0.5},
            ],
        }
    )
    writer.agent(
        {
            "type": "compaction_boundary",
            "boundaryId": "compact-non-finite",
            "beforeTokenCount": math.nan,
            "afterTokenCount": math.inf,
            "createdAt": -math.inf,
        }
    )

    payloads = _data_payloads(writer.body)

    assert "NaN" not in writer.body
    assert "Infinity" not in writer.body
    assert payloads[0] == {
        "type": "tool_end",
        "id": "tool-non-finite",
        "status": "ok",
    }
    assert payloads[1] == {
        "type": "llm_progress",
        "turnId": "turn-non-finite",
        "stage": "started",
        "label": "Calling model",
    }
    assert payloads[2] == {
        "type": "research_artifact_delta",
        "claims": [{"claimId": "claim-non-finite", "text": "Public claim"}],
    }
    assert payloads[3] == {
        "type": "tournament_result",
        "variants": [{"variantIndex": 0}, {"score": 0.5}],
    }
    assert payloads[4] == {
        "type": "compaction_boundary",
        "boundaryId": "compact-non-finite",
    }


def test_sse_writer_drops_oversized_finite_numeric_fields_from_public_events() -> None:
    writer = InMemorySseWriter()
    oversized = 10**100

    writer.agent(
        {
            "type": "tool_progress",
            "id": "tool-oversized",
            "label": "Working",
            "progress": oversized,
            "createdAt": oversized,
        }
    )
    writer.agent(
        {
            "type": "tool_end",
            "id": "tool-oversized",
            "status": "ok",
            "durationMs": oversized,
            "createdAt": oversized,
        }
    )
    writer.agent(
        {
            "type": "llm_progress",
            "turnId": "turn-oversized",
            "stage": "waiting",
            "iter": oversized,
            "elapsedMs": oversized,
        }
    )
    writer.agent(
        {
            "type": "heartbeat",
            "turnId": "turn-oversized",
            "iter": oversized,
            "elapsedMs": oversized,
            "lastEventAt": oversized,
        }
    )
    writer.agent(
        {
            "type": "runtime_trace",
            "turnId": "turn-oversized",
            "phase": "retry_scheduled",
            "severity": "warning",
            "attempt": oversized,
            "maxAttempts": oversized,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "imageBase64": "aGVsbG8=",
            "capturedAt": oversized,
        }
    )

    payloads = _data_payloads(writer.body)

    assert str(oversized) not in writer.body
    assert payloads == [
        {"type": "tool_progress", "id": "tool-oversized", "label": "Working"},
        {"type": "tool_end", "id": "tool-oversized", "status": "ok"},
        {
            "type": "llm_progress",
            "turnId": "turn-oversized",
            "stage": "waiting",
        },
        {"type": "heartbeat", "turnId": "turn-oversized"},
        {
            "type": "runtime_trace",
            "turnId": "turn-oversized",
            "phase": "retry_scheduled",
            "severity": "warning",
        },
        {
            "type": "browser_frame",
            "action": "browser",
            "imageBase64": "aGVsbG8=",
            "contentType": "image/png",
        },
    ]


def test_sse_writer_pr7_redacts_browser_frame_auth_and_session_url_paths() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "browser_frame",
            "action": "open",
            "url": "/sessions/sess_public_123",
            "imageBase64": "YQ==",
            "contentType": "image/png",
            "capturedAt": 1710000207,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "open",
            "url": "https://example.com/auth/callback/public-code",
            "imageBase64": "Yg==",
            "contentType": "image/png",
            "capturedAt": 1710000208,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "open",
            "url": "/callback?code=public-code&state=public-state",
            "imageBase64": "Yw==",
            "contentType": "image/png",
            "capturedAt": 1710000209,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "open",
            "url": "/app?code=public-code#public-state",
            "imageBase64": "ZA==",
            "contentType": "image/png",
            "capturedAt": 1710000210,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "open",
            "url": "example.com/app?state=oauth-state#frag",
            "imageBase64": "ZQ==",
            "contentType": "image/png",
            "capturedAt": 1710000211,
        }
    )

    payloads = _data_payloads(writer.body)
    assert payloads[0].get("url") is None
    assert payloads[1].get("url") == "https://example.com"
    assert payloads[2].get("url") is None
    assert payloads[3].get("url") == "/app"
    assert payloads[4].get("url") == "example.com/app"
    assert "/sessions/" not in writer.body
    assert "sess_public_123" not in writer.body
    assert "/auth/" not in writer.body
    assert "public-code" not in writer.body
    assert "public-state" not in writer.body
    assert "oauth-state" not in writer.body


def test_sse_writer_pr7_redacts_encoded_and_stripe_browser_frame_urls() -> None:
    writer = InMemorySseWriter()
    sample_stripe = "sk" + "_live_" + ("a" * 24)

    writer.agent(
        {
            "type": "browser_frame",
            "action": "open",
            "url": "/sessions%2Fsess_public_123",
            "imageBase64": "Zg==",
            "contentType": "image/png",
            "capturedAt": 1710000212,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "open",
            "url": "https://example.com/callback%2Fpublic-code",
            "imageBase64": "Zw==",
            "contentType": "image/png",
            "capturedAt": 1710000213,
        }
    )
    writer.agent(
        {
            "type": "browser_frame",
            "action": "open",
            "url": "https://example.com/" + sample_stripe,
            "imageBase64": "aA==",
            "contentType": "image/png",
            "capturedAt": 1710000214,
        }
    )

    payloads = _data_payloads(writer.body)
    assert payloads[0].get("url") is None
    assert payloads[1].get("url") == "https://example.com"
    assert payloads[2].get("url") is None
    assert "sessions%2F" not in writer.body
    assert "callback%2F" not in writer.body
    assert "sess_public_123" not in writer.body
    assert "public-code" not in writer.body
    assert sample_stripe not in writer.body


def test_sse_writer_pr7_document_draft_is_preview_only_and_ts_compatible() -> None:
    writer = InMemorySseWriter()
    private_content = "private raw draft body"

    writer.agent(
        {
            "type": "document_draft",
            "id": "draft-public",
            "filename": "/Users/kevin/private/report.md",
            "format": "md",
            "contentPreview": "# Public\n" + ("A" * 6_050),
            "content": private_content,
            "rawContent": private_content,
            "sourceSnapshot": private_content,
        }
    )

    payloads = _data_payloads(writer.body)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload == {
        "type": "document_draft",
        "id": "draft-public",
        "filename": "[redacted-path]",
        "format": "md",
        "contentPreview": "# Public\n" + ("A" * 5_988) + "...",
        "contentLength": 6_000,
        "truncated": True,
    }
    assert private_content not in writer.body
    assert "rawContent" not in writer.body
    assert "sourceSnapshot" not in writer.body


def test_sse_writer_pr7_document_draft_redacts_auth_and_session_paths() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "document_draft",
            "id": "draft-public",
            "filename": "reports/public.md?code=public-code",
            "format": "md",
            "contentPreview": (
                "redirect=/sessions/sess_public_123 "
                "callback=/callback?code=public-code#public-state "
                "docs=/docs?state=oauth-state#frag"
            ),
        }
    )

    payloads = _data_payloads(writer.body)
    assert len(payloads) == 1
    assert payloads[0]["filename"] == "[redacted-path]"
    assert "[redacted-path]" in str(payloads[0]["contentPreview"])
    assert "callback?code" not in writer.body
    assert "public-code" not in writer.body
    assert "public-state" not in writer.body
    assert "oauth-state" not in writer.body
    assert "/sessions/" not in writer.body
    assert "sess_public_123" not in writer.body


def test_sse_writer_pr7_document_draft_redacts_nested_and_punctuated_routes() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "document_draft",
            "id": "draft-public",
            "filename": "reports/session/sess_public_123.md",
            "format": "md",
            "contentPreview": (
                "redirect=/api/sessions/sess_public_123 "
                "callback=https://example.com/v1/auth/callback/public-code"
            ),
        }
    )
    writer.agent(
        {
            "type": "document_draft",
            "id": "draft-public-2",
            "filename": "reports/public.md",
            "format": "md",
            "contentPreview": "see,/sessions/sess_public_123 and next,/auth/callback/public-code",
        }
    )

    payloads = _data_payloads(writer.body)
    assert len(payloads) == 2
    assert payloads[0]["filename"] == "[redacted-path]"
    assert "[redacted-path]" in str(payloads[0]["contentPreview"])
    assert "[redacted-path]" in str(payloads[1]["contentPreview"])
    assert "reports/session" not in writer.body
    assert "/api/sessions/" not in writer.body
    assert "sess_public_123" not in writer.body
    assert "/v1/auth/" not in writer.body
    assert "/auth/" not in writer.body
    assert "public-code" not in writer.body


def test_sse_writer_pr7_document_draft_redacts_encoded_routes_and_split_tokens() -> None:
    writer = InMemorySseWriter()
    sample_github = "github" + "_pat_" + ("a" * 24)
    sample_stripe = "sk" + "_live_" + ("b" * 24)

    writer.agent(
        {
            "type": "document_draft",
            "id": "draft-public",
            "filename": "reports/callback%2Fpublic-code.md",
            "format": "md",
            "contentPreview": (
                "callback=https://example.com/callback%2Fpublic-code "
                "session=/sessions%2Fsess_public_123 "
                f"values={sample_github} {sample_stripe}"
            ),
        }
    )

    body = writer.body
    payloads = _data_payloads(body)
    assert len(payloads) == 1
    assert payloads[0]["filename"] == "[redacted-path]"
    assert "[redacted-path]" in str(payloads[0]["contentPreview"])
    assert "[redacted]" in str(payloads[0]["contentPreview"])
    assert "callback%2F" not in body
    assert "sessions%2F" not in body
    assert "public-code" not in body
    assert "sess_public_123" not in body
    assert sample_github not in body
    assert sample_stripe not in body


def test_sse_writer_pr9_source_inspected_redacts_auth_callback_uri() -> None:
    for uri in [
        "https://example.test/oauth/callback?code=abc123&state=secretstate#session-frag",
        "https://example.test/oauth-callback?code=abc123&state=secretstate#session-frag",
        "https://example.test/oauth_callback?code=abc123&state=secretstate#session-frag",
        "https://example.test/oauth%2Dcallback?code=abc123&state=secretstate",
        "https://example.test/oauth%252Dcallback?code=abc123&state=secretstate",
        "https://example.test/public?c%6f%64%65=abc123",
        "https://example.test/public%253Fc%256f%2564%2565=abc123",
        "https://example.test/public?c%6f%64%65=abc123&state=secretstate",
        "https://example.test/public%253Fc%256f%2564%2565=abc123%2526state=secretstate",
        "https://example.test/public%3Fcode=abc123&state=secretstate",
        "https://example.test/public?callback=abc123&next=/safe",
        "https://example.test/public#callback=abc123",
        "https://example.test/public%23callback=abc123",
        "https://example.test/public%2523callback=abc123",
        "https://example.test/oauth%252Fcallback%253Fcode=abc123%2526state=secretstate",
        "https://example.test/public%253Fcode=abc123%2526state=secretstate",
    ]:
        writer = InMemorySseWriter()

        writer.agent(
            {
                "type": "source_inspected",
                "source": {
                    "sourceId": "src-public",
                    "kind": "browser",
                    "uri": uri,
                    "title": "Public source",
                    "contentHash": "receipt:sha256:" + ("d" * 64),
                },
            }
        )

        payloads = _data_payloads(writer.body)
        assert len(payloads) == 1
        assert "abc123" not in payloads[0]["source"]["uri"]
        assert "secretstate" not in payloads[0]["source"]["uri"]
        assert "abc123" not in writer.body
        assert "secretstate" not in writer.body
        if "public" in uri:
            assert payloads[0]["source"]["uri"] == "https://example.test/public[redacted-query]"
        else:
            assert payloads[0]["source"]["uri"] == "[redacted-path]"
            assert "oauth" not in writer.body
            assert "callback" not in writer.body


def test_sse_writer_pr9_source_inspected_redacts_callback_uri_from_text_fields() -> None:
    writer = InMemorySseWriter()

    writer.agent(
        {
            "type": "source_inspected",
            "source": {
                "sourceId": "src-public",
                "kind": "browser",
                "uri": "https://example.test/public",
                "title": (
                    "Opened https://example.test/oauth/callback?"
                    "code=abc123&state=secretstate"
                ),
                "contentHash": "receipt:sha256:" + ("d" * 64),
                "snippets": [
                    (
                        "Read https://example.test/public?"
                        "c%6f%64%65=abc123&state=secretstate"
                    ),
                ],
            },
        }
    )

    payloads = _data_payloads(writer.body)

    assert len(payloads) == 1
    assert "abc123" not in writer.body
    assert "secretstate" not in writer.body
    assert "oauth" not in writer.body
    assert "callback" not in writer.body
    assert payloads[0]["source"]["title"] == "Opened [redacted-path]"
    assert payloads[0]["source"]["snippets"] == [
        "Read https://example.test/public[redacted-query]",
    ]
