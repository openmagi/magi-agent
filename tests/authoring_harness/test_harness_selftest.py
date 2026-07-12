"""Self-tests for the authoring QA harness itself (its own TDD).

Part 1 (U1): ScriptedLlm, the ``use_scripted_llm`` injection helper covering
both conversational routes, and the two magi-agent turn-API adapters.

Part 2 (U2): the invariant engine (I1..I9) and the persisted-state oracles.

Everything here is ZERO-network: a scripted fake model stands in for the LLM
and the adapters run against an in-process ``TestClient``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Part 1: ScriptedLlm
# ---------------------------------------------------------------------------


def test_scripted_llm_yields_in_order() -> None:
    from benchmarks.authoring.fakes import ScriptedLlm

    scripted = ScriptedLlm(["first", "second"])
    factory = scripted.as_factory()

    import asyncio

    async def _drive(text_marker: str) -> str:
        from magi_agent.customize.shacl_compiler import _invoke_llm

        model = factory()
        return await _invoke_llm(
            model, text_marker, system_instruction="sys", prior_turns=()
        )

    assert asyncio.run(_drive("p1")) == "first"
    assert asyncio.run(_drive("p2")) == "second"


def test_scripted_llm_strict_exhaustion_raises() -> None:
    from benchmarks.authoring.fakes import ScriptedLlm, ScriptExhaustedError

    scripted = ScriptedLlm(["only"])
    factory = scripted.as_factory()

    import asyncio

    async def _drive() -> str:
        from magi_agent.customize.shacl_compiler import _invoke_llm

        model = factory()
        return await _invoke_llm(model, "p", system_instruction="s", prior_turns=())

    assert asyncio.run(_drive()) == "only"
    with pytest.raises(ScriptExhaustedError):
        asyncio.run(_drive())


def test_scripted_llm_captures_prompt_and_system() -> None:
    from benchmarks.authoring.fakes import ScriptedLlm

    scripted = ScriptedLlm(["r"])
    factory = scripted.as_factory()

    import asyncio

    async def _drive() -> None:
        from magi_agent.customize.shacl_compiler import _invoke_llm

        model = factory()
        await _invoke_llm(
            model,
            "the user prompt",
            system_instruction="the system persona",
            prior_turns=({"role": "user", "content": "earlier"},),
        )

    asyncio.run(_drive())
    assert len(scripted.capture_log) == 1
    cap = scripted.capture_log[0]
    assert cap.system_instruction == "the system persona"
    assert cap.prompt == "the user prompt"
    # prior turns are captured for golden assertions (e.g. answers reflected)
    assert any("earlier" in c for c in cap.contents)
    assert "the user prompt" in cap.contents[-1]


# ---------------------------------------------------------------------------
# Part 1: use_scripted_llm covers BOTH routes
# ---------------------------------------------------------------------------

_TOKEN = "test-gateway-token"


def _runtime():
    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from magi_agent.app import create_app

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def test_use_scripted_llm_patches_route_a_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route-A injection seam is wired to our factory.

    DRIFT (magi-agent bug at HEAD 60bc91f8a): route A's live LLM path is dead
    because ``_INTERACTIVE_SYSTEM_INSTRUCTION_TMPL.format(...)`` raises
    ``KeyError`` on the literal JSON-example braces in the template BEFORE the
    model factory is ever called; ``step_compile`` catches it and falls back to
    the deterministic "can't reach the AI compiler" path. So the scripted
    envelope cannot land in the draft over route A today. We therefore pin the
    SEAM (our factory replaced the production one) rather than end-to-end draft
    mutation. This test flips to end-to-end automatically once the engine
    template bug is fixed upstream.
    """
    import magi_agent.cli.wiring as wiring

    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.injection import use_scripted_llm

    envelope = json.dumps(
        {
            "assistant_message": "Which tool?",
            "draft_updates": {"what": {"kind": "tool_perm"}},
            "questions": [],
        }
    )
    scripted = ScriptedLlm([envelope])
    use_scripted_llm(monkeypatch, scripted)
    # The seam is our factory now.
    assert wiring._build_criterion_model_factory() is scripted

    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile-interactive",
        json={
            "history": [{"role": "user", "content": "block the Bash tool"}],
            "draft_so_far": {},
            "answers": {},
        },
    )
    # Route stays honest: 200 with a deterministic-fallback envelope.
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready_to_save"] is False


