"""HAL item 03 — interval-based in-context facts-survey replanning.

These tests exercise :class:`FactsReplanControl` (the ADK ``on_before_model``
adapter) against the *real* control plane plus the pure helpers in
``magi_agent.runtime.facts_replan`` — no survey logic is re-implemented here.

Default-OFF / inert contract:

* ``parse_facts_replan_env({})`` is ``None`` and neither ``build_default_plane``
  with an empty env nor a no-arg ``build_default_plugin()`` registers the
  control, so every existing caller is byte-identical to before.
* A plane built with an empty env, driven repeatedly through ``_before_model``
  with a dict-fake request, leaves ``llm_request["contents"]`` untouched.

Activation (flag ON) appends exactly one user-role survey instruction every N
model iterations after the first, capped per (session, turn), without ever
touching tools and without ever raising into the model loop (fail-soft).
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from magi_agent.adk_bridge.control_plane import (
    build_default_plane,
    build_default_plugin,
)
from magi_agent.adk_bridge.facts_replan_control import (
    FACTS_REPLAN_CONTROL_NAME,
    FactsReplanControl,
    build_facts_replan_control,
)
from magi_agent.runtime.facts_replan import (
    FactsReplanConfig,
    build_survey_message,
    parse_facts_replan_env,
    should_inject_survey,
)


_FLAG_ON = {"MAGI_FACTS_REPLAN_ENABLED": "1"}


def _run(coro):
    return asyncio.run(coro)


class _FakeSession:
    def __init__(self, session_id: str):
        self.id = session_id


class _FakeCallbackContext:
    def __init__(self, session_id: str = "session-1", invocation_id: str = "turn-1"):
        self.session = _FakeSession(session_id)
        self.invocation_id = invocation_id


def _control(
    *, interval: int = 4, max_surveys: int = 5, max_tracked: int = 128
) -> FactsReplanControl:
    return FactsReplanControl(
        FactsReplanConfig(
            interval=interval,
            max_surveys_per_turn=max_surveys,
            max_tracked_turns=max_tracked,
        )
    )


def _drive(control: FactsReplanControl, ctx: Any, request: Any) -> None:
    _run(control.on_before_model(callback_context=ctx, llm_request=request))


def _has_facts_replan(controls) -> bool:
    return any(isinstance(c, FactsReplanControl) for c in controls)


def _content_text(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("content", ""))
    parts = getattr(content, "parts", None) or []
    return "".join(
        text for text in (getattr(p, "text", None) for p in parts) if isinstance(text, str)
    )


# ---------------------------------------------------------------------------
# 1. Default-OFF zero-behavior proof
# ---------------------------------------------------------------------------


_FLAG_OFF = {"MAGI_FACTS_REPLAN_ENABLED": "0"}


def test_disabled_parse_returns_none() -> None:
    assert parse_facts_replan_env(dict(_FLAG_OFF)) is None


def test_disabled_not_registered_in_plane() -> None:
    plane = build_default_plane(os_environ=dict(_FLAG_OFF))
    assert not _has_facts_replan(plane._controls)


def test_disabled_plugin_has_no_control() -> None:
    plugin = build_default_plugin(dict(_FLAG_OFF))
    assert not _has_facts_replan(plugin._p._controls)


def test_disabled_request_untouched_through_plane() -> None:
    plane = build_default_plane(os_environ=dict(_FLAG_OFF))
    request = {"contents": [{"role": "user", "content": "hello"}]}
    before = copy.deepcopy(request)
    for _ in range(12):
        _run(
            plane._before_model(
                callback_context=_FakeCallbackContext(), llm_request=request
            )
        )
    assert request == before


# ---------------------------------------------------------------------------
# 2. Registration
# ---------------------------------------------------------------------------


def test_flag_on_registers_control() -> None:
    plane = build_default_plane(os_environ=dict(_FLAG_ON))
    assert _has_facts_replan(plane._controls)


def test_flag_off_values_not_registered() -> None:
    for value in ("0", "", "false", "no"):
        plane = build_default_plane(
            os_environ={"MAGI_FACTS_REPLAN_ENABLED": value}
        )
        assert not _has_facts_replan(plane._controls), f"registered for {value!r}"


def test_interval_zero_env_not_registered() -> None:
    plane = build_default_plane(
        os_environ={**_FLAG_ON, "MAGI_FACTS_REPLAN_INTERVAL": "0"}
    )
    assert not _has_facts_replan(plane._controls)


def test_build_facts_replan_control_off_returns_none() -> None:
    assert build_facts_replan_control(dict(_FLAG_OFF)) is None


def test_build_facts_replan_control_on_returns_named_control() -> None:
    control = build_facts_replan_control(dict(_FLAG_ON))
    assert isinstance(control, FactsReplanControl)
    assert control.name == FACTS_REPLAN_CONTROL_NAME == "magi_facts_replan"


# ---------------------------------------------------------------------------
# 3. Injection timing
# ---------------------------------------------------------------------------


def test_injection_timing_interval_4() -> None:
    ctrl = _control(interval=4)
    ctx = _FakeCallbackContext()
    request: dict[str, Any] = {"contents": []}

    counts = []
    for _ in range(9):
        _drive(ctrl, ctx, request)
        counts.append(len(request["contents"]))

    # Calls 1-4 append nothing; call 5 appends exactly one; 6-8 nothing; 9 the second.
    assert counts == [0, 0, 0, 0, 1, 1, 1, 1, 2]
    assert all(c["role"] == "user" for c in request["contents"])


def test_single_call_turn_never_injected() -> None:
    ctrl = _control(interval=1)
    request: dict[str, Any] = {"contents": [{"role": "user", "content": "hi"}]}
    _drive(ctrl, _FakeCallbackContext(), request)
    assert request["contents"] == [{"role": "user", "content": "hi"}]


def test_should_inject_survey_pure_contract() -> None:
    # model_calls <= 1 never injects.
    assert should_inject_survey(
        model_calls=1, interval=1, surveys_used=0, max_surveys=5
    ) is False
    # interval=4 → due before calls 5, 9, 13.
    due = [
        n
        for n in range(1, 14)
        if should_inject_survey(model_calls=n, interval=4, surveys_used=0, max_surveys=5)
    ]
    assert due == [5, 9, 13]
    # Cap exhausted → never due.
    assert should_inject_survey(
        model_calls=5, interval=4, surveys_used=5, max_surveys=5
    ) is False


def test_object_request_appends_genai_or_dict_content() -> None:
    ctrl = _control(interval=4)
    ctx = _FakeCallbackContext()

    class _Req:
        def __init__(self) -> None:
            self.contents: list[Any] = []

    request = _Req()
    for _ in range(5):
        _drive(ctrl, ctx, request)

    assert len(request.contents) == 1
    assert "facts survey" in _content_text(request.contents[0])


# ---------------------------------------------------------------------------
# 4. Message contract
# ---------------------------------------------------------------------------


def test_survey_message_contract_sections_in_order() -> None:
    msg = build_survey_message(steps_so_far=4, survey_index=1, max_surveys=5)
    required_in_order = [
        "Pause before your next action and write a facts survey:",
        "Facts GIVEN in the task",
        "Facts LEARNED so far",
        "Facts still to LOOK UP",
        "Facts to DERIVE or compute",
        "REMAINING",
        "This survey supersedes any earlier plan or survey — do not restate "
        "or defend earlier plans; rebuild from evidence so far.",
        "You have used 4 working steps; this is consolidation 1 of at most 5.",
        "Then continue with the next concrete action.",
    ]
    positions = [msg.find(section) for section in required_in_order]
    assert all(p >= 0 for p in positions), f"missing sections: {positions}"
    assert positions == sorted(positions), "sections out of order"
    # Unverified facts are marked using the LedgerFactKind vocabulary.
    assert "working guess" in msg.lower()


def test_survey_message_budget_integers() -> None:
    msg = build_survey_message(steps_so_far=8, survey_index=2, max_surveys=3)
    assert "You have used 8 working steps; this is consolidation 2 of at most 3." in msg


def test_injected_message_matches_builder_output() -> None:
    ctrl = _control(interval=4, max_surveys=5)
    ctx = _FakeCallbackContext()
    request: dict[str, Any] = {"contents": []}
    for _ in range(5):
        _drive(ctrl, ctx, request)
    expected = build_survey_message(steps_so_far=4, survey_index=1, max_surveys=5)
    assert request["contents"][0] == {"role": "user", "content": expected}


# ---------------------------------------------------------------------------
# 5. Per-turn cap
# ---------------------------------------------------------------------------


def test_cap_max_surveys_per_turn() -> None:
    ctrl = _control(interval=4, max_surveys=2)
    ctx = _FakeCallbackContext()
    request: dict[str, Any] = {"contents": []}

    injected_at = []
    for call in range(1, 18):
        before = len(request["contents"])
        _drive(ctrl, ctx, request)
        if len(request["contents"]) > before:
            injected_at.append(call)

    assert injected_at == [5, 9]


# ---------------------------------------------------------------------------
# 6. Per-(session, turn) isolation
# ---------------------------------------------------------------------------


def test_per_turn_isolation_interleaved() -> None:
    ctrl = _control(interval=4)
    ctx_a = _FakeCallbackContext(session_id="s1", invocation_id="turn-a")
    ctx_b = _FakeCallbackContext(session_id="s2", invocation_id="turn-b")
    req_a: dict[str, Any] = {"contents": []}
    req_b: dict[str, Any] = {"contents": []}

    for _ in range(4):
        _drive(ctrl, ctx_a, req_a)
        _drive(ctrl, ctx_b, req_b)
    assert req_a["contents"] == [] and req_b["contents"] == []

    # Each turn's 5th call injects independently.
    _drive(ctrl, ctx_a, req_a)
    assert len(req_a["contents"]) == 1 and req_b["contents"] == []
    _drive(ctrl, ctx_b, req_b)
    assert len(req_b["contents"]) == 1


# ---------------------------------------------------------------------------
# 7. State bound (FIFO eviction)
# ---------------------------------------------------------------------------


def test_state_bound_fifo_eviction() -> None:
    ctrl = _control(max_tracked=4)
    for i in range(6):
        ctx = _FakeCallbackContext(session_id="s", invocation_id=f"turn-{i}")
        _drive(ctrl, ctx, {"contents": []})

    assert len(ctrl._turns) <= 4
    assert ("s", "turn-0") not in ctrl._turns
    assert ("s", "turn-1") not in ctrl._turns
    assert ("s", "turn-5") in ctrl._turns


# ---------------------------------------------------------------------------
# 8. Fail-soft
# ---------------------------------------------------------------------------


def test_failsoft_callback_context_without_session() -> None:
    ctrl = _control(interval=1)

    class _NoSessionContext:
        invocation_id = "turn-1"

    request: dict[str, Any] = {"contents": []}
    for _ in range(6):
        assert (
            _run(
                ctrl.on_before_model(
                    callback_context=_NoSessionContext(), llm_request=request
                )
            )
            is None
        )
    assert request["contents"] == []


def test_failsoft_unresolvable_turn_id() -> None:
    ctrl = _control(interval=1)

    class _NoTurnContext:
        session = _FakeSession("session-1")
        invocation_id = None

    request: dict[str, Any] = {"contents": []}
    for _ in range(6):
        _drive(ctrl, _NoTurnContext(), request)
    assert request["contents"] == []
    assert len(ctrl._turns) == 0


def test_failsoft_builder_raises(monkeypatch) -> None:
    import magi_agent.adk_bridge.facts_replan_control as mod

    def _boom(**_kwargs: Any) -> str:
        raise RuntimeError("survey builder broke")

    monkeypatch.setattr(mod, "build_survey_message", _boom)
    ctrl = _control(interval=4)
    ctx = _FakeCallbackContext()
    request: dict[str, Any] = {"contents": []}

    for _ in range(5):
        assert (
            _run(ctrl.on_before_model(callback_context=ctx, llm_request=request))
            is None
        )
    assert request["contents"] == []


# ---------------------------------------------------------------------------
# 9. Env parsing
# ---------------------------------------------------------------------------


def test_parse_defaults_when_flag_on() -> None:
    assert parse_facts_replan_env(dict(_FLAG_ON)) == FactsReplanConfig()


def test_parse_invalid_interval_falls_back_to_default() -> None:
    cfg = parse_facts_replan_env({**_FLAG_ON, "MAGI_FACTS_REPLAN_INTERVAL": "abc"})
    assert cfg is not None
    assert cfg.interval == 4


def test_parse_invalid_max_falls_back_to_default() -> None:
    cfg = parse_facts_replan_env({**_FLAG_ON, "MAGI_FACTS_REPLAN_MAX_PER_TURN": "x"})
    assert cfg is not None
    assert cfg.max_surveys_per_turn == 5


def test_parse_nonpositive_interval_or_max_is_off() -> None:
    assert (
        parse_facts_replan_env({**_FLAG_ON, "MAGI_FACTS_REPLAN_INTERVAL": "-3"}) is None
    )
    assert (
        parse_facts_replan_env({**_FLAG_ON, "MAGI_FACTS_REPLAN_INTERVAL": "0"}) is None
    )
    assert (
        parse_facts_replan_env({**_FLAG_ON, "MAGI_FACTS_REPLAN_MAX_PER_TURN": "0"})
        is None
    )


def test_parse_custom_values() -> None:
    cfg = parse_facts_replan_env(
        {
            **_FLAG_ON,
            "MAGI_FACTS_REPLAN_INTERVAL": "2",
            "MAGI_FACTS_REPLAN_MAX_PER_TURN": "1",
        }
    )
    assert cfg == FactsReplanConfig(interval=2, max_surveys_per_turn=1)


def test_parse_truthy_and_explicit_falsy_variants() -> None:
    for value in ("1", "true", "yes", "on", " TRUE "):
        assert (
            parse_facts_replan_env({"MAGI_FACTS_REPLAN_ENABLED": value}) is not None
        ), f"expected ON for {value!r}"
    # Profile-aware default-ON (_pb): only the explicit-falsy tokens disable it;
    # empty string is in the FALSE set, but unrecognised values resolve to the
    # (full) profile default and are covered by the profile cases elsewhere.
    for value in ("0", "false", "off", ""):
        assert (
            parse_facts_replan_env({"MAGI_FACTS_REPLAN_ENABLED": value}) is None
        ), f"expected OFF for {value!r}"


def test_config_env_accessor_and_reexport() -> None:
    from magi_agent.config.env import (
        is_facts_replan_enabled,
        parse_facts_replan_env as env_parse,
    )

    assert is_facts_replan_enabled(dict(_FLAG_OFF)) is False
    assert is_facts_replan_enabled(dict(_FLAG_ON)) is True
    assert env_parse(dict(_FLAG_ON)) == FactsReplanConfig()
    assert env_parse(dict(_FLAG_OFF)) is None


def test_flags_registered_in_registry() -> None:
    from magi_agent.config.flags import get_flag

    enabled = get_flag("MAGI_FACTS_REPLAN_ENABLED")
    assert enabled.kind == "profile_bool"
    assert enabled.default is None

    interval = get_flag("MAGI_FACTS_REPLAN_INTERVAL")
    assert interval.kind == "int"
    assert interval.default == 4

    max_per_turn = get_flag("MAGI_FACTS_REPLAN_MAX_PER_TURN")
    assert max_per_turn.kind == "int"
    assert max_per_turn.default == 5
