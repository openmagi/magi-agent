"""CLI entry point for live tiers T2 and T3 (design §10.3).

Usage:
    python -m benchmarks.authoring.run \\
      --tier t2 \\
      --corpus benchmarks/authoring/corpus/v1 \\
      --filter 'flow=single_rule lang=ko' \\
      --max-scenarios 30 --turn-cap 8 --per-turn-timeout 45 \\
      --out runs/

    python -m benchmarks.authoring.run --tier t3 --personas corrective,adversarial \\
      --usersim-model $CHEAP_MODEL --judge

Flags:
  --tier          t1 | t2 | t3
  --corpus        path to corpus directory (e.g. corpus/v1)
  --filter        whitespace-separated key=value pairs (flow, lang, archetype)
  --max-scenarios max scenarios to execute (default 30)
  --turn-cap      max user turns per scenario, overriding the scenario's turn_budget
  --per-turn-timeout  wall-clock timeout per turn in seconds (T2/T3, default 45)
  --budget-usd    soft spend ceiling; stops launching new scenarios past it
  --only          run exactly one scenario by id (repro path)
  --promote       after running, write a regression YAML for each failed scenario
  --out           directory to write run artefacts (default runs/)
  --judge         T3 only: annotate each result with the advisory judge
  --preflight / --no-preflight  run / skip the preflight check (default: run)
  --skip-preflight  alias for --no-preflight (programmatic use)

T1 CI is driven by ``tests/authoring_harness/test_t1_golden_corpus.py``; this
CLI is manual-only (live tiers). T1 is also accepted here for integration testing
of the CLI itself (the test suite uses ``skip_preflight=True``).

Programmatic entry point for tests: :func:`run_cli` (keyword-only, no argparse).
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.authoring.runner import RunResult, run_scenario
from benchmarks.authoring.scenario import Scenario, load_scenario


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


class PreflightError(RuntimeError):
    """The live tier cannot run: a required factory or flag is missing."""


def preflight_check(runtime: Any, *, token: str) -> None:
    """Verify that the production model factory resolves to a non-None callable.

    This is mandatory honesty (design §10.3): ``step_compile`` degrades
    gracefully when the factory yields None, and without this probe a T2 run
    would silently measure the deterministic fallback while claiming live.

    Raises :class:`PreflightError` with a human-readable diagnosis if the
    factory is None or absent.
    """
    import magi_agent.cli.wiring as wiring

    factory = getattr(wiring, "_build_criterion_model_factory", None)
    if factory is None:
        raise PreflightError(
            "preflight: route-A factory (_build_criterion_model_factory) is None. "
            "Set MAGI_EGRESS_GATE_ENABLED=1, MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED=1, "
            "and a provider key before running T2/T3."
        )
    # Route A production factory is a function (not the model itself); calling it
    # returns the model (or None when env keys are absent).
    try:
        model = factory()
    except Exception as exc:  # noqa: BLE001
        raise PreflightError(f"preflight: route-A factory raised: {exc}") from exc
    if model is None:
        raise PreflightError(
            "preflight: route-A factory() returned None. "
            "Check MAGI_EGRESS_GATE_ENABLED, MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED, "
            "and the provider key envs. Run with --skip-preflight to suppress."
        )


# ---------------------------------------------------------------------------
# Corpus loader + filter
# ---------------------------------------------------------------------------


def _load_corpus(corpus_dir: Path, *, only: str | None = None) -> list[Scenario]:
    """Recursively load all YAML scenarios from corpus_dir."""
    paths = sorted(corpus_dir.rglob("*.yaml"))
    scenarios: list[Scenario] = []
    for p in paths:
        # Skip the _broken/ directory used for negative fixtures in selftest.
        if "_broken" in str(p):
            continue
        try:
            sc = load_scenario(p)
            scenarios.append(sc)
        except Exception:  # noqa: BLE001
            pass  # linter will catch schema errors; don't abort the run
    if only:
        scenarios = [s for s in scenarios if s.id == only]
    return scenarios


def _apply_filter(scenarios: list[Scenario], filter_str: str | None) -> list[Scenario]:
    """Apply ``key=value`` filter pairs (whitespace-separated)."""
    if not filter_str:
        return scenarios
    pairs: dict[str, str] = {}
    for token in filter_str.split():
        if "=" in token:
            k, _, v = token.partition("=")
            pairs[k.strip()] = v.strip()
    if not pairs:
        return scenarios
    out: list[Scenario] = []
    for sc in scenarios:
        match = True
        for k, v in pairs.items():
            if k == "flow" and sc.flow != v:
                match = False
            elif k in ("lang", "language") and sc.language != v:
                match = False
            elif k == "archetype" and sc.archetype != v:
                match = False
        if match:
            out.append(sc)
    return out


# ---------------------------------------------------------------------------
# Promote helper
# ---------------------------------------------------------------------------


def promote_scenario(result: RunResult, *, out_dir: Path) -> Path:
    """Write a regression YAML skeleton for a failed scenario.

    The emitted file has the observed transcript as ``llm_script`` entries
    (assistant messages only, one per turn) and a prefilled ``oracle`` block
    marked TODO so the operator edits the correct expectation before committing
    (design §11.3 item 6).
    """
    import yaml  # type: ignore

    out_dir.mkdir(parents=True, exist_ok=True)
    sid = result.scenario_id
    path = out_dir / f"{sid}.yaml"

    # Build llm_script from transcript
    llm_script: list[str] = []
    turns: list[dict[str, Any]] = []
    for entry in result.transcript or []:
        if not isinstance(entry, dict):
            continue
        resp = entry.get("response") if isinstance(entry.get("response"), dict) else {}
        msg = resp.get("assistant_message", "") if isinstance(resp, dict) else ""
        # A minimal valid envelope
        llm_script.append(json.dumps({
            "assistant_message": msg,
            "draft_updates": {},
            "questions": [],
        }))
        say = entry.get("say")
        answers = entry.get("answers") or {}
        turn_entry: dict[str, Any] = {}
        if say:
            turn_entry["say"] = say
        if answers:
            turn_entry["answers"] = answers
        if turn_entry:
            turns.append(turn_entry)

    fd = result.first_divergence or {}
    doc: dict[str, Any] = {
        "schema_version": 1,
        "id": sid,
        "flow": "single_rule",           # operator MUST correct if flow B
        "archetype": "corrective",        # operator MUST correct
        "language": "en",
        "turn_budget": max(result.turns + 2, 4),
        "turns": turns or [{"say": "TODO"}],
        "llm_script": llm_script or ["{}"],
        "save": "none",                   # operator sets if needed
        "oracle": {
            "expect_ready": False,         # TODO: set correct expectation
            "_promoted_from_failure": fd,
        },
    }

    path.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Per-scenario transcript writer
# ---------------------------------------------------------------------------


def _write_transcript(scenario_dir: Path, result: RunResult) -> None:
    """Append-write transcript.jsonl, one JSON line per HTTP exchange."""
    scenario_dir.mkdir(parents=True, exist_ok=True)
    t_path = scenario_dir / "transcript.jsonl"
    with t_path.open("w", encoding="utf-8") as fh:
        for entry in result.transcript or []:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()  # flush per line (design §11.2)


def _write_result(scenario_dir: Path, result: RunResult) -> None:
    scenario_dir.mkdir(parents=True, exist_ok=True)
    obj: dict[str, Any] = {
        "scenario_id": result.scenario_id,
        "passed": result.passed,
        "turns": result.turns,
        "reached_ready_at": result.reached_ready_at,
        "first_divergence": result.first_divergence,
        "metrics": result.metrics,
    }
    (scenario_dir / "result.json").write_text(
        json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# RunCliResult — programmatic return value for tests
# ---------------------------------------------------------------------------


@dataclass
class RunCliResult:
    scenarios_run: int
    passed: int
    failed: int
    run_dir: Path
    budget_stopped: bool = False
    results: list[RunResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core runner loop
# ---------------------------------------------------------------------------


def run_cli(
    *,
    tier: str = "t1",
    corpus_dir: Path,
    runtime: Any,
    token: str,
    out_dir: Path | None = None,
    max_scenarios: int | None = None,
    turn_cap: int | None = None,
    per_turn_timeout: float | None = None,
    budget_usd: float | None = None,
    only: str | None = None,
    filter_str: str | None = None,
    promote: bool = False,
    judge: bool = False,
    personas: str | None = None,
    usersim_model: str | None = None,
    skip_preflight: bool = False,
) -> RunCliResult:
    """Programmatic entry point: run scenarios and write artefacts.

    Parameters are the CLI flag equivalents. Tests set ``skip_preflight=True``
    and ``tier="t1"`` so no live LLM is required.
    """
    from benchmarks.authoring.report import write_report

    if not skip_preflight and tier in ("t2", "t3"):
        preflight_check(runtime, token=token)

    # Corpus
    scenarios = _load_corpus(corpus_dir, only=only)
    if not scenarios:
        raise RuntimeError(f"no scenarios found in {corpus_dir}")
    scenarios = _apply_filter(scenarios, filter_str)
    if only and not scenarios:
        raise RuntimeError(f"scenario {only!r} not found in corpus")

    if max_scenarios is not None:
        scenarios = scenarios[:max_scenarios]

    # Run directory
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (out_dir or Path("runs")) / f"{ts}-{tier}"
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[RunResult] = []
    budget_stopped = False
    estimated_spend = 0.0

    # T3 fans each scenario out into one work-unit per persona, each driven by a
    # live PersonaUserSim. T1/T2 have exactly one unit per scenario, canned-replay
    # driven (user_sim=None). The unit id is the scenario id, suffixed with the
    # persona for T3 so per-persona results / transcripts don't collide.
    from dataclasses import replace as _replace

    from benchmarks.authoring.usersim import PersonaUserSim

    persona_names: tuple[str, ...] = ()
    persona_factory = None
    if tier == "t3":
        if personas:
            persona_names = tuple(
                p.strip() for p in personas.split(",") if p.strip()
            )
        else:
            persona_names = _DEFAULT_PERSONAS
        persona_factory = _resolve_persona_factory(usersim_model)

    def _work_units(sc: Scenario):
        """Yield (unit_id, scenario, user_sim) tuples for one scenario."""
        if tier == "t3":
            for persona in persona_names:
                unit_id = f"{sc.id}::{persona}"
                yield (
                    unit_id,
                    _replace(sc, id=unit_id),
                    PersonaUserSim(persona=persona, scripted_llm=persona_factory),
                )
        else:
            yield (sc.id, sc, None)

    for sc in scenarios:
        if budget_stopped:
            break

        # Apply turn_cap by clipping the scenario's budget
        if turn_cap is not None:
            sc = _cap_turns(sc, turn_cap)

        for unit_id, unit_sc, user_sim in _work_units(sc):
            if budget_usd is not None and estimated_spend >= budget_usd:
                budget_stopped = True
                break

            # Per-scenario isolation. MUST mirror the T1 pytest fixture exactly
            # (test_t1_golden_corpus.py), or the CLI measures a DIFFERENT, dirtier
            # world than CI and reports spurious failures:
            #   1. tmp MAGI_CUSTOMIZE store,
            #   2. NL interactive flag on (route A's authoring path),
            #   3. the dashboard-authored sidecar redirected to tmp. Without this,
            #      from-plan producers read/write the REAL host ~/.magi sidecar and
            #      leak state ACROSS scenarios (a prior run's domainAllowlist bleeds
            #      into the next), which is exactly the host-global-sidecar hazard
            #      the harness exists to keep out of its own measurements.
            import tempfile

            import magi_agent.packs.discovery as _discovery

            with tempfile.TemporaryDirectory() as tmpd:
                tmp_path = Path(tmpd)
                old_customize = os.environ.get("MAGI_CUSTOMIZE")
                old_nl = os.environ.get("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED")
                old_search = _discovery.default_search_bases
                try:
                    # Set the isolation state INSIDE the try so the finally
                    # restore always matches, even if an assignment raised.
                    os.environ["MAGI_CUSTOMIZE"] = str(tmp_path / "customize.json")
                    os.environ["MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED"] = "1"
                    _discovery.default_search_bases = lambda: [tmp_path]
                    result = run_scenario(
                        unit_sc, runtime, token=token, tier=tier, user_sim=user_sim
                    )
                except Exception as exc:  # noqa: BLE001
                    result = RunResult(
                        scenario_id=unit_id,
                        passed=False,
                        turns=0,
                        first_divergence={"oracle": "runner_exception", "expected": "no exception", "got": str(exc)},
                    )
                finally:
                    _discovery.default_search_bases = old_search
                    if old_customize is not None:
                        os.environ["MAGI_CUSTOMIZE"] = old_customize
                    elif "MAGI_CUSTOMIZE" in os.environ:
                        del os.environ["MAGI_CUSTOMIZE"]
                    if old_nl is not None:
                        os.environ["MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED"] = old_nl
                    elif "MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED" in os.environ:
                        del os.environ["MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED"]

            results.append(result)

            # Write per-unit artefacts immediately (so partial runs are triageable).
            # The unit id (scenario id, ``::persona``-suffixed for T3) keeps the
            # per-persona transcript / result dirs from colliding.
            sc_dir = run_dir / "scenario" / unit_id
            _write_transcript(sc_dir, result)
            _write_result(sc_dir, result)

            # Budget estimation (chars/4 tokens × rough cost)
            token_est = sum(
                len(json.dumps(e.get("response") or {}, ensure_ascii=False)) // 4
                for e in result.transcript
            )
            estimated_spend += token_est * 0.000001  # rough $1/1M token estimate

    # Judge annotations (T3 + --judge)
    judge_annotations: dict[str, Any] = {}
    if judge and tier == "t3":
        from benchmarks.authoring.judge import annotate_with_judge

        # Resolve a real model factory for the advisory judge. Reuse the SAME
        # production factory the live tiers + preflight already validated
        # (_build_criterion_model_factory), so an operator who can run T2/T3 at
        # all gets a working judge with no extra config. --usersim-model, when
        # given, overrides the model id for the cheap judge pass.
        judge_factory = None
        try:
            from magi_agent.cli import wiring

            base_factory = getattr(wiring, "_build_criterion_model_factory", None)
            if base_factory is not None:
                if usersim_model:
                    import os as _os

                    def judge_factory() -> Any:  # type: ignore[misc]
                        prev = _os.environ.get("MAGI_EGRESS_CRITIC_MODEL")
                        _os.environ["MAGI_EGRESS_CRITIC_MODEL"] = usersim_model
                        try:
                            return base_factory()()
                        finally:
                            if prev is None:
                                _os.environ.pop("MAGI_EGRESS_CRITIC_MODEL", None)
                            else:
                                _os.environ["MAGI_EGRESS_CRITIC_MODEL"] = prev
                else:
                    judge_factory = base_factory()
        except Exception:  # noqa: BLE001 - judge is advisory; never break the run
            judge_factory = None

        # annotate_with_judge is internally fail-soft, but guard the loop too so
        # a judge annotation can NEVER abort the run (which would skip
        # write_report). Per-unit results are already flushed to disk above.
        for r in results:
            try:
                ann = annotate_with_judge(
                    r, judge_factory=judge_factory or (lambda: None)
                )
            except Exception:  # noqa: BLE001 - judge is advisory; never break the run
                continue
            judge_annotations[r.scenario_id] = ann

    # Promote failures
    if promote:
        promote_dir = run_dir / "promoted_regressions"
        for r in results:
            if not r.passed:
                promote_scenario(r, out_dir=promote_dir)

    # Write summary.json + report.md
    env_info: dict[str, Any] = {
        "tier": tier,
        "corpus_dir": str(corpus_dir),
        "scenarios_run": len(results),
        "max_scenarios": max_scenarios,
        "turn_cap": turn_cap,
    }
    write_report(
        run_dir=run_dir,
        run_results=results,
        tier=tier,
        judge_annotations=judge_annotations if judge_annotations else None,
        env_info=env_info,
        budget_stopped=budget_stopped,
    )

    passed = sum(1 for r in results if r.passed)
    return RunCliResult(
        scenarios_run=len(results),
        passed=passed,
        failed=len(results) - passed,
        run_dir=run_dir,
        budget_stopped=budget_stopped,
        results=results,
    )


def _cap_turns(scenario: Scenario, cap: int) -> Scenario:
    """Return a copy of the scenario with turn_budget capped at ``cap``."""
    from dataclasses import replace

    return replace(scenario, turn_budget=min(scenario.turn_budget, cap))


#: Default persona set for T3 when --personas is not given.
_DEFAULT_PERSONAS = ("cooperative", "corrective", "confused", "adversarial")


def _resolve_persona_factory(usersim_model: str | None):
    """Resolve the persona live-model factory from the SAME production wiring the
    judge uses (``_build_criterion_model_factory``).

    Mirrors the judge factory resolution in :func:`run_cli`: ``--usersim-model``,
    when given, overrides the model id via ``MAGI_EGRESS_CRITIC_MODEL`` for the
    persona pass. Returns a ``() -> model`` factory, or ``None`` when the
    production factory is absent (persona then fails soft to empty utterances).
    """
    try:
        from magi_agent.cli import wiring

        base_factory = getattr(wiring, "_build_criterion_model_factory", None)
        if base_factory is None:
            return None
        if usersim_model:
            import os as _os

            def _persona_factory() -> Any:
                prev = _os.environ.get("MAGI_EGRESS_CRITIC_MODEL")
                _os.environ["MAGI_EGRESS_CRITIC_MODEL"] = usersim_model
                try:
                    return base_factory()()
                finally:
                    if prev is None:
                        _os.environ.pop("MAGI_EGRESS_CRITIC_MODEL", None)
                    else:
                        _os.environ["MAGI_EGRESS_CRITIC_MODEL"] = prev

            return _persona_factory
        return base_factory()
    except Exception:  # noqa: BLE001 - fail soft; persona is not the critical path
        return None


# ---------------------------------------------------------------------------
# argparse CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    parser = argparse.ArgumentParser(
        description="Authoring QA harness CLI — manual live tiers T2/T3."
    )
    parser.add_argument("--tier", choices=("t1", "t2", "t3"), default="t2")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).resolve().parent / "corpus" / "v1",
        help="corpus directory (default: benchmarks/authoring/corpus/v1)",
    )
    parser.add_argument("--filter", dest="filter_str", default=None,
                        help="key=value pairs: flow=single_rule lang=ko archetype=corrective")
    parser.add_argument("--max-scenarios", type=int, default=30)
    parser.add_argument("--turn-cap", type=int, default=8)
    parser.add_argument("--per-turn-timeout", type=float, default=45.0)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--only", default=None, help="run a single scenario by id")
    parser.add_argument("--promote", action="store_true",
                        help="write regression YAML for failed scenarios")
    parser.add_argument("--out", type=Path, default=Path("runs"))
    parser.add_argument("--judge", action="store_true",
                        help="T3 only: annotate with advisory judge")
    parser.add_argument("--personas", default=None,
                        help="T3 only: comma-sep persona names (default: all four)")
    parser.add_argument("--usersim-model", default=None,
                        help="T3 only: cheap model id for persona sim")
    parser.add_argument(
        "--skip-preflight", action="store_true",
        help="skip preflight check (CI only)"
    )
    # Gateway token from env
    parser.add_argument("--token", default=None,
                        help="gateway token (default: MAGI_GATEWAY_TOKEN env)")

    args = parser.parse_args(argv)
    token = args.token or os.environ.get("MAGI_GATEWAY_TOKEN", "test-gateway-token")

    # Build a minimal runtime for in-process testing
    runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id=os.environ.get("MAGI_BOT_ID", "local-bot"),
            user_id=os.environ.get("MAGI_USER_ID", "local-user"),
            gateway_token=token,
            api_proxy_url=os.environ.get("MAGI_API_PROXY_URL", "http://api-proxy.local"),
            chat_proxy_url=os.environ.get("MAGI_CHAT_PROXY_URL", "http://chat-proxy.local"),
            redis_url=os.environ.get("REDIS_URL", "redis://redis.local:6379/0"),
            model=os.environ.get("MAGI_MODEL", "gpt-5.2"),
            build=BuildInfo(version="0.1.0", build_sha="head"),
        )
    )

    result = run_cli(
        tier=args.tier,
        corpus_dir=args.corpus,
        runtime=runtime,
        token=token,
        out_dir=args.out,
        max_scenarios=args.max_scenarios,
        turn_cap=args.turn_cap,
        per_turn_timeout=args.per_turn_timeout,
        budget_usd=args.budget_usd,
        only=args.only,
        filter_str=args.filter_str,
        promote=args.promote,
        judge=args.judge,
        personas=args.personas,
        usersim_model=args.usersim_model,
        skip_preflight=args.skip_preflight,
    )

    print(
        f"[authoring-qa] tier={args.tier} "
        f"run={result.scenarios_run} "
        f"passed={result.passed} "
        f"failed={result.failed}"
    )
    if result.budget_stopped:
        print("[authoring-qa] WARNING: budget stopped")
    print(f"[authoring-qa] report: {result.run_dir / 'report.md'}")
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