def test_use_scripted_llm_drives_route_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.injection import use_scripted_llm

    envelope = json.dumps(
        {
            "assistant_message": "Which label?",
            "param_updates": {"gatedTool": "execute_trade"},
        }
    )
    scripted = ScriptedLlm([envelope])
    use_scripted_llm(monkeypatch, scripted)

    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/v1/app/policies/compile/interactive",
        json={
            "history": [{"role": "user", "content": "gate execute_trade on a source"}],
            "paramsSoFar": {},
            "answers": {},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["params"]["gatedTool"] == "execute_trade"
    assert len(scripted.capture_log) == 1


# ---------------------------------------------------------------------------
# Part 1: adapters
# ---------------------------------------------------------------------------


def test_rule_flow_adapter_roundtrips_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.adapter import MagiRuleFlowAdapter
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.injection import use_scripted_llm

    envelope = json.dumps(
        {
            "assistant_message": "ok",
            "draft_updates": {"what": {"kind": "tool_perm"}},
            "questions": [],
        }
    )
    scripted = ScriptedLlm([envelope])
    use_scripted_llm(monkeypatch, scripted)
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")

    adapter = MagiRuleFlowAdapter(_runtime(), _TOKEN)
    assert adapter.flow == "single_rule"
    state = adapter.start(scenario=None)
    result = adapter.step(state, say="block the Bash tool", answers={})

    # 200 + normalized shape is what the adapter must guarantee. (Route A's live
    # LLM path is dead at this HEAD — see the seam test above — so the working
    # draft is the deterministic fallback, not the scripted envelope.)
    assert result.http_status == 200
    assert isinstance(result.working, dict)
    assert result.plan is None
    assert result.ready_to_save is False
    assert isinstance(result.missing, list)
    assert isinstance(result.questions, list)
    # The history echo contract: the user turn was appended.
    assert state.history[-1] == {"role": "user", "content": "block the Bash tool"}


def test_policy_flow_adapter_roundtrips_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.adapter import MagiPolicyFlowAdapter
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.injection import use_scripted_llm

    envelope = json.dumps(
        {
            "assistant_message": "ok",
            "param_updates": {"gatedTool": "execute_trade"},
        }
    )
    scripted = ScriptedLlm([envelope])
    use_scripted_llm(monkeypatch, scripted)
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))

    adapter = MagiPolicyFlowAdapter(_runtime(), _TOKEN)
    assert adapter.flow == "linked_policy"
    state = adapter.start(scenario=None)
    result = adapter.step(state, say="gate execute_trade on a source", answers={})

    assert result.http_status == 200
    assert result.working["gatedTool"] == "execute_trade"
    assert result.ready_to_save is False


def test_adapter_threads_auth_and_isolates_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.adapter import MagiRuleFlowAdapter

    # No token -> the adapter must still send the header it was constructed
    # with; a wrong token yields 401.
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    adapter = MagiRuleFlowAdapter(_runtime(), "wrong-token")
    state = adapter.start(scenario=None)
    result = adapter.step(state, say="hello", answers={})
    assert result.http_status == 401


# ===========================================================================
# Part 2 (U2): invariant engine I1..I9
# ===========================================================================


def _tr(**kw):
    """Build a TurnResult with sensible defaults for invariant tests."""
    from benchmarks.authoring.adapter import TurnResult

    base = dict(
        assistant_message="",
        working={},
        plan=None,
        missing=[],
        questions=[],
        needs_more=False,
        ready_to_save=False,
        schema_issues=[],
        raw={},
        http_status=200,
    )
    base.update(kw)
    return TurnResult(**base)


def _ids(violations):
    return sorted({v.invariant_id for v in violations})


# --- I1 shape ---


