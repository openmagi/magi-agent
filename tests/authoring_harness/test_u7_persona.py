"""U7: PersonaUserSim + advisory judge annotation.

Test plan (design §12 U7):
- persona sim consumes a scripted cheap-LLM fake
- judge output is structurally validated and PROVABLY non-gating (a failing judge
  or a judge-says-fail scenario cannot change any verdict field: asserted)
- report renders the advisory box
"""
from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# PersonaUserSim: consumes a scripted cheap-LLM fake
# ---------------------------------------------------------------------------


def test_persona_user_sim_drives_turns_via_scripted_llm() -> None:
    """PersonaUserSim generates utterances from a scripted cheap LLM."""
    from benchmarks.authoring.usersim import PersonaUserSim, UserTurn
    from benchmarks.authoring.fakes import ScriptedLlm

    # The persona LLM returns a simple utterance each turn
    persona_llm = ScriptedLlm([
        '{"say": "I want to block the Bash tool"}',
        '{"say": "make it always apply"}',
    ])

    sim = PersonaUserSim(
        persona="cooperative",
        scripted_llm=persona_llm.as_factory(),
    )

    scenario = type("S", (), {
        "turns": [],  # no scripted turns; persona generates them
        "turn_budget": 4,
        "generated": {"slots": {"kind": "tool_perm", "firesAt": "before_tool_use", "action": "block", "scope": "always"}},
        "oracle": type("O", (), {"expect_ready": True})(),
        "language": "en",
    })()

    r0 = sim.next_turn(scenario, [])
    assert isinstance(r0, UserTurn)
    assert "Bash" in (r0.say or "") or r0.say is not None

    transcript = [{"response": {"questions": [], "needs_more": False}}]
    r1 = sim.next_turn(scenario, transcript)
    assert isinstance(r1, UserTurn)


def test_persona_user_sim_stops_at_budget() -> None:
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.usersim import PersonaUserSim, Stop

    # budget=1 means only one call
    persona_llm = ScriptedLlm(['{"say": "hello"}'])
    sim = PersonaUserSim(persona="cooperative", scripted_llm=persona_llm.as_factory())

    scenario = type("S", (), {
        "turns": [], "turn_budget": 1, "generated": None,
        "oracle": type("O", (), {"expect_ready": False})(),
        "language": "en",
    })()

    _r = sim.next_turn(scenario, [])
    r = sim.next_turn(scenario, [{}])
    assert isinstance(r, Stop)


def test_persona_user_sim_all_four_personas_constructible() -> None:
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.usersim import PersonaUserSim

    for persona in ("cooperative", "corrective", "confused", "adversarial"):
        llm = ScriptedLlm(['{"say": "hello"}'])
        sim = PersonaUserSim(persona=persona, scripted_llm=llm.as_factory())
        assert sim.persona == persona


# ---------------------------------------------------------------------------
# Judge: structurally validated, PROVABLY non-gating
# ---------------------------------------------------------------------------


def test_judge_output_structurally_valid() -> None:
    """JudgeAnnotation has the required fields and a non-gating advisory field."""
    from benchmarks.authoring.judge import annotate_with_judge, JudgeAnnotation
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.runner import RunResult

    judge_llm = ScriptedLlm([
        json.dumps({
            "verdict": "pass",
            "confidence": 0.9,
            "reasoning": "The flow converged correctly and persisted without orphans.",
            "suggest_promote": False,
        })
    ])

    run_result = RunResult(
        scenario_id="test_judge_001",
        passed=True,
        turns=2,
        reached_ready_at=2,
        transcript=[],
        metrics={"turns_to_ready": 2},
    )
    annotation = annotate_with_judge(run_result, judge_factory=judge_llm.as_factory())
    assert isinstance(annotation, JudgeAnnotation)
    assert annotation.verdict in ("pass", "fail", "unknown")
    assert isinstance(annotation.reasoning, str)
    assert isinstance(annotation.non_gating, bool)
    assert annotation.non_gating is True  # MUST be non-gating by design


def test_judge_cannot_change_verdict_field(tmp_path: Path) -> None:
    """A judge-says-fail cannot flip RunResult.passed — asserted structurally."""
    from benchmarks.authoring.judge import annotate_with_judge
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.runner import RunResult

    # Judge says FAIL
    judge_llm = ScriptedLlm([
        json.dumps({
            "verdict": "fail",
            "confidence": 0.99,
            "reasoning": "I think this is wrong.",
            "suggest_promote": True,
        })
    ])

    run_result = RunResult(
        scenario_id="test_nongating_001",
        passed=True,  # deterministic oracle: PASSED
        turns=2,
        transcript=[],
        metrics={},
    )
    original_passed = run_result.passed

    annotation = annotate_with_judge(run_result, judge_factory=judge_llm.as_factory())

    # The RunResult must be unchanged
    assert run_result.passed is original_passed, (
        "judge changed RunResult.passed — non-gating contract violated"
    )
    # The annotation itself may say fail
    assert annotation.verdict == "fail"
    # But it is explicitly non-gating
    assert annotation.non_gating is True


def test_judge_fail_soft_on_exception() -> None:
    """If the judge LLM raises, annotate_with_judge returns a degraded annotation."""
    from benchmarks.authoring.judge import annotate_with_judge
    from benchmarks.authoring.runner import RunResult

    def _bad_factory():
        raise RuntimeError("network error")

    run_result = RunResult(
        scenario_id="test_judge_failsoft_001",
        passed=False,
        turns=1,
        transcript=[],
        metrics={},
    )
    annotation = annotate_with_judge(run_result, judge_factory=_bad_factory)
    # Must not raise; returns degraded annotation
    assert annotation.verdict == "unknown"
    assert annotation.non_gating is True


# ---------------------------------------------------------------------------
# Report renders the advisory box in T3 mode
# ---------------------------------------------------------------------------


def test_report_renders_advisory_box_for_t3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When judge annotations are present, report.md includes the advisory box."""
    from benchmarks.authoring.report import write_report
    from benchmarks.authoring.runner import RunResult
    from benchmarks.authoring.judge import JudgeAnnotation

    run_results = [
        RunResult(
            scenario_id="sc_001",
            passed=True,
            turns=2,
            reached_ready_at=2,
            transcript=[],
            metrics={"turns_to_ready": 2},
        ),
        RunResult(
            scenario_id="sc_002",
            passed=False,
            turns=3,
            first_divergence={"turn": 3, "oracle": "expect_ready", "expected": "true", "got": "false"},
            transcript=[],
            metrics={},
        ),
    ]

    judge_annotations = {
        "sc_001": JudgeAnnotation(
            scenario_id="sc_001", verdict="pass", confidence=0.9,
            reasoning="Looks good.", suggest_promote=False, non_gating=True,
        ),
        "sc_002": JudgeAnnotation(
            scenario_id="sc_002", verdict="fail", confidence=0.8,
            reasoning="Did not converge.", suggest_promote=True, non_gating=True,
        ),
    }

    run_dir = tmp_path / "run-t3"
    run_dir.mkdir()
    write_report(
        run_dir=run_dir,
        run_results=run_results,
        tier="t3",
        judge_annotations=judge_annotations,
        env_info={"tier": "t3", "corpus_version": 1},
    )

    report_md = (run_dir / "report.md").read_text()
    # Advisory box must be present
    assert "NON-GATING" in report_md or "advisory" in report_md.lower()
    assert "sc_002" in report_md  # Failed scenario referenced
