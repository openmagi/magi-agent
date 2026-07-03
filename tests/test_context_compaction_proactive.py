"""WS4 PR4a: live-loop proactive context recovery (tiers 6-7) tests.

These drive the live ``MagiContextCompactionPlugin`` (the real before-model
seam), NOT the dormant ``ContextManagementHook``. They prove that with
``MAGI_CONTEXT_COMPACTION_ENABLED`` and ``MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED``
both ON, a context that is still over ``crit * W`` after the existing tail-drop
escalates through collapse-drain (tier 6), reactive-compact (tier 7), then a
deterministic-truncation fail-safe, with measured outgoing tokens reduced to
``<= crit * W`` (or the irreducible minimum), orphan-safe, never crashing, and
that with the proactive flag OFF the plugin is byte-identical to today.

Fixtures size content in TOKENS via :func:`_filler` (space-separated short words)
so the shared estimator stays fast and the budget arithmetic is predictable; the
critical window is monkeypatched small so the gate fires on modest fixtures.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from google.genai import types

from magi_agent.adk_bridge import context_compaction as cc
from magi_agent.adk_bridge.context_compaction import (
    PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY,
    SUMMARY_FAILURE_COUNT_STATE_KEY,
    MagiContextCompactionPlugin,
    _estimate_contents_tokens,
    _repair_orphans_nonprefix,
    contents_to_msgs,
    msgs_to_contents,
)
from magi_agent.config.env import RuntimeEnvError, parse_context_compaction_env


# ---------------------------------------------------------------------------
# Hermeticity: clear any developer-shell MAGI_CONTEXT_* / MAGI_COMPACTION_* exports
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_context_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("MAGI_CONTEXT_") or key.startswith("MAGI_COMPACTION_"):
            monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _filler(n_tokens: int) -> str:
    """Roughly ``n_tokens`` of fast-tokenizing space-separated words."""
    return "tok " * n_tokens


def _text(role: str, tokens: int) -> types.Content:
    return types.Content(role=role, parts=[types.Part(text=_filler(tokens))])


def _call(call_id: str, name: str = "Read") -> types.Content:
    return types.Content(
        role="model",
        parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=name, args={"p": "x"}))],
    )


def _resp(call_id: str, name: str = "Read") -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id, name=name, response={"ok": True}
                )
            )
        ],
    )


class _Req:
    """Minimal duck-typed LlmRequest carrying ``contents`` + ``model``."""

    def __init__(self, contents: list[types.Content], model: str = "test-model") -> None:
        self.contents = contents
        self.model = model


class _FakeCallbackContext:
    """A callback context exposing a mutable dict-backed ``state`` (G6 shape)."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = {} if state is None else state


class _RaisingCaller:
    async def compact(self, messages_text: str, prompt: str) -> str:
        raise RuntimeError("summarizer boom")


class _CountingCaller:
    def __init__(self) -> None:
        self.calls = 0

    async def compact(self, messages_text: str, prompt: str) -> str:
        self.calls += 1
        return "ok summary"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def assert_no_orphan_response(contents: list[types.Content]) -> None:
    call_ids: set[str] = set()
    for c in contents:
        for p in c.parts or []:
            fc = getattr(p, "function_call", None)
            if fc is not None and getattr(fc, "id", None):
                call_ids.add(fc.id)
    for c in contents:
        for p in c.parts or []:
            fr = getattr(p, "function_response", None)
            if fr is not None:
                rid = getattr(fr, "id", None)
                if rid is not None:
                    assert rid in call_ids, f"orphaned function_response id={rid}"


def _proactive_plugin(
    *,
    enabled: bool = True,
    critical_pct: float = 0.90,
    tail_events: int = 16,
    token_threshold: int = 24_000,
    summary_max_failures: int = 3,
) -> MagiContextCompactionPlugin:
    return MagiContextCompactionPlugin(
        token_threshold=token_threshold,
        tail_events=tail_events,
        proactive_recovery_enabled=enabled,
        proactive_critical_pct=critical_pct,
        summary_max_failures=summary_max_failures,
    )


# ---------------------------------------------------------------------------
# #1 / #1b OFF byte-identity (SC-5)
# ---------------------------------------------------------------------------


