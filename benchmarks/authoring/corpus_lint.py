"""Corpus schema linter (also run as a CI test via the T1 module).

Validates a scenario YAML against the DSL schema and the slot/oracle
consistency rules, returning a list of human-readable problem strings (empty ==
clean). See design section 8, stage 5.
"""
from __future__ import annotations

from pathlib import Path

from benchmarks.authoring.scenario import (
    Scenario,
    ScenarioSchemaError,
    load_scenario,
)


def lint_corpus_file(path: str | Path, *, warn_only_ok: bool = False) -> list[str]:
    """Return a list of problems for one scenario file.

    ``warn_only_ok`` controls whether the "missing llm_script" note is treated
    as a returned problem (a corpus WARNING that a scenario is live-only). CI
    corpus files should not have it; the linter test passes ``warn_only_ok`` to
    exercise the warning path.
    """
    path = Path(path)
    problems: list[str] = []
    try:
        scenario = load_scenario(path)
    except ScenarioSchemaError as exc:
        return [str(exc)]

    problems.extend(_lint_llm_script(scenario, warn_only_ok=warn_only_ok))
    problems.extend(_lint_save_consistency(scenario))
    problems.extend(_lint_oracle_consistency(scenario))
    return problems


def _lint_llm_script(scenario: Scenario, *, warn_only_ok: bool) -> list[str]:
    problems: list[str] = []
    # A CI-replayable scenario that drives the compiler LLM (flow B) SHOULD ship
    # an llm_script. Flow A converges deterministically (its live LLM path is
    # dead at HEAD), so a flow-A scenario without a script is fine. Flow B
    # without a script is live-only -> flagged (a corpus WARNING). ``warn_only_ok``
    # documents at the call site that the caller is deliberately exercising this
    # warning path rather than a hard schema error.
    del warn_only_ok  # behavior is identical; the flag is call-site intent only
    if scenario.flow == "linked_policy" and not scenario.llm_script:
        problems.append(
            f"{scenario.id}: flow linked_policy has no llm_script (live-only; "
            f"not CI-replayable)"
        )
    return problems


def _lint_save_consistency(scenario: Scenario) -> list[str]:
    problems: list[str] = []
    if scenario.save == "grouped" and not scenario.grouped_save:
        problems.append(f"{scenario.id}: save=grouped requires a grouped_save block")
    if scenario.save == "from_plan" and scenario.flow != "linked_policy":
        problems.append(f"{scenario.id}: save=from_plan requires flow linked_policy")
    if scenario.save == "envelope" and scenario.flow != "single_rule":
        problems.append(f"{scenario.id}: save=envelope requires flow single_rule")
    return problems


def _lint_oracle_consistency(scenario: Scenario) -> list[str]:
    problems: list[str] = []
    oracle = scenario.oracle
    if oracle.never_persists and scenario.save != "none":
        problems.append(
            f"{scenario.id}: never_persists is incompatible with save={scenario.save!r}"
        )
    if oracle.expect_ready and oracle.never_persists:
        problems.append(
            f"{scenario.id}: expect_ready cannot be true when never_persists is set"
        )
    if (
        oracle.max_turns_to_ready is not None
        and oracle.max_turns_to_ready > scenario.turn_budget
    ):
        problems.append(
            f"{scenario.id}: max_turns_to_ready {oracle.max_turns_to_ready} "
            f"> turn_budget {scenario.turn_budget}"
        )
    # Flow-consistent oracle sub-blocks.
    if scenario.flow == "single_rule" and oracle.params:
        problems.append(f"{scenario.id}: single_rule scenario has an oracle.params block")
    if scenario.flow == "linked_policy" and oracle.draft:
        problems.append(f"{scenario.id}: linked_policy scenario has an oracle.draft block")
    return problems


def lint_corpus_dir(directory: str | Path) -> dict[str, list[str]]:
    """Lint every ``*.yaml`` under ``directory`` (non-recursive into _broken)."""
    directory = Path(directory)
    out: dict[str, list[str]] = {}
    for f in sorted(directory.glob("*.yaml")):
        out[f.name] = lint_corpus_file(f)
    return out