def test_i1_shape_positive() -> None:
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(http_status=200, raw={"assistant_message": "", "draft": None,
            "missing_fields": [], "questions": [], "needs_more": False,
            "ready_to_save": False, "schema_issues": []})
    assert "I1" not in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i1_shape_violation_non_200_non_error() -> None:
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(http_status=500, raw={"unexpected": True})
    assert "I1" in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i1_documented_error_envelope_is_ok() -> None:
    from benchmarks.authoring.invariants import check_invariants

    # A 200 fail-soft error envelope is a documented shape, not an I1 failure.
    r = _tr(http_status=200, raw={"ok": False, "error": "compiler_failed: x"})
    assert "I1" not in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i1_error_envelope_without_ok_key_is_ok() -> None:
    """Regression (live T3 finding): the flow-B interactive compile-policy route
    emits ``{"ready_to_save": False, "error": "compile timed out"}`` on timeout
    (transport/customize.py:858-866) with NO ``ok`` key. The old detector keyed on
    ``ok is False`` and misclassified this honest fail-soft envelope as an I1
    shape violation (missing response keys). The server contract is: a top-level
    ``error`` string IS the error envelope."""
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(http_status=200, raw={"ready_to_save": False, "error": "compile timed out"})
    assert "I1" not in _ids(check_invariants(r, flow="linked_policy", answers={}))
    # The other two documented shapes (route A/B, which DO carry ok:False):
    r_a = _tr(http_status=200, raw={"ok": False, "error": "compile timed out", "draft": None})
    assert "I1" not in _ids(check_invariants(r_a, flow="single_rule", answers={}))
    r_b = _tr(http_status=200, raw={"ok": False, "error": "compile timed out", "plan": None})
    assert "I1" not in _ids(check_invariants(r_b, flow="linked_policy", answers={}))


def test_i1_missing_keys_without_error_still_fails() -> None:
    """The widened detector must NOT swallow a genuine shape bug: a 200 response
    that is NOT an error envelope (no ``error`` key) but is missing required keys
    still hard-fails I1."""
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(http_status=200, raw={"assistant_message": "hi"})  # missing the rest, no error
    assert "I1" in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i1_error_key_but_ok_true_is_not_error_envelope() -> None:
    """Defensive: a response that carries ``ok: True`` is a success envelope even
    if it happens to include an ``error`` field, so it is still shape-checked."""
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(http_status=200, raw={"ok": True, "error": "", "assistant_message": "hi"})
    assert "I1" in _ids(check_invariants(r, flow="single_rule", answers={}))


# --- I2 ready-truth (flow A) ---


def test_i2_ready_truth_positive() -> None:
    from benchmarks.authoring.invariants import check_invariants

    valid = {"scope": "always", "enabled": True, "firesAt": "before_tool_use",
             "action": "block",
             "what": {"kind": "tool_perm", "payload": {"match": {"tool": "Bash"},
                       "decision": "deny"}}}
    r = _tr(working=valid, missing=[], ready_to_save=True)
    assert "I2" not in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i2_ready_truth_violation_lies() -> None:
    from benchmarks.authoring.invariants import check_invariants

    # ready_to_save True but the draft is incomplete -> the Save CTA lies.
    r = _tr(working={"what": {"kind": "tool_perm"}}, missing=[], ready_to_save=True)
    assert "I2" in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i2_ready_truth_flow_b_positive_and_violation() -> None:
    from benchmarks.authoring.invariants import check_invariants
    import asyncio
    from magi_agent.customize.nl_policy_interactive import step_policy_compile

    out = asyncio.run(step_policy_compile(
        history=[{"role": "user", "content": "gate execute_trade"}],
        params_so_far={}, answers={"gatedTool": "execute_trade",
        "evidenceLabel": "source credibility", "allowlistDomains": "sec.gov"},
        model_factory=None))
    good = _tr(working=out["params"], plan=out["plan"], missing=[], ready_to_save=True)
    assert "I2" not in _ids(check_invariants(good, flow="linked_policy", answers={}))
    # ready True but plan is None -> violation
    bad = _tr(working=out["params"], plan=None, ready_to_save=True)
    assert "I2" in _ids(check_invariants(bad, flow="linked_policy", answers={}))