def test_off_is_byte_identical_at_critical() -> None:
    contents = [_text("user" if i % 2 == 0 else "model", 100) for i in range(40)]
    original = list(contents)
    plugin = _proactive_plugin(enabled=False, token_threshold=2_000)
    req = _Req(list(contents))
    fake = _FakeCallbackContext()

    result = _run(plugin.before_model_callback(callback_context=fake, llm_request=req))

    assert result is None
    # tail-drop-only: kept tail is the last 16 ORIGINAL Content objects (identity).
    assert len(req.contents) == 16
    assert req.contents == original[24:]
    # OFF never touches state.
    assert PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY not in fake.state


def test_off_is_byte_identical_on_tail_events_early_return() -> None:
    # exactly tail_events contents -> _trim_request hits the <= tail_events
    # early-return and does NO tail-drop.
    contents = [_text("user" if i % 2 == 0 else "model", 300) for i in range(16)]
    original = list(contents)
    plugin = _proactive_plugin(enabled=False)
    req = _Req(list(contents))
    fake = _FakeCallbackContext()

    _run(plugin.before_model_callback(callback_context=fake, llm_request=req))

    assert req.contents == original  # unchanged
    assert PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY not in fake.state


# ---------------------------------------------------------------------------
# #2 / #2b escalation fires (SC-1)
# ---------------------------------------------------------------------------


def _collapse_sufficient_fixture() -> list[types.Content]:
    # Five rounds; the OLDEST middle round (r1) is HUGE so collapse-drain alone
    # gets under budget.
    contents: list[types.Content] = []
    contents += [_text("user", 20), _text("model", 20)]  # r0 small
    contents += [_text("user", 5_000), _text("model", 5_000)]  # r1 HUGE
    contents += [_text("user", 20), _text("model", 20)]  # r2 small
    contents += [_text("user", 20), _text("model", 20)]  # r3 small
    contents += [_text("user", 20), _text("model", 20)]  # r4 small (last)
    return contents


def test_escalation_fires_when_tail_still_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_window_for_model", lambda model: 11_111)
    budget = int(11_111 * 0.90)  # 9_999
    contents = _collapse_sufficient_fixture()
    plugin = _proactive_plugin()
    req = _Req(list(contents))

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert _estimate_contents_tokens(req.contents) <= budget
    assert len(req.contents) < len(contents)
    assert plugin._last_proactive_record is not None
    assert plugin._last_proactive_record["tier"] == "collapse"
    assert_no_orphan_response(req.contents)


def test_escalation_fires_on_tail_events_early_return(monkeypatch: pytest.MonkeyPatch) -> None:
    # The §1.2 headline case: exactly tail_events contents that early-return at
    # the <= tail_events branch (no tail-drop) yet are over crit*W. The window is
    # monkeypatched small so each Content is HUGE relative to crit*W.
    monkeypatch.setattr(cc, "_window_for_model", lambda model: 2_000)
    budget = int(2_000 * 0.90)  # 1_800
    contents = [_text("user" if i % 2 == 0 else "model", 200) for i in range(16)]
    plugin = _proactive_plugin()
    req = _Req(list(contents))

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert _estimate_contents_tokens(req.contents) <= budget
    assert len(req.contents) < 16
    assert plugin._last_proactive_record is not None
    assert plugin._last_proactive_record["tier"] in {"collapse", "compact"}
    assert_no_orphan_response(req.contents)


# ---------------------------------------------------------------------------
# #3 reactive-compact when collapse insufficient (SC-1 + §3.8 materialization)
# ---------------------------------------------------------------------------


def test_reactive_compact_fires_when_collapse_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cc, "_window_for_model", lambda model: 2_000)
    budget = int(2_000 * 0.90)
    contents = [_text("user" if i % 2 == 0 else "model", 200) for i in range(16)]
    original_last = contents[-1]
    plugin = _proactive_plugin()
    req = _Req(list(contents))

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # reactive output is exactly [materialized summary, last]
    assert len(req.contents) == 2
    assert req.contents[0].parts[0].text.startswith("[Conversation Summary]")
    # last_message mapped back to the IDENTICAL original Content object.
    assert req.contents[1] is original_last
    assert _estimate_contents_tokens(req.contents) <= budget
    assert plugin._last_proactive_record["tier"] == "compact"
    # adapter invariant: every emitted dict carries _orig_index (so the ONLY dict
    # lacking it downstream is the synthesized summary).
    msgs = contents_to_msgs(contents)
    assert all("_orig_index" in m for m in msgs)


