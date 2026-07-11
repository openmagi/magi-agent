"""U6: Live CLI T2 with deterministic user-sim + report emitter.

All tests use ScriptedLlm (no network). Tests follow the spec (design §12 U6):
- preflight fails when factory resolves None
- budget stop after N scenarios
- JSONL flushed per line
- report groups by code, computes M1-M7
- --promote writes a valid regression YAML
- --only repro path works
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# DeterministicUserSim tests
# ---------------------------------------------------------------------------


def test_scripted_user_sim_plays_turns_in_order() -> None:
    from benchmarks.authoring.scenario import Turn
    from benchmarks.authoring.usersim import ScriptedUserSim, Stop, UserTurn

    scenario = type("S", (), {"turns": [Turn(say="hello"), Turn(say="world")], "turn_budget": 4})()
    sim = ScriptedUserSim()
    transcript = []
    result = sim.next_turn(scenario, transcript)
    assert isinstance(result, UserTurn)
    assert result.say == "hello"
    assert result.answers == {}

    transcript.append({})
    result = sim.next_turn(scenario, transcript)
    assert isinstance(result, UserTurn)
    assert result.say == "world"

    transcript.append({})
    result = sim.next_turn(scenario, transcript)
    assert isinstance(result, Stop)


def test_scripted_user_sim_passes_through_explicit_answers() -> None:
    from benchmarks.authoring.scenario import Turn
    from benchmarks.authoring.usersim import ScriptedUserSim, UserTurn

    scenario = type("S", (), {
        "turns": [Turn(say="hi", answers={"q_action": "block"})],
        "turn_budget": 4,
    })()
    sim = ScriptedUserSim()
    result = sim.next_turn(scenario, [])
    assert isinstance(result, UserTurn)
    assert result.answers == {"q_action": "block"}


def test_deterministic_user_sim_plays_say_entries_literally() -> None:
    from benchmarks.authoring.scenario import Turn
    from benchmarks.authoring.usersim import DeterministicUserSim, Stop, UserTurn

    scenario = type("S", (), {
        "turns": [Turn(say="block Bash"), Turn(say=None, answers={"q_scope": "always"})],
        "turn_budget": 4,
        "generated": None,
    })()
    sim = DeterministicUserSim()
    result = sim.next_turn(scenario, [])
    assert isinstance(result, UserTurn)
    assert result.say == "block Bash"
    assert result.answers == {}

    result = sim.next_turn(scenario, [{}])
    assert isinstance(result, UserTurn)
    assert result.answers == {"q_scope": "always"}


def test_deterministic_user_sim_answers_from_slots_maps_known_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """answers_from_slots: canonical questions answered from slots block."""
    from benchmarks.authoring.scenario import Turn
    from benchmarks.authoring.usersim import DeterministicUserSim, UserTurn

    scenario = type("S", (), {
        "turns": [Turn(answers_from_slots=True)],
        "turn_budget": 4,
        "generated": {"slots": {"kind": "tool_perm", "firesAt": "before_tool_use", "action": "block", "scope": "always"}},
    })()
    # Simulate a prior turn that left a question asking q_action
    transcript = [
        {
            "response": {
                "questions": [
                    {"id": "q_action", "prompt": "What action?", "kind": "single_select",
                     "targets_field": "action", "options": ["block", "ask_approval"]},
                ]
            }
        }
    ]
    sim = DeterministicUserSim()
    result = sim.next_turn(scenario, transcript)
    assert isinstance(result, UserTurn)
    # The sim read q_action -> slots.action = "block"
    assert result.answers.get("q_action") == "block"


def test_deterministic_user_sim_unanswerable_question_emits_observation(
    tmp_path: Path,
) -> None:
    """Unknown question id: answer nothing, emit unanswerable_question observation."""
    from benchmarks.authoring.scenario import Turn
    from benchmarks.authoring.usersim import DeterministicUserSim, UserTurn

    scenario = type("S", (), {
        "turns": [Turn(answers_from_slots=True)],
        "turn_budget": 4,
        "generated": {"slots": {"kind": "tool_perm", "firesAt": "before_tool_use", "action": "block", "scope": "always"}},
    })()
    transcript = [
        {
            "response": {
                "questions": [
                    {"id": "q_unknown_field_xyz", "prompt": "?", "kind": "text",
                     "targets_field": "unknown_field", "options": []},
                ]
            }
        }
    ]
    sim = DeterministicUserSim()
    result = sim.next_turn(scenario, transcript)
    assert isinstance(result, UserTurn)
    # unknown question id -> not answered
    assert "q_unknown_field_xyz" not in result.answers
    # observation emitted
    assert any("unanswerable" in str(o) for o in result.observations)


def test_deterministic_user_sim_stop_at_budget() -> None:
    from benchmarks.authoring.scenario import Turn
    from benchmarks.authoring.usersim import DeterministicUserSim, Stop

    scenario = type("S", (), {
        "turns": [Turn(say="hello")],
        "turn_budget": 1,
        "generated": None,
    })()
    sim = DeterministicUserSim()
    # Turn 0 plays the script
    r = sim.next_turn(scenario, [])
    from benchmarks.authoring.usersim import UserTurn
    assert isinstance(r, UserTurn)
    # Turn 1: beyond budget
    r = sim.next_turn(scenario, [{}])
    assert isinstance(r, Stop)


# ---------------------------------------------------------------------------
# Preflight tests
# ---------------------------------------------------------------------------


def test_preflight_fails_when_route_a_factory_resolves_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight detects that the production factory yields None and raises."""
    from benchmarks.authoring.run import preflight_check, PreflightError

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    # Simulate factory returning None (no env keys configured)
    monkeypatch.setattr(
        "magi_agent.cli.wiring._build_criterion_model_factory", None
    )
    with pytest.raises(PreflightError, match="factory"):
        preflight_check(_runtime(), token="test-gateway-token")


