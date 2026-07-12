"""U3: T3-only report headline (design 2026-07-12 §2.3 + Fix 2/6).

``write_report`` renders a T3-only headline block ABOVE the M1-M7 table
foregrounding invariant health, M5/M6 containment, structural convergence rate,
an "expected persona variance" bucket, and the empty persona-say fraction (with
a LOUD warning banner when 100% empty). A t1/t2 run does NOT render the block
and its summary.json stays structurally identical to today.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.authoring.report import write_report
from benchmarks.authoring.runner import RunResult

_CORPUS_V1 = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "authoring" / "corpus" / "v1"
)
_HANDWRITTEN = _CORPUS_V1 / "handwritten"
_GENERATED = _CORPUS_V1 / "generated"
# A generated flow-A scenario carrying a `generated.slots` block, which is what
# PersonaUserSim reads to derive structured answers (handwritten scenarios that
# rely only on seed_draft have no slots and so cannot be persona-driven).
_GEN_SCENARIO = "gen_rule_capability_scope_spawn_block_always_en_none"


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


def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )


def _pass(sid: str) -> RunResult:
    return RunResult(
        scenario_id=sid, passed=True, turns=2, reached_ready_at=2,
        transcript=[{"turn": 0, "say": "block Bash", "answers": {}, "response": {}, "http_status": 200}],
    )


def _fail_invariant(sid: str) -> RunResult:
    return RunResult(
        scenario_id=sid, passed=False, turns=1,
        first_divergence={"turn": 0, "invariant": "I5", "expected": "no leak", "got": "leaked"},
        transcript=[{"turn": 0, "say": "x", "answers": {}, "response": {}, "http_status": 200}],
    )


def _fail_oracle_draft(sid: str) -> RunResult:
    return RunResult(
        scenario_id=sid, passed=False, turns=2,
        first_divergence={"turn": 2, "oracle": "draft.action", "expected": "deny", "got": "allow"},
        transcript=[{"turn": 0, "say": "x", "answers": {}, "response": {}, "http_status": 200}],
    )


def _fail_oracle_plan(sid: str) -> RunResult:
    """Flow-B (linked_policy) prose-override deviation: arrives as oracle:plan.*"""
    return RunResult(
        scenario_id=sid, passed=False, turns=2,
        first_divergence={
            "turn": 2,
            "oracle": "plan.gate.what.payload.requireEvidence.onEvidenceUnavailable",
            "expected": "deny", "got": "ask",
        },
        transcript=[{"turn": 0, "say": "x", "answers": {}, "response": {}, "http_status": 200}],
    )


def test_t3_variance_bucket_includes_flow_b_plan_deviations(tmp_path: Path) -> None:
    """Regression (final live T3 run): flow-B prose-override deviations arrive as
    oracle:plan.* in failures_by_code; the variance bucket must include them, not
    mislabel the run 'Expected persona variance: none'."""
    results = [_pass("a::cooperative"), _fail_oracle_plan("b::adversarial")]
    write_report(tmp_path, results, tier="t3")
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "persona variance" in md.lower()
    assert "oracle:plan.gate.what.payload.requireEvidence.onEvidenceUnavailable" in md
    assert "variance**: none" not in md.lower()


def _pass_with_empty_say(sid: str) -> RunResult:
    return RunResult(
        scenario_id=sid, passed=True, turns=2, reached_ready_at=2,
        transcript=[
            {"turn": 0, "say": None, "answers": {}, "response": {}, "http_status": 200,
             "observations": [{"type": "persona_llm_empty_say", "persona": "cooperative"}]},
        ],
    )


def test_t3_report_renders_headline(tmp_path: Path) -> None:
    results = [
        _pass("a::cooperative"),
        _pass("b::corrective"),
        _fail_invariant("c::adversarial"),
        _fail_oracle_draft("d::confused"),
    ]
    write_report(tmp_path, results, tier="t3")
    md = (tmp_path / "report.md").read_text(encoding="utf-8")

    # Headline block present and ABOVE the M1-M7 metric table.
    assert "T3 Headline" in md
    head_idx = md.index("T3 Headline")
    metrics_idx = md.index("## Metrics")
    assert head_idx < metrics_idx

    # (1) invariant-health count (product-health signal): 1 invariant failure.
    assert "Invariant health" in md
    # (2) M5/M6 containment surfaced.
    assert "M5" in md and "M6" in md
    # (3) structural convergence rate = passed/total.
    assert "Structural convergence" in md
    # (4) expected persona-variance bucket names the dotted-path deviation.
    assert "persona variance" in md.lower()
    assert "oracle:draft.action" in md
    # (5) empty persona-say fraction present.
    assert "empty" in md.lower() and "persona" in md.lower()


def test_t3_report_100pct_empty_say_renders_warning(tmp_path: Path) -> None:
    results = [_pass_with_empty_say("a::cooperative"), _pass_with_empty_say("b::confused")]
    write_report(tmp_path, results, tier="t3")
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "WARNING" in md
    assert "persona LLM never fired" in md


def test_t3_report_zero_empty_say_no_warning(tmp_path: Path) -> None:
    results = [_pass("a::cooperative"), _pass("b::confused")]
    write_report(tmp_path, results, tier="t3")
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "persona LLM never fired" not in md


def test_non_t3_report_has_no_headline(tmp_path: Path) -> None:
    results = [_pass("a"), _fail_oracle_draft("b")]
    write_report(tmp_path, results, tier="t1")
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "T3 Headline" not in md
    assert "persona LLM never fired" not in md


def test_non_t3_summary_json_structurally_identical(tmp_path: Path) -> None:
    """Fix 6: a non-t3 summary.json carries no headline keys."""
    results = [_pass("a"), _pass("b")]

    write_report(tmp_path, results, tier="t1")
    t1_keys = set(json.loads((tmp_path / "summary.json").read_text()).keys())

    # Baseline keys = the RunSummary fields + tier/env/generated_at, nothing more.
    expected = {
        "total", "passed", "failed",
        "M1_completion_rate", "M2_turns_to_ready", "M3_dead_end_rate",
        "M4_question_loop_rate", "M5_forbidden_string_hits",
        "M6_containment_violations", "M7_persisted_oracle_failures",
        "failures_by_code", "budget_stopped",
        "tier", "env", "generated_at",
    }
    assert t1_keys == expected, t1_keys


# ---------------------------------------------------------------------------
# End-to-end: the full t3 CLI path renders the headline offline, and personas
# produce slot-answers (no live keys — scripted fake factory).
# ---------------------------------------------------------------------------


def test_t3_cli_path_renders_headline_and_slot_answers_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.run import run_cli

    _isolated(tmp_path, monkeypatch)

    # A scripted persona LLM that always answers with a valid (non-canned) say,
    # so the persona fires (no empty-say) and drives the flow with slot answers.
    class _FakeModel:
        async def generate_content_async(self, req, stream=False):
            class _P:
                text = '{"say": "please block spawning always"}'

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
        corpus_dir=_GENERATED,
        runtime=_runtime(),
        token="test-gateway-token",
        out_dir=tmp_path / "runs",
        only=_GEN_SCENARIO,
        personas="cooperative",
        turn_cap=8,
        skip_preflight=True,
    )

    md = (result.run_dir / "report.md").read_text(encoding="utf-8")
    # Headline renders on the real CLI path.
    assert "## T3 Headline" in md
    assert "Invariant health" in md
    assert "Structural convergence" in md
    # The persona fired (a real say was produced), so no empty-say warning.
    assert "persona LLM never fired" not in md

    # The persona-driven run produced structured slot answers on some turn
    # (proves change #1 is live on the CLI path, not just in unit tests).
    r = result.results[0]
    assert any(
        entry.get("answers") for entry in r.transcript
    ), "persona run produced no structured answers"
    # It converged end-to-end under the relaxed t3 oracle despite the non-canned
    # persona first utterance.
    assert r.passed is True, r.first_divergence