# ---------------------------------------------------------------------------
# #4 / #4b orphan safety + adapter round grouping (SC-2 + §3.7)
# ---------------------------------------------------------------------------


def test_orphan_safety_after_middle_round_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_window_for_model", lambda model: 1_000)
    # Five rounds. r0/r3 hold same-round call+response pairs (E8). The HUGE r1
    # carries call c1; r2 carries c1's response (E9 cross-round). Collapse-drain
    # drops the single oldest middle round (r1, the huge one), stranding c1's
    # response in r2 -> _repair_orphans_nonprefix must remove it (interior gap).
    contents: list[types.Content] = []
    contents += [_text("user", 20), _call("c0"), _resp("c0")]  # r0 (kept first)
    contents += [_text("user", 5_000), _call("c1")]  # r1 HUGE: call only
    contents += [_text("user", 20), _resp("c1")]  # r2: response for c1 (E9)
    contents += [_text("user", 20), _call("c2"), _resp("c2")]  # r3 (E8 same-round)
    contents += [_text("user", 20), _text("model", 20)]  # r4 (last)

    plugin = _proactive_plugin()
    req = _Req(list(contents))
    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert_no_orphan_response(req.contents)
    assert len(req.contents) < len(contents)
    surviving_resp_ids = {
        getattr(p, "function_response").id
        for c in req.contents
        for p in c.parts or []
        if getattr(p, "function_response", None) is not None
    }
    assert "c1" not in surviving_resp_ids  # stranded response dropped
    assert plugin._last_proactive_record["tier"] == "collapse"

    # Direct E9 proof on the repair helper: a response whose call is absent is dropped.
    kept = [_call("kept"), _resp("kept"), _resp("missing")]
    repaired = _repair_orphans_nonprefix(kept)
    repaired_ids = {
        getattr(p, "function_response").id
        for c in repaired
        for p in c.parts or []
        if getattr(p, "function_response", None) is not None
    }
    assert repaired_ids == {"kept"}


def test_adapter_groups_funcresponse_with_call() -> None:
    from magi_agent.runtime.error_recovery.strategies.collapse_drain import (
        _partition_into_rounds,
    )

    contents = [_text("user", 5), _call("c0"), _resp("c0")]
    msgs = contents_to_msgs(contents)

    # function_response is tagged "tool" (NOT "user") so it groups with its call.
    assert msgs[2]["role"] == "tool"
    assert len(_partition_into_rounds(msgs)) == 1

    # The rejected verbatim pass-through (role="user" on the response) would split.
    verbatim = [dict(m) for m in msgs]
    verbatim[2]["role"] = "user"
    assert len(_partition_into_rounds(verbatim)) == 2


# ---------------------------------------------------------------------------
# #5 / #5b / #5c summarizer failure + breaker (SC-3 + SC-4 + §3.4a)
# ---------------------------------------------------------------------------


def _failsafe_fixture(monkeypatch: pytest.MonkeyPatch) -> list[types.Content]:
    # window small so collapse is insufficient and the deterministic fail-safe
    # has to drop a model-role tail Content (so the marker is inserted, not merged).
    monkeypatch.setattr(cc, "_window_for_model", lambda model: 200)
    return [_text("user" if i % 2 == 0 else "model", 50) for i in range(6)]


def test_summarizer_failure_falls_back_to_deterministic_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contents = _failsafe_fixture(monkeypatch)
    plugin = _proactive_plugin()
    plugin._proactive_llm_caller = _RaisingCaller()
    req = _Req(list(contents))
    fake = _FakeCallbackContext()

    # No exception propagates.
    _run(plugin.before_model_callback(callback_context=fake, llm_request=req))

    assert len(req.contents) < len(contents)
    assert any(
        (p.text or "").startswith("[older context truncated")
        for c in req.contents
        for p in c.parts or []
    )
    # independent proactive counter persisted to 1; G6 counter untouched.
    assert fake.state[PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY] == 1
    assert SUMMARY_FAILURE_COUNT_STATE_KEY not in fake.state
    assert plugin._last_proactive_record["tier"] == "failsafe"


def test_breaker_tripped_skips_tier7_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    contents = _failsafe_fixture(monkeypatch)
    plugin = _proactive_plugin(summary_max_failures=3)
    caller = _CountingCaller()
    plugin._proactive_llm_caller = caller
    req = _Req(list(contents))
    fake = _FakeCallbackContext({PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY: 3})

    _run(plugin.before_model_callback(callback_context=fake, llm_request=req))

    assert caller.calls == 0  # tier-7 LLM never called
    assert plugin._last_proactive_record["tier"] == "failsafe"
    assert len(req.contents) < len(contents)