# ---------------------------------------------------------------------------
# Budget stop test
# ---------------------------------------------------------------------------


def test_budget_stop_limits_scenario_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run stops launching new scenarios past --max-scenarios."""
    from benchmarks.authoring.run import run_cli

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )

    out_dir = tmp_path / "runs"
    result = run_cli(
        tier="t1",
        corpus_dir=_HANDWRITTEN,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=out_dir,
        max_scenarios=2,
        turn_cap=8,
        skip_preflight=True,
    )
    # At most 2 scenarios executed
    assert result.scenarios_run <= 2
    # run dir created
    run_dirs = list(out_dir.iterdir())
    assert len(run_dirs) >= 1


# ---------------------------------------------------------------------------
# JSONL flushed per line test
# ---------------------------------------------------------------------------


def test_transcript_jsonl_flushed_per_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each scenario produces a transcript.jsonl with valid per-line JSON."""
    from benchmarks.authoring.run import run_cli

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )

    out_dir = tmp_path / "runs2"
    run_cli(
        tier="t1",
        corpus_dir=_HANDWRITTEN,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=out_dir,
        max_scenarios=1,
        turn_cap=8,
        skip_preflight=True,
    )

    # Find transcript.jsonl files
    transcripts = list(out_dir.rglob("transcript.jsonl"))
    assert len(transcripts) >= 1, "no transcript.jsonl produced"
    for t in transcripts:
        for line in t.read_text("utf-8").splitlines():
            if line.strip():
                obj = json.loads(line)  # must not raise
                assert isinstance(obj, dict)


# ---------------------------------------------------------------------------
# Report groups by code, computes M1-M7
# ---------------------------------------------------------------------------


def test_report_computes_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """summary.json has M1..M7 keys; report.md contains metric table."""
    from benchmarks.authoring.run import run_cli

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )

    out_dir = tmp_path / "runs3"
    run_cli(
        tier="t1",
        corpus_dir=_HANDWRITTEN,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=out_dir,
        max_scenarios=3,
        turn_cap=8,
        skip_preflight=True,
    )

    run_dirs = sorted(out_dir.iterdir())
    assert run_dirs, "no run dir created"
    run_dir = run_dirs[-1]

    summary = json.loads((run_dir / "summary.json").read_text())
    for metric in ("M1_completion_rate", "M2_turns_to_ready", "M3_dead_end_rate",
                   "M4_question_loop_rate", "M5_forbidden_string_hits",
                   "M6_containment_violations", "M7_persisted_oracle_failures"):
        assert metric in summary, f"missing {metric}"

    report_md = (run_dir / "report.md").read_text()
    assert "M1" in report_md
    assert "M7" in report_md


# ---------------------------------------------------------------------------
# --promote writes a valid regression YAML
# ---------------------------------------------------------------------------


def test_promote_writes_valid_regression_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """promote_scenario writes a YAML that loads without schema errors."""
    from benchmarks.authoring.run import promote_scenario
    from benchmarks.authoring.runner import RunResult
    from benchmarks.authoring.scenario import load_scenario

    result = RunResult(
        scenario_id="fake_promote_test_001",
        passed=False,
        turns=2,
        first_divergence={"turn": 2, "oracle": "expect_ready", "expected": "ready=true", "got": "never ready"},
        transcript=[
            {"turn": 0, "say": "block Bash", "answers": {}, "response": {"assistant_message": "ok", "questions": []}, "http_status": 200},
            {"turn": 1, "say": None, "answers": {"q_scope": "always"}, "response": {"assistant_message": "ok", "questions": []}, "http_status": 200},
        ],
        metrics={},
    )

    out_dir = tmp_path / "regressions"
    path = promote_scenario(result, out_dir=out_dir)
    assert path.exists()
    # Must be a valid YAML scenario
    sc = load_scenario(path)
    assert sc.id == "fake_promote_test_001"