# --- I3 question discipline ---


def test_i3_question_count_cap() -> None:
    from benchmarks.authoring.invariants import check_invariants

    qs = [{"id": f"q{i}", "prompt": "?", "kind": "text", "targets_field": "action"}
          for i in range(3)]
    r = _tr(questions=qs, missing=["action"])
    assert "I3" in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i3_targets_field_must_be_missing_flow_a() -> None:
    from benchmarks.authoring.invariants import check_invariants

    # question targets a field that is NOT in the missing set -> violation
    q = [{"id": "q", "prompt": "?", "kind": "text", "targets_field": "scope"}]
    r = _tr(questions=q, missing=["action"])
    assert "I3" in _ids(check_invariants(r, flow="single_rule", answers={}))
    ok = [{"id": "q", "prompt": "?", "kind": "text", "targets_field": "action"}]
    r2 = _tr(questions=ok, missing=["action"])
    assert "I3" not in _ids(check_invariants(r2, flow="single_rule", answers={}))


def test_i3_flow_b_degrades_to_count_only() -> None:
    from benchmarks.authoring.invariants import check_invariants

    # flow B questions carry empty targets_field; I3 checks count only.
    q = [{"id": "q", "prompt": "?", "kind": "text", "targets_field": ""}]
    r = _tr(questions=q, missing=[])
    assert "I3" not in _ids(check_invariants(r, flow="linked_policy", answers={}))


# --- I4 operator supremacy ---


def test_i4_answered_field_present() -> None:
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(working={"scope": "always", "what": {"kind": "tool_perm"}})
    v = check_invariants(r, flow="single_rule", answers={"q_scope": "always"})
    assert "I4" not in _ids(v)


def test_i4_answered_field_missing_is_violation() -> None:
    from benchmarks.authoring.invariants import check_invariants

    # operator answered scope=always but it did not land -> violation
    r = _tr(working={"what": {"kind": "tool_perm"}})
    v = check_invariants(r, flow="single_rule", answers={"q_scope": "always"})
    assert "I4" in _ids(v)


def test_i4_invalid_answer_must_be_absent_not_coerced() -> None:
    from benchmarks.authoring.invariants import check_invariants

    # an out-of-vocabulary answer must NOT appear coerced in the working state.
    r = _tr(working={"scope": "bogus", "what": {"kind": "tool_perm"}})
    v = check_invariants(r, flow="single_rule", answers={"q_scope": "bogus"})
    assert "I4" in _ids(v)


# --- I5 vocabulary containment ---


def test_i5_fixed_point_positive() -> None:
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(assistant_message="Save the rule when ready.",
            questions=[{"id": "q", "prompt": "Which tool?", "kind": "text",
                        "targets_field": "action"}], missing=["action"])
    assert "I5" not in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i5_leak_violation() -> None:
    from benchmarks.authoring.invariants import check_invariants

    # A raw internal token leaked to the wire (scrubber is NOT a fixed point).
    r = _tr(assistant_message="Use a shacl matcher at the firesAt lifecycle")
    assert "I5" in _ids(check_invariants(r, flow="single_rule", answers={}))


# --- I6 working-state hygiene ---


def test_i6_draft_allowlist_positive() -> None:
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(working={"scope": "always", "action": "block",
                     "what": {"kind": "tool_perm"}, "_payload_hint": "x"})
    assert "I6" not in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i6_draft_unknown_key_violation() -> None:
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(working={"scope": "always", "evil": "x"})
    assert "I6" in _ids(check_invariants(r, flow="single_rule", answers={}))


def test_i6_params_allowlist_and_allow_ban() -> None:
    from benchmarks.authoring.invariants import check_invariants

    ok = _tr(working={"gatedTool": "execute_trade", "onUnavailable": "deny"})
    assert "I6" not in _ids(check_invariants(ok, flow="linked_policy", answers={}))
    bad_key = _tr(working={"gatedTool": "x", "sneaky": 1})
    assert "I6" in _ids(check_invariants(bad_key, flow="linked_policy", answers={}))
    allow = _tr(working={"gatedTool": "x", "onUnavailable": "allow"})
    assert "I6" in _ids(check_invariants(allow, flow="linked_policy", answers={}))