def test_breaker_persists_across_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _proactive_plugin(summary_max_failures=3)
    fake = _FakeCallbackContext()

    # Turns 1..3 raise -> counter climbs across turns via the persisted state.
    for expected in (1, 2, 3):
        contents = _failsafe_fixture(monkeypatch)
        plugin._proactive_llm_caller = _RaisingCaller()
        req = _Req(list(contents))
        _run(plugin.before_model_callback(callback_context=fake, llm_request=req))
        assert fake.state[PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY] == expected

    # Turn 4: the count read at the start is 3 (>= max) -> tier-7 LLM skipped.
    contents = _failsafe_fixture(monkeypatch)
    counting = _CountingCaller()
    plugin._proactive_llm_caller = counting
    req = _Req(list(contents))
    _run(plugin.before_model_callback(callback_context=fake, llm_request=req))
    assert counting.calls == 0


# ---------------------------------------------------------------------------
# #6 fail-safe lower bound (SC-1)
# ---------------------------------------------------------------------------


def test_failsafe_guarantees_reduction_when_both_strategies_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cc, "_window_for_model", lambda model: 400)
    budget = int(400 * 0.90)
    # 2 rounds (collapse no-op: rounds <= 2); last message HUGE so reactive's
    # [summary, last] is still over budget -> fail-safe runs but is minimal.
    contents = [
        _text("user", 15),
        _text("model", 15),
        _text("user", 15),
        _text("model", 4_000),
    ]
    plugin = _proactive_plugin()
    req = _Req(list(contents))

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert len(req.contents) < len(contents) or _estimate_contents_tokens(
        req.contents
    ) <= budget


# ---------------------------------------------------------------------------
# #7 telemetry (SC-7)
# ---------------------------------------------------------------------------


def test_proactive_records_tier_in_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_window_for_model", lambda model: 11_111)
    contents = _collapse_sufficient_fixture()
    plugin = _proactive_plugin()
    req = _Req(list(contents))

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert plugin._last_proactive_record is not None
    assert plugin._last_proactive_record["tier"] in {"collapse", "compact", "failsafe"}
    assert "tokens_before" in plugin._last_proactive_record
    assert "tokens_after" in plugin._last_proactive_record


# ---------------------------------------------------------------------------
# #8 / #8b no escalation under budget + self-guard (SC-6 + SC-3)
# ---------------------------------------------------------------------------


def test_no_escalation_when_under_budget_after_tail_drop() -> None:
    contents = [_text("user" if i % 2 == 0 else "model", 5) for i in range(20)]
    plugin = _proactive_plugin()
    req = _Req(list(contents))

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # under crit*W -> no escalation, no strategy run.
    assert plugin._last_proactive_record is None


def test_proactive_self_guards_when_trim_request_already_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cc, "_window_for_model", lambda model: 400)
    contents = [_text("user" if i % 2 == 0 else "model", 500) for i in range(20)]
    plugin = _proactive_plugin()

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("strategy explode")

    monkeypatch.setattr(plugin, "_proactive_recover", _boom)
    req = _Req(list(contents))
    after_trim = list(contents)  # tail-drop did not fire (under token_threshold)

    # must NOT raise into the model loop.
    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert req.contents == after_trim  # left at post-cap.trim() state


# ---------------------------------------------------------------------------
# #9 env parse (§4)
# ---------------------------------------------------------------------------


def test_env_parse_defaults() -> None:
    # MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED was promoted to profile-aware
    # default-ON (_pb); make the OFF case explicit so the "disabled => not
    # proactive" intent is preserved without depending on an unset default.
    env = parse_context_compaction_env(
        {"MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED": "0"}
    )
    assert env.proactive_recovery_enabled is False
    assert env.proactive_critical_pct == 0.90

    on = parse_context_compaction_env(
        {
            "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED": "1",
            "MAGI_CONTEXT_CRITICAL_THRESHOLD": "0.85",
        }
    )
    assert on.proactive_recovery_enabled is True
    assert on.proactive_critical_pct == 0.85

    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_CONTEXT_CRITICAL_THRESHOLD": "1.5"})
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_CONTEXT_CRITICAL_THRESHOLD": "0"})