# ---------------------------------------------------------------------------
# --only repro path works
# ---------------------------------------------------------------------------


def test_only_filter_runs_exactly_one_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--only <id> runs exactly the requested scenario."""
    from benchmarks.authoring.run import run_cli
    from benchmarks.authoring.scenario import load_scenario

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )

    # Pick the first handwritten scenario id
    files = sorted(_HANDWRITTEN.glob("*.yaml"))
    assert files, "no handwritten scenarios"
    target_id = load_scenario(files[0]).id

    out_dir = tmp_path / "only-run"
    result = run_cli(
        tier="t1",
        corpus_dir=_HANDWRITTEN,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=out_dir,
        only=target_id,
        skip_preflight=True,
    )
    assert result.scenarios_run == 1


def test_run_cli_self_isolates_sidecar_and_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI must isolate the store + sidecar + NL flag ITSELF, not rely on
    the caller pre-patching them. Regression for a real leak: `python -m
    benchmarks.authoring.run --tier t1` measured the host ~/.magi sidecar and a
    prior run's domainAllowlist bled across scenarios (a from-plan linked
    scenario failed europa.eu vs a leaked sec.gov) while the pytest suite -
    which DID pre-patch - stayed green. This test deliberately does NOT patch
    default_search_bases / MAGI_CUSTOMIZE, so a regression re-appears here."""
    import magi_agent.packs.discovery as discovery

    from benchmarks.authoring.run import run_cli

    # A poisoned host sidecar base the CLI must NOT read from.
    poison = tmp_path / "host_sidecar"
    poison.mkdir()
    monkeypatch.setattr(discovery, "default_search_bases", lambda: [poison])
    monkeypatch.delenv("MAGI_CUSTOMIZE", raising=False)
    monkeypatch.delenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", raising=False)
    baseline = discovery.default_search_bases

    out_dir = tmp_path / "runs"
    # Replay the from-plan linked scenarios that exercise the sidecar.
    result = run_cli(
        tier="t1",
        corpus_dir=_CORPUS_V1,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=out_dir,
        filter_str="flow=linked_policy",
        turn_cap=8,
        skip_preflight=True,
    )
    assert result.failed == 0, [
        (r.scenario_id, r.first_divergence)
        for r in result.results
        if not r.passed
    ]
    # The CLI restored the caller's search-base function on exit.
    assert discovery.default_search_bases is baseline


def test_run_cli_judge_resolves_production_factory(
    tmp_path, monkeypatch
) -> None:
    """When --judge is set, run_cli must resolve a REAL judge model factory
    from the production wiring (_build_criterion_model_factory), not leave it
    None. Regression for a live-run finding: the judge annotation was a
    `pass # pragma: no cover` stub, so every T3 judge verdict came back
    'unknown' (NoneType has no generate_content_async). The judge stays
    advisory / non-gating regardless."""
    import benchmarks.authoring.judge as judge_mod
    from benchmarks.authoring.run import run_cli

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )

    # A fake production factory: base_factory() -> model factory -> model.
    class _FakeModel:
        async def generate_content_async(self, req, stream=False):
            class _P:
                text = '{"verdict": "pass", "confidence": 0.9, "reasoning": "ok"}'

            class _C:
                parts = [_P()]

            class _R:
                content = _C()

            yield _R()

    monkeypatch.setattr(
        "magi_agent.cli.wiring._build_criterion_model_factory",
        lambda: (lambda: _FakeModel()),
    )

    seen: dict = {}
    real_annotate = judge_mod.annotate_with_judge

    def _spy(run_result, *, judge_factory):
        model = judge_factory()
        seen["model_type"] = type(model).__name__
        return real_annotate(run_result, judge_factory=judge_factory)

    # run.py imports annotate_with_judge function-locally, so patch the source module.
    monkeypatch.setattr(judge_mod, "annotate_with_judge", _spy)

    run_cli(
        tier="t3",
        corpus_dir=_HANDWRITTEN,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=tmp_path / "runs",
        max_scenarios=1,
        turn_cap=8,
        judge=True,
        skip_preflight=True,
    )
    # The judge got a REAL model (not the None stub).
    assert seen.get("model_type") == "_FakeModel", seen