# --- I7 consistency ---


def test_i7_needs_more_consistency() -> None:
    from benchmarks.authoring.invariants import check_invariants

    ok = _tr(missing=["action"], schema_issues=[], needs_more=True)
    assert "I7" not in _ids(check_invariants(ok, flow="single_rule", answers={}))
    bad = _tr(missing=["action"], needs_more=False)
    assert "I7" in _ids(check_invariants(bad, flow="single_rule", answers={}))


def test_i7_flow_b_plan_iff_ready_and_binding_crossmatch() -> None:
    from benchmarks.authoring.invariants import check_invariants
    import asyncio
    from magi_agent.customize.nl_policy_interactive import step_policy_compile

    out = asyncio.run(step_policy_compile(
        history=[{"role": "user", "content": "gate execute_trade"}],
        params_so_far={}, answers={"gatedTool": "execute_trade",
        "evidenceLabel": "source credibility", "allowlistDomains": "sec.gov"},
        model_factory=None))
    good = _tr(working=out["params"], plan=out["plan"], ready_to_save=True,
               needs_more=False)
    assert "I7" not in _ids(check_invariants(good, flow="linked_policy", answers={}))
    # plan present but not ready -> violation
    bad = _tr(working=out["params"], plan=out["plan"], ready_to_save=False)
    assert "I7" in _ids(check_invariants(bad, flow="linked_policy", answers={}))


# --- I9 error honesty ---


def test_i9_error_envelope_not_ready() -> None:
    from benchmarks.authoring.invariants import check_invariants

    r = _tr(raw={"ok": False, "error": "compiler_failed: x"}, ready_to_save=True)
    assert "I9" in _ids(check_invariants(r, flow="single_rule", answers={}))
    r2 = _tr(raw={"ok": False, "error": "compiler_failed: x"}, ready_to_save=False)
    assert "I9" not in _ids(check_invariants(r2, flow="single_rule", answers={}))


# ===========================================================================
# Part 2 (U2): persisted-state oracle helpers
# ===========================================================================


def _sidecar_isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )


def _rule_flow_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from benchmarks.authoring.adapter import MagiRuleFlowAdapter

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    _sidecar_isolate(tmp_path, monkeypatch)
    return MagiRuleFlowAdapter(_runtime(), _TOKEN)


_VALID_RULE = {
    "scope": "always", "enabled": True, "firesAt": "before_tool_use",
    "action": "block",
    "what": {"kind": "tool_perm",
             "payload": {"match": {"tool": "Bash"}, "decision": "deny"}},
}


def test_assert_rule_clean_and_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.authoring.oracles.persisted import (
        assert_policy_intent, assert_rule_clean,
    )

    adapter = _rule_flow_client(tmp_path, monkeypatch)
    body = dict(_VALID_RULE, intent="block bash tool", displayName="No Bash")
    resp = adapter._client.put("/v1/app/customize/custom-rules", json=body)
    rule_id = resp.json()["id"]
    snap = adapter.snapshot_persisted()

    assert_rule_clean(snap, rule_id)                       # no raise
    assert_policy_intent(snap, rule_id, "block bash tool", expected_display="No Bash")


def _intent_snap(intent: str):
    """Synthetic snapshot: one user policy referencing rule 'cr_1' with `intent`."""
    from benchmarks.authoring.adapter import PersistedSnapshot

    return PersistedSnapshot(
        store={}, customize={}, store_hash="",
        policies={"policies": [
            {"id": "p1", "ruleIds": ["cr_1"], "intent": intent, "origin": "user"}
        ]},
    )


