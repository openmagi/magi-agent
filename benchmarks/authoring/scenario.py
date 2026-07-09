"""Scenario DSL: dataclasses + YAML loader + schema validation.

See design section 5. A scenario is a versioned YAML file describing one
conversational authoring run: the user-side script, the (T1-only) canned
compiler envelopes, the save leg, and the deterministic oracle expectations.

DRIFT NOTE (magi-agent HEAD 60bc91f8a): route A's live LLM path is dead (the
system-instruction template's literal JSON-example braces crash ``str.format``
before the model is called), so flow-A scenarios cannot converge via
``llm_script`` ``draft_updates``. They converge deterministically via ``answers``
plus an optional ``seed_draft`` that provides the structured ``what.payload`` a
working compiler would have built. ``seed_draft`` / ``seed_params`` are the only
additions to the design's DSL and are clearly a HEAD-compat shim; flow B (whose
LLM path works) needs neither.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_FLOWS = {"single_rule", "linked_policy"}
_ARCHETYPES = {
    "happy_path", "linked", "corrective", "out_of_scope", "adversarial",
    "grouped_hybrid",
}
_LANGUAGES = {"en", "ko"}
_SAVE_MODES = {"none", "envelope", "from_plan", "grouped"}


class ScenarioSchemaError(ValueError):
    """A scenario YAML violated the DSL schema."""


@dataclass
class Turn:
    """One user-side turn."""

    say: str | None = None
    answers: dict[str, str] = field(default_factory=dict)
    answers_from_slots: bool = False


@dataclass
class Oracle:
    expect_ready: bool = False
    max_turns_to_ready: int | None = None
    draft: dict[str, Any] = field(default_factory=dict)      # dotted-path subset
    params: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    persisted: dict[str, Any] = field(default_factory=dict)
    forbidden_strings: str | None = None
    never_persists: bool = False
    no_question_loop: bool = False
    draft_absent_keys: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    id: str
    flow: str
    archetype: str
    language: str
    turns: list[Turn]
    oracle: Oracle
    save: str = "none"
    turn_budget: int = 8
    llm_script: list[str] = field(default_factory=list)
    #: HEAD-compat seed for flow A (see module docstring).
    seed_draft: dict[str, Any] = field(default_factory=dict)
    seed_params: dict[str, Any] = field(default_factory=dict)
    #: grouped-save spec (archetype grouped_hybrid).
    grouped_save: dict[str, Any] = field(default_factory=dict)
    #: envelope-save display name.
    save_spec: dict[str, Any] = field(default_factory=dict)
    generated: dict[str, Any] | None = None
    source_path: Path | None = None


def _require(doc: dict[str, Any], key: str, path: Path) -> Any:
    if key not in doc:
        raise ScenarioSchemaError(f"{path.name}: missing required key {key!r}")
    return doc[key]


def load_scenario(path: str | Path) -> Scenario:
    """Load + schema-validate one scenario YAML."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioSchemaError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioSchemaError(f"{path.name}: top level must be a mapping")

    if raw.get("schema_version") != 1:
        raise ScenarioSchemaError(
            f"{path.name}: schema_version must be 1, got {raw.get('schema_version')!r}"
        )
    flow = _require(raw, "flow", path)
    if flow not in _FLOWS:
        raise ScenarioSchemaError(f"{path.name}: flow {flow!r} not in {sorted(_FLOWS)}")
    archetype = _require(raw, "archetype", path)
    if archetype not in _ARCHETYPES:
        raise ScenarioSchemaError(
            f"{path.name}: archetype {archetype!r} not in {sorted(_ARCHETYPES)}"
        )
    language = raw.get("language", "en")
    if language not in _LANGUAGES:
        raise ScenarioSchemaError(f"{path.name}: language {language!r} not in {sorted(_LANGUAGES)}")
    save = raw.get("save", "none")
    if save not in _SAVE_MODES:
        raise ScenarioSchemaError(f"{path.name}: save {save!r} not in {sorted(_SAVE_MODES)}")
    scenario_id = _require(raw, "id", path)
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ScenarioSchemaError(f"{path.name}: id must be a non-empty string")

    turns: list[Turn] = []
    for i, t in enumerate(raw.get("turns") or []):
        if not isinstance(t, dict):
            raise ScenarioSchemaError(f"{path.name}: turns[{i}] must be a mapping")
        answers = t.get("answers") or {}
        if not isinstance(answers, dict):
            raise ScenarioSchemaError(f"{path.name}: turns[{i}].answers must be a mapping")
        turns.append(
            Turn(
                say=t.get("say"),
                answers={str(k): str(v) for k, v in answers.items()},
                answers_from_slots=bool(t.get("answers_from_slots", False)),
            )
        )

    oracle_doc = raw.get("oracle") or {}
    if not isinstance(oracle_doc, dict):
        raise ScenarioSchemaError(f"{path.name}: oracle must be a mapping")
    oracle = Oracle(
        expect_ready=bool(oracle_doc.get("expect_ready", False)),
        max_turns_to_ready=oracle_doc.get("max_turns_to_ready"),
        draft=oracle_doc.get("draft") or {},
        params=oracle_doc.get("params") or {},
        plan=oracle_doc.get("plan") or {},
        persisted=oracle_doc.get("persisted") or {},
        forbidden_strings=oracle_doc.get("forbidden_strings"),
        never_persists=bool(oracle_doc.get("never_persists", False)),
        no_question_loop=bool(oracle_doc.get("no_question_loop", False)),
        draft_absent_keys=list(oracle_doc.get("draft_absent_keys") or []),
    )

    llm_script = raw.get("llm_script") or []
    if not isinstance(llm_script, list) or not all(isinstance(s, str) for s in llm_script):
        raise ScenarioSchemaError(f"{path.name}: llm_script must be a list of strings")

    return Scenario(
        id=scenario_id,
        flow=flow,
        archetype=archetype,
        language=language,
        turns=turns,
        oracle=oracle,
        save=save,
        turn_budget=int(raw.get("turn_budget", 8)),
        llm_script=list(llm_script),
        seed_draft=raw.get("seed_draft") or {},
        seed_params=raw.get("seed_params") or {},
        grouped_save=raw.get("grouped_save") or {},
        save_spec=raw.get("save_spec") or {},
        generated=raw.get("generated"),
        source_path=path,
    )
