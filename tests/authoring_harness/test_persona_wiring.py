"""Persona wiring: run_scenario drives a UserSim + T3 CLI wires PersonaUserSim.

Live-run finding (the gap this covers): ``run_scenario`` never called a
``UserSim`` — it replayed ``scenario.turns`` literally, so T2 and T3 were
identical canned replay and ``PersonaUserSim`` was dead outside the unit tests.
This file pins the wiring:

- U-A: ``run_scenario(user_sim=None)`` is byte-identical to canned replay; a
  provided ``user_sim`` drives the loop (Stop ends it; a wandering persona that
  never reaches ready gets an HONEST oracle failure, not a crash).
- U-B: ``run_cli --tier t3`` constructs a persona-driven run per (scenario,
  persona) resolved from the SAME production factory the judge uses.
- U-C: ``PersonaUserSim._call_llm_async`` reuses ``shacl_compiler._invoke_llm``
  and no longer references the removed ``_Minimal*`` shims.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

_CORPUS_V1 = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "authoring"
    / "corpus"
    / "v1"
)
_HANDWRITTEN = _CORPUS_V1 / "handwritten"


def _runtime():
    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token="test-gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _happy_scenario():
    from benchmarks.authoring.scenario import load_scenario

    return load_scenario(_HANDWRITTEN / "rule_happy_toolperm_en_001.yaml")


def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror the T1 CI fixture: tmp store + NL flag + tmp sidecar base."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )


# ---------------------------------------------------------------------------
# U-A: run_scenario(user_sim=None) is byte-identical to canned replay
# ---------------------------------------------------------------------------


def test_run_scenario_user_sim_none_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A golden scenario passes with and without an explicit user_sim=None,
    producing the SAME RunResult (canned-replay path is untouched)."""
    from benchmarks.authoring.runner import run_scenario

    _isolated(tmp_path, monkeypatch)

    sc = _happy_scenario()
    r_default = run_scenario(sc, _runtime(), token="test-gateway-token", tier="t1")

    sc2 = _happy_scenario()
    r_explicit = run_scenario(
        sc2, _runtime(), token="test-gateway-token", tier="t1", user_sim=None
    )

    assert r_default.passed is True
    assert r_explicit.passed is True
    assert r_explicit.scenario_id == r_default.scenario_id
    assert r_explicit.turns == r_default.turns
    assert r_explicit.reached_ready_at == r_default.reached_ready_at
    assert r_explicit.first_divergence == r_default.first_divergence
    # Transcript shape is identical: turn/say/answers/response/http_status.
    assert len(r_explicit.transcript) == len(r_default.transcript)
    for a, b in zip(r_default.transcript, r_explicit.transcript):
        assert a["turn"] == b["turn"]
        assert a["say"] == b["say"]
        assert a["answers"] == b["answers"]
        assert a["http_status"] == b["http_status"]


# ---------------------------------------------------------------------------
# U-A: a provided user_sim drives the loop (Stop ends it; oracle still applies)
# ---------------------------------------------------------------------------


def test_run_scenario_driven_by_user_sim_reaches_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ScriptedUserSim (which replays scenario.turns) drives run_scenario to
    the SAME passing result as canned replay — invariants + oracle still apply,
    and a Stop ends the loop cleanly."""
    from benchmarks.authoring.runner import run_scenario
    from benchmarks.authoring.usersim import ScriptedUserSim

    _isolated(tmp_path, monkeypatch)

    sc = _happy_scenario()
    result = run_scenario(
        sc,
        _runtime(),
        token="test-gateway-token",
        tier="t1",
        user_sim=ScriptedUserSim(),
    )
    assert result.passed is True, result.first_divergence
    assert result.reached_ready_at is not None
    # transcript entry shape preserved
    assert result.transcript
    for entry in result.transcript:
        assert set(entry) == {"turn", "say", "answers", "response", "http_status"}


def test_run_scenario_wandering_persona_fails_oracle_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user-sim that never supplies the answers needed to converge yields an
    HONEST oracle failure (expect_ready divergence), not an exception."""
    from benchmarks.authoring.runner import run_scenario
    from benchmarks.authoring.usersim import Stop, UserTurn

    _isolated(tmp_path, monkeypatch)

    class _WanderingSim:
        """Says an off-topic thing once, then stops — never answers questions."""

        def __init__(self) -> None:
            self._said = False

        def next_turn(self, scenario, transcript):
            if self._said:
                return Stop()
            self._said = True
            return UserTurn(say="tell me a joke", answers={})

    sc = _happy_scenario()
    result = run_scenario(
        sc,
        _runtime(),
        token="test-gateway-token",
        tier="t1",
        user_sim=_WanderingSim(),
    )
    # Honest failure: the oracle expects ready and it was never reached.
    assert result.passed is False
    assert result.first_divergence is not None


# ---------------------------------------------------------------------------
# U-B: run_cli --tier t3 constructs a persona-driven run per (scenario, persona)
# ---------------------------------------------------------------------------