def test_intent_server_truncation_prefix_ok_only_under_flag() -> None:
    """Live T3 finding: the server stores the policy intent truncated at 200
    chars (nl_policy_interactive `_LABEL_MAX`). A persona's long utterance yields
    a persisted intent that is an exact 200-char prefix of the actual say — an
    honest truncation, accepted ONLY when allow_server_truncation is set."""
    from benchmarks.authoring.oracles.persisted import (
        OracleFailure, assert_policy_intent,
    )

    full = "A" * 200 + "B" * 62          # 262-char actual utterance
    persisted = "A" * 200                # server-truncated at 200
    snap = _intent_snap(persisted)

    # Under the flag (t3): honest truncation passes.
    assert_policy_intent(snap, "cr_1", full, allow_server_truncation=True)
    # Without the flag (t1/t2): still an exact-match failure.
    try:
        assert_policy_intent(snap, "cr_1", full, allow_server_truncation=False)
        raise AssertionError("expected intent_mismatch without the flag")
    except OracleFailure as exc:
        assert exc.code == "intent_mismatch"


def test_intent_corruption_still_fails_under_truncation_flag() -> None:
    """The truncation allowance must NOT swallow real corruption: a persisted
    intent that is NOT a prefix of the expected say fails even under t3, and a
    persisted intent shorter/longer than the exact server cap is not a
    truncation."""
    from benchmarks.authoring.oracles.persisted import (
        OracleFailure, assert_policy_intent,
    )

    full = "A" * 200 + "B" * 62
    # (a) different text at 200 chars = corruption, not truncation.
    snap_corrupt = _intent_snap("X" * 200)
    try:
        assert_policy_intent(snap_corrupt, "cr_1", full, allow_server_truncation=True)
        raise AssertionError("expected intent_mismatch for corrupted intent")
    except OracleFailure as exc:
        assert exc.code == "intent_mismatch"

    # (b) a prefix but NOT at the server cap length is not the documented
    # truncation (e.g. arbitrary mid-string cut) -> still fails.
    snap_short = _intent_snap("A" * 150)
    try:
        assert_policy_intent(snap_short, "cr_1", full, allow_server_truncation=True)
        raise AssertionError("expected intent_mismatch for non-cap-length prefix")
    except OracleFailure as exc:
        assert exc.code == "intent_mismatch"


def test_intent_ref_count_still_hard_under_truncation_flag() -> None:
    """allow_server_truncation only touches the string leg; ref-count stays hard."""
    from benchmarks.authoring.adapter import PersistedSnapshot
    from benchmarks.authoring.oracles.persisted import (
        OracleFailure, assert_policy_intent,
    )

    # Two policies reference the same rule -> ref_count leg must fire.
    snap = PersistedSnapshot(
        store={}, customize={}, store_hash="",
        policies={"policies": [
            {"id": "p1", "ruleIds": ["cr_1"], "intent": "A" * 200, "origin": "user"},
            {"id": "p2", "ruleIds": ["cr_1"], "intent": "A" * 200, "origin": "user"},
        ]},
    )
    try:
        assert_policy_intent(snap, "cr_1", "A" * 262, allow_server_truncation=True)
        raise AssertionError("expected intent_ref_count failure")
    except OracleFailure as exc:
        assert exc.code == "intent_ref_count"


def test_assert_rule_clean_fails_when_envelope_leaked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.oracles.persisted import (
        OracleFailure, assert_rule_clean,
    )
    from benchmarks.authoring.adapter import PersistedSnapshot

    # Synthetic snapshot where the stored rule kept an intent key.
    snap = PersistedSnapshot(
        store={"verification": {"custom_rules": [
            dict(_VALID_RULE, id="cr_x", intent="leaked")]}},
        policies={}, customize={}, store_hash="")
    with pytest.raises(OracleFailure):
        assert_rule_clean(snap, "cr_x")


def test_assert_no_orphan_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.authoring.oracles.persisted import assert_no_orphan_rules

    adapter = _rule_flow_client(tmp_path, monkeypatch)
    adapter._client.put("/v1/app/customize/custom-rules",
                        json=dict(_VALID_RULE, intent="i"))
    snap = adapter.snapshot_persisted()
    assert_no_orphan_rules(snap)  # every rule referenced by a policy


