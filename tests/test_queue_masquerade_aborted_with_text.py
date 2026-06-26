"""Queue masquerade 2nd-pass regression (aborted-with-text turn end).

PR-I closes the gap left by #686 ("fix(serve+web): finalize empty turns + hold
queue on mid-task stop").

Repro per Kevin's 0.1.86 local-serve session:

* Turn A: user asks for a long Tesla 10-K analysis. The local headless engine
  emits visible ``text_delta`` events and ``tool_end`` events, then closes the
  turn with ``status=aborted reason=missing_runtime_receipt`` (the OSS local
  runner has no receipt infrastructure, so the projection downgrades a
  committed-without-receipt to aborted; the streaming-chat surface reconciles
  back to committed but the user-visible final text never materialized).
  Dashboard rendered "Work started, but no final answer text arrived. Please
  try again."
* Turn B: user types a fresh casual greeting ("hi") expecting a hi-greeting
  back.

Pre-fix, the local ``/v1/chat/stream`` route extracted the new-turn prompt by
**joining every user-authored content block in body["messages"]**. The
dashboard sends the full conversation history each turn (OpenAI-compat shape),
so Turn B's body still carries Turn A's long Tesla request as the first user
message. Joining them produced ``"<long Tesla 10-K request>\\nhi"`` as the
new prompt, the LLM kept executing the Tesla task, and the user's "hi" was
ignored (the prior-turn context bled into the new turn, the queue-masquerade
shape).

The fix takes only the LATEST user message as the new-turn prompt. Prior turns
already live in the ADK session events; joining them duplicates context into
the prompt and lets a long prior request drown out a short new one. The
content-block join inside that single latest user message is preserved
(multimodal text blocks per turn still concatenate as before).
"""

from __future__ import annotations

from magi_agent.transport.chat_routes import _local_chat_prompt_text
from magi_agent.transport.streaming_chat_route import _extract_prompt_text

_TESLA_10K_REQUEST = (
    "Analyze the latest Tesla 10-K filing. Pull every business-segment "
    "revenue line, every risk-factor change vs the prior year, and the "
    "vehicle-delivery guidance vs analyst consensus, then produce a "
    "side-by-side memo with citations to the SEC filing."
)

_DASHBOARD_EMPTY_TURN_FALLBACK = (
    "⚠️ Work started, but no final answer text arrived. Please try again."
)


def _kevin_repro_body() -> dict:
    """Build the exact body shape the dashboard sends on Turn B.

    The history carries:
      1. Turn A's user message (Tesla 10-K)
      2. The fallback assistant message the dashboard rendered when Turn A
         ended without visible final text
      3. Turn B's fresh user greeting "hi"
    """

    return {
        "messages": [
            {"role": "user", "content": _TESLA_10K_REQUEST},
            {"role": "assistant", "content": _DASHBOARD_EMPTY_TURN_FALLBACK},
            {"role": "user", "content": "hi"},
        ]
    }


def test_streaming_chat_extract_prompt_takes_only_latest_user_message() -> None:
    """Kevin 0.1.86 repro: Turn B's prompt must be only "hi"."""

    prompt = _extract_prompt_text(_kevin_repro_body())

    assert prompt == "hi", (
        "Turn B's prompt must be only the latest user message (queue "
        "masquerade 2nd-pass after #686). The aborted Turn A's Tesla "
        "10-K text must not bleed into the new turn's prompt."
    )
    # Belt-and-suspenders: the prior-turn Tesla text must be ABSENT.
    assert "Tesla" not in prompt
    assert "10-K" not in prompt


def test_local_adk_chat_prompt_text_takes_only_latest_user_message() -> None:
    """Same 2nd-pass fix applied to the legacy local ADK chat path."""

    prompt = _local_chat_prompt_text(_kevin_repro_body())

    assert prompt == "hi"
    assert "Tesla" not in prompt
    assert "10-K" not in prompt


def test_extract_prompt_text_returns_latest_user_when_streaming_was_in_flight() -> None:
    """Mirror the queue-drain timing: user types Turn B while Turn A's
    aborted assistant fallback is the most recent assistant entry, then a
    third turn arrives via the queue. Only the LATEST user entry must win."""

    body = {
        "messages": [
            {"role": "user", "content": _TESLA_10K_REQUEST},
            {"role": "assistant", "content": _DASHBOARD_EMPTY_TURN_FALLBACK},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "what's the weather"},
        ]
    }

    assert _extract_prompt_text(body) == "what's the weather"
    assert _local_chat_prompt_text(body) == "what's the weather"


def test_latest_user_message_with_multimodal_text_blocks_joins_within_turn() -> None:
    """Within the LATEST user message, multiple text blocks still concatenate
    (per-turn multimodal content). This preserves the existing single-turn
    content-block join path; only the across-turns join is removed."""

    body = {
        "messages": [
            {"role": "user", "content": _TESLA_10K_REQUEST},
            {"role": "assistant", "content": "..."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize"},
                    {"type": "text", "text": "in three bullets"},
                ],
            },
        ]
    }

    prompt = _extract_prompt_text(body)
    assert prompt == "Summarize\nin three bullets"
    assert "Tesla" not in prompt


def test_no_user_messages_returns_empty_string() -> None:
    """An all-assistant transcript yields the empty prompt (no user input)."""

    body = {
        "messages": [
            {"role": "assistant", "content": "out-of-band assistant text"},
            {"role": "system", "content": "system note"},
        ]
    }
    assert _extract_prompt_text(body) == ""
    assert _local_chat_prompt_text(body) == ""


def test_single_user_message_path_is_unchanged() -> None:
    """First-turn (single user message) behavior is byte-identical to pre-fix."""

    body = {"messages": [{"role": "user", "content": "hello world"}]}
    assert _extract_prompt_text(body) == "hello world"
    assert _local_chat_prompt_text(body) == "hello world"


def test_missing_role_treated_as_user_still_works() -> None:
    """A bare ``{"content": "..."}`` payload (no role) is still treated as
    the latest user message."""

    body = {"messages": [{"content": "bare message"}]}
    assert _extract_prompt_text(body) == "bare message"
    assert _local_chat_prompt_text(body) == "bare message"