def test_run_cli_t3_wires_persona_from_production_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--tier t3 --personas cooperative resolves the SAME production factory the
    judge uses (_build_criterion_model_factory) and drives run_scenario with a
    PersonaUserSim. The per-persona result id is ``<scenario_id>::<persona>``."""
    import benchmarks.authoring.runner as runner_mod
    from benchmarks.authoring.run import run_cli
    from benchmarks.authoring.usersim import PersonaUserSim

    _isolated(tmp_path, monkeypatch)

    # A fake production factory: base_factory() -> model factory -> model.
    class _FakeModel:
        async def generate_content_async(self, req, stream=False):
            class _P:
                text = '{"say": "block the Bash tool"}'

            class _C:
                parts = [_P()]

            class _R:
                content = _C()

            yield _R()

    resolved: dict = {"called": False}

    def _base_factory():
        resolved["called"] = True
        return lambda: _FakeModel()

    monkeypatch.setattr(
        "magi_agent.cli.wiring._build_criterion_model_factory", _base_factory
    )

    seen_sims: list = []
    real_run_scenario = runner_mod.run_scenario

    def _spy_run_scenario(scenario, runtime, *, token, tier="t1", user_sim=None):
        seen_sims.append((scenario.id, user_sim))
        return real_run_scenario(
            scenario, runtime, token=token, tier=tier, user_sim=user_sim
        )

    # run.py imports run_scenario by name at module load; patch the source too.
    monkeypatch.setattr(runner_mod, "run_scenario", _spy_run_scenario)
    monkeypatch.setattr("benchmarks.authoring.run.run_scenario", _spy_run_scenario)

    result = run_cli(
        tier="t3",
        corpus_dir=_HANDWRITTEN,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=tmp_path / "runs",
        only="rule_happy_toolperm_en_001",
        personas="cooperative",
        turn_cap=8,
        skip_preflight=True,
    )

    # The production factory was resolved for the persona.
    assert resolved["called"] is True
    # run_scenario was driven by a PersonaUserSim, not None.
    assert seen_sims, "run_scenario was never called"
    persona_sims = [s for _, s in seen_sims if isinstance(s, PersonaUserSim)]
    assert persona_sims, f"no PersonaUserSim passed: {seen_sims}"
    assert persona_sims[0].persona == "cooperative"
    # Per-persona result id scheme.
    ids = [r.scenario_id for r in result.results]
    assert "rule_happy_toolperm_en_001::cooperative" in ids, ids


def test_run_cli_t3_default_personas_are_all_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no --personas, T3 runs all four personas per scenario."""
    from benchmarks.authoring.run import run_cli

    _isolated(tmp_path, monkeypatch)

    class _FakeModel:
        async def generate_content_async(self, req, stream=False):
            class _P:
                text = '{"say": ""}'

            class _C:
                parts = [_P()]

            class _R:
                content = _C()

            yield _R()

    monkeypatch.setattr(
        "magi_agent.cli.wiring._build_criterion_model_factory",
        lambda: (lambda: _FakeModel()),
    )

    result = run_cli(
        tier="t3",
        corpus_dir=_HANDWRITTEN,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=tmp_path / "runs",
        only="rule_happy_toolperm_en_001",
        turn_cap=8,
        skip_preflight=True,
    )
    suffixes = sorted(r.scenario_id.split("::", 1)[-1] for r in result.results)
    assert suffixes == ["adversarial", "confused", "cooperative", "corrective"]


# ---------------------------------------------------------------------------
# U-C: PersonaUserSim live call uses _invoke_llm; _Minimal* shims removed
# ---------------------------------------------------------------------------


def test_persona_user_sim_uses_invoke_llm() -> None:
    """The persona live call reuses shacl_compiler._invoke_llm (structural pin)
    and does NOT reference the removed hand-rolled _Minimal* request shims."""
    import benchmarks.authoring.usersim as usersim

    src = inspect.getsource(usersim.PersonaUserSim._call_llm_async)
    assert "_invoke_llm" in src, "persona live call must reuse _invoke_llm"
    # No hand-rolled genai request construction (the role-less request bug).
    assert "GenerateContentRequest(" not in src, (
        "persona live call must not hand-roll a role-less genai request"
    )
    # The dead shims are gone.
    assert not hasattr(usersim, "_MinimalLlmRequest")
    assert not hasattr(usersim, "_MinimalContent")
    assert not hasattr(usersim, "_MinimalPart")


def test_persona_user_sim_scripted_llm_still_drives(tmp_path: Path) -> None:
    """The persona still generates utterances from a scripted cheap LLM after
    the _invoke_llm swap (the ScriptedLlm consumes a real LlmRequest)."""
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.usersim import PersonaUserSim, UserTurn

    persona_llm = ScriptedLlm(['{"say": "I want to block the Bash tool"}'])
    sim = PersonaUserSim(
        persona="cooperative", scripted_llm=persona_llm.as_factory()
    )
    scenario = type("S", (), {
        "turns": [],
        "turn_budget": 4,
        "generated": {"slots": {"kind": "tool_perm"}},
        "language": "en",
    })()
    r0 = sim.next_turn(scenario, [])
    assert isinstance(r0, UserTurn)
    assert r0.say == "I want to block the Bash tool"
    # The scripted fake recorded a real system-instruction + prompt.
    assert persona_llm.capture_log
    assert persona_llm.capture_log[0].prompt