def test_assert_promotion_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.authoring.oracles.persisted import assert_promotion_idempotent

    adapter = _rule_flow_client(tmp_path, monkeypatch)
    body = dict(_VALID_RULE, id="cr_fixed", intent="i")
    adapter._client.put("/v1/app/customize/custom-rules", json=body)
    snap_before = adapter.snapshot_persisted()
    # re-PUT the SAME id is an UPDATE -> zero new policies
    adapter._client.put("/v1/app/customize/custom-rules", json=body)
    snap_after = adapter.snapshot_persisted()
    assert_promotion_idempotent(snap_before, snap_after)


def test_assert_reserved_id_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.authoring.oracles.persisted import assert_reserved_id_rejected

    adapter = _rule_flow_client(tmp_path, monkeypatch)
    assert_reserved_id_rejected(adapter._client)


def test_assert_from_plan_triple(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.authoring.adapter import MagiPolicyFlowAdapter
    from benchmarks.authoring.oracles.persisted import assert_from_plan_triple
    import asyncio
    from magi_agent.customize.nl_policy_interactive import step_policy_compile

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _sidecar_isolate(tmp_path, monkeypatch)
    out = asyncio.run(step_policy_compile(
        history=[{"role": "user", "content": "gate execute_trade on sec.gov"}],
        params_so_far={}, answers={"gatedTool": "execute_trade",
        "evidenceLabel": "source credibility", "allowlistDomains": "sec.gov"},
        model_factory=None))
    adapter = MagiPolicyFlowAdapter(_runtime(), _TOKEN)
    state = adapter.start(scenario=None)
    state.plan = out["plan"]
    save = adapter.save(state, scenario=None)
    assert save.ok
    snap = adapter.snapshot_persisted()
    assert_from_plan_triple(snap, save)


def test_assert_no_double_representation_grouped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.oracles.persisted import assert_no_double_representation

    adapter = _rule_flow_client(tmp_path, monkeypatch)
    gid = "grp_test_001"
    r1 = dict(_VALID_RULE, id="cr_g1", groupId=gid)
    r2 = {"scope": "always", "enabled": True, "firesAt": "pre_final",
          "action": "block", "groupId": gid, "id": "cr_g2",
          "what": {"kind": "llm_criterion",
                   "payload": {"criterion": "Does the reply contain a credential?"}}}
    adapter._client.put("/v1/app/customize/custom-rules", json=r1)
    adapter._client.put("/v1/app/customize/custom-rules", json=r2)
    adapter._client.put(f"/v1/app/policies/{gid}",
                        json={"displayName": "grp", "ruleIds": ["cr_g1", "cr_g2"]})
    snap = adapter.snapshot_persisted()
    assert_no_double_representation(snap, gid)


def test_assert_catalog_consistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.authoring.oracles.persisted import assert_catalog_consistent

    adapter = _rule_flow_client(tmp_path, monkeypatch)
    adapter._client.put("/v1/app/customize/custom-rules",
                        json=dict(_VALID_RULE, intent="i"))
    snap = adapter.snapshot_persisted()
    assert_catalog_consistent(snap)


def test_assert_store_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.authoring.oracles.persisted import (
        OracleFailure, assert_store_untouched,
    )

    adapter = _rule_flow_client(tmp_path, monkeypatch)
    adapter._client.put("/v1/app/customize/custom-rules",
                        json=dict(_VALID_RULE, intent="i"))
    before = adapter.snapshot_persisted()
    # a compile-interactive turn must NOT write the store
    adapter._client.post("/v1/app/customize/custom-rules/compile-interactive",
                        json={"history": [{"role": "user", "content": "hi"}],
                              "draft_so_far": {}, "answers": {}})
    after = adapter.snapshot_persisted()
    assert_store_untouched(before, after)
    # negative: a save DOES change the store
    adapter._client.put("/v1/app/customize/custom-rules",
                        json=dict(_VALID_RULE, id="cr_new2", intent="i2"))
    after2 = adapter.snapshot_persisted()
    with pytest.raises(OracleFailure):
        assert_store_untouched(before, after2)
