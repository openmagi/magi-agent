"""Oracle-first scenario generator (offline, design section 8).

Labels are derived from slots MECHANICALLY, before any text or LLM touches the
scenario. This module is the pure core: a total function ``derive_scenario`` from
a ``Slots`` combo to a fully-formed :class:`~benchmarks.authoring.scenario.Scenario`
(user turns, HEAD-compat seed, the T1 ``llm_script`` for flow B, save leg, and
the deterministic oracle). CI never runs the generator; it replays the generator's
OUTPUT (YAML under ``corpus/v1/generated/``).

Design mapping:
- Stage 1 slot matrix: :func:`flow_a_matrix` / :func:`flow_b_matrix` enumerate
  slot combos, drawing the legal ``(kind, firesAt, action)`` triples from the
  imported ``_LEGAL`` matrix so the generator can never drift from the runtime.
- Stage 2 oracle derivation: :func:`derive_scenario` writes ``expect_ready``, the
  final draft/params subset, ``max_turns_to_ready`` (base turns + quirk
  surcharge), and the persisted-assertion toggles. ``out_of_scope`` slots map to
  ``expect_ready: false`` + ``never_persists``.
- Stage 3 utterance realization: deterministic per-language templates.
- Stage 5 dedup + versioning: :func:`select_pairwise` picks a pairwise-covering
  subset; :func:`dedup` drops turn-script duplicates; ``CORPUS_VERSION`` stamps
  provenance.

HEAD-DRIFT (honest, mirrors the handwritten corpus): route A's live LLM path is
dead at this ref, so flow-A scenarios converge via ``answers`` (kind/firesAt/
action/scope) plus a ``seed_draft`` that supplies the ``what.payload`` a working
compiler would have built. Flow B's LLM path works, so flow-B scenarios ship a
canned ``llm_script`` and need no seed.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmarks.authoring.scenario import Oracle, Scenario, Turn
from magi_agent.customize.custom_rules import _LEGAL

#: Corpus/generator version. Additive growth stays here; a breaking DSL change
#: bumps the corpus to ``v2/`` (design section 8, stage 5).
CORPUS_VERSION = 1
GENERATOR_VERSION = 1

_LANGUAGES = ("en", "ko")

# Flow-A quirks the generator can realize deterministically at this HEAD. The
# LLM-dependent quirks (confused / multi_intent / injection) are LIVE-tier
# concerns (T3 persona sim, Wave C); the CI-replayable generator ships the
# deterministic ones only.
_FLOW_A_QUIRKS = ("none", "corrective", "out_of_scope")
_FLOW_B_QUIRKS = ("none",)


# ---------------------------------------------------------------------------
# Kinds with a known validator-clean payload seed (verified against
# validate_custom_rule at HEAD). A generated flow-A scenario only uses kinds we
# can seed to a CLEAN rule, so every generated scenario is CI-replayable.
# ---------------------------------------------------------------------------

_KIND_PAYLOAD_SEED: dict[str, dict[str, Any]] = {
    "tool_perm": {"match": {"tool": "Bash"}, "decision": "deny"},
    "llm_criterion": {"criterion": "Does the reply contain financial advice?"},
    "output_rewrite": {"pattern": "AKIA[0-9A-Z]{16}", "replacement": "***", "mode": "redact"},
    "shell_command": {"source": "inline", "inline": "echo audit"},
    "shell_check": {"source": "inline", "inline": "exit 0"},
    "capability_scope": {"tightenOnly": True, "denyTools": ["Bash"]},
    "prompt_injection": {
        "mode": "append", "value": "stay within policy", "target": "system_prompt",
    },
}

#: Per-kind plain-English utterance templates, {tool}-free (the seed carries the
#: structured payload; the utterance is the operator's natural phrasing).
_KIND_UTTERANCE: dict[str, dict[str, str]] = {
    "tool_perm": {
        "en": "restrict the Bash tool",
        "ko": "Bash 툴 사용을 제한해줘",
    },
    "llm_criterion": {
        "en": "have an AI judge check the reply for financial advice",
        "ko": "답변에 금융 조언이 있는지 AI 심판이 확인하게 해줘",
    },
    "output_rewrite": {
        "en": "redact AWS keys from tool output",
        "ko": "툴 출력에서 AWS 키를 가려줘",
    },
    "shell_command": {
        "en": "run an audit shell hook",
        "ko": "감사용 셸 후크를 실행해줘",
    },
    "shell_check": {
        "en": "run a shell verifier before finishing",
        "ko": "마무리 전에 셸 검증기를 실행해줘",
    },
    "capability_scope": {
        "en": "stop spawned subagents from using Bash",
        "ko": "하위 에이전트가 Bash를 못 쓰게 해줘",
    },
    "prompt_injection": {
        "en": "append a safety note to the prompt",
        "ko": "프롬프트에 안전 안내를 덧붙여줘",
    },
}

_OUT_OF_SCOPE_UTTERANCE: dict[str, tuple[str, str]] = {
    "en": (
        "email me a weekly summary of everything the bot did",
        "so a rule can't schedule outbound email?",
    ),
    "ko": (
        "봇이 한 일을 매주 이메일로 보내줘",
        "규칙으로 이메일 예약은 못 하는 거야?",
    ),
}


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Slots:
    """One point in the slot matrix (design section 8, stage 1)."""

    flow: str  # single_rule | linked_policy
    language: str  # en | ko
    quirk: str  # none | corrective | out_of_scope (flow A) / none (flow B)
    # Flow-A slots:
    kind: str | None = None
    fires_at: str | None = None
    action: str | None = None
    scope: str | None = None
    # Flow-B slots:
    gated_tool: str | None = None
    domain: str | None = None
    on_unavailable: str | None = None  # "default" | "ask"


def _seed_is_clean(kind: str, fires_at: str, action: str) -> bool:
    """Does the per-kind payload seed produce a validator-clean rule HERE?

    Payload validity is firesAt-dependent (e.g. llm_criterion at after_tool_use
    additionally requires a toolMatch the static seed lacks). We therefore
    validate each candidate at generation time with the production validator, so
    a generated triple is CI-replayable by construction and can never drift from
    the runtime's per-kind schema.
    """
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = {
        "id": "cr_probe",
        "scope": "always",
        "enabled": True,
        "firesAt": fires_at,
        "action": action,
        "what": {"kind": kind, "payload": dict(_KIND_PAYLOAD_SEED[kind])},
    }
    return not validate_custom_rule(rule)


def _legal_triples() -> list[tuple[str, str, str]]:
    """Every ``(kind, firesAt, action)`` whose STATIC payload seed validates clean.

    Drawn from the imported ``_LEGAL`` matrix (so the slot dimensions cannot drift
    from the runtime) and filtered by :func:`_seed_is_clean` (so the per-kind
    payload schema cannot drift either). Every emitted triple therefore yields a
    validator-clean, deterministically-convergent, CI-replayable scenario.
    """
    out: list[tuple[str, str, str]] = []
    for kind in sorted(_KIND_PAYLOAD_SEED):
        for fires_at, actions in sorted(_LEGAL.get(kind, {}).items()):
            for action in sorted(actions):
                if _seed_is_clean(kind, fires_at, action):
                    out.append((kind, fires_at, action))
    return out


def flow_a_matrix() -> list[Slots]:
    """Enumerate flow-A slot combos (kind×firesAt×action×scope×lang×quirk)."""
    from magi_agent.customize.custom_rules import SCOPES

    out: list[Slots] = []
    triples = _legal_triples()
    # (kind, firesAt) -> the seed-clean actions available there (for the
    # corrective quirk, which retargets between two clean actions).
    clean_actions: dict[tuple[str, str], list[str]] = {}
    for k, fa, act in triples:
        clean_actions.setdefault((k, fa), []).append(act)
    for (kind, fires_at, action), scope, lang, quirk in itertools.product(
        triples, sorted(SCOPES), _LANGUAGES, _FLOW_A_QUIRKS
    ):
        # A corrective quirk needs a DIFFERENT seed-clean action to retarget TO,
        # so it only applies where the (kind, firesAt) slot has >= 2 clean actions.
        if quirk == "corrective" and len(clean_actions[(kind, fires_at)]) < 2:
            continue
        out.append(
            Slots(
                flow="single_rule", language=lang, quirk=quirk,
                kind=kind, fires_at=fires_at, action=action, scope=scope,
            )
        )
    return out


_FLOW_B_TOOLS = ("execute_trade", "publish_report", "send_payment")
_FLOW_B_DOMAINS = ("sec.gov", "europa.eu", "fda.gov")
_FLOW_B_ONUNAVAIL = ("default", "ask")


def flow_b_matrix() -> list[Slots]:
    """Enumerate flow-B slot combos (gatedTool×domain×onUnavailable×lang)."""
    out: list[Slots] = []
    for tool, domain, onu, lang, quirk in itertools.product(
        _FLOW_B_TOOLS, _FLOW_B_DOMAINS, _FLOW_B_ONUNAVAIL, _LANGUAGES, _FLOW_B_QUIRKS
    ):
        out.append(
            Slots(
                flow="linked_policy", language=lang, quirk=quirk,
                gated_tool=tool, domain=domain, on_unavailable=onu,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Stage 2/3: pure slot -> Scenario derivation
# ---------------------------------------------------------------------------


def _answers_for(kind: str, fires_at: str, action: str, scope: str) -> dict[str, str]:
    """The canonical answers the deterministic user-sim would give.

    Only fields the singleton auto-fill will NOT collapse are answered; a
    singleton slot/action is auto-filled by the engine, so answering it is
    redundant (and would still be legal). We answer kind + scope always, and
    firesAt/action only when their legal set is not a singleton.
    """
    answers = {"q_what.kind": kind, "q_scope": scope}
    legal_slots = _LEGAL[kind]
    if len(legal_slots) > 1:
        answers["q_firesAt"] = fires_at
    legal_actions = legal_slots[fires_at]
    if len(legal_actions) > 1:
        answers["q_action"] = action
    return answers


def _scenario_id(slots: Slots) -> str:
    if slots.flow == "single_rule":
        base = f"gen_rule_{slots.kind}_{slots.fires_at}_{slots.action}_{slots.scope}"
        base += f"_{slots.language}_{slots.quirk}"
    else:
        base = f"gen_policy_{slots.gated_tool}_{slots.domain.replace('.', '_')}"
        base += f"_{slots.on_unavailable}_{slots.language}"
    return base


def _derive_flow_a(slots: Slots) -> Scenario:
    kind = slots.kind
    fires_at = slots.fires_at
    action = slots.action
    scope = slots.scope
    lang = slots.language
    utter = _KIND_UTTERANCE[kind][lang]

    if slots.quirk == "out_of_scope":
        # Non-convergent by construction: the operator's request is off-surface.
        say1, say2 = _OUT_OF_SCOPE_UTTERANCE[lang]
        return Scenario(
            id=_scenario_id(slots),
            flow="single_rule", archetype="out_of_scope", language=lang,
            turns=[Turn(say=say1), Turn(say=say2)],
            oracle=Oracle(
                expect_ready=False, never_persists=True, no_question_loop=True,
                forbidden_strings="default",
            ),
            save="none", turn_budget=4,
            generated=_provenance(slots),
        )

    seed_draft = {"what": {"payload": dict(_KIND_PAYLOAD_SEED[kind])}}
    answers = _answers_for(kind, fires_at, action, scope)

    if slots.quirk == "corrective":
        # Turn 1 answers a DIFFERENT seed-clean action; turn 2 retargets to the
        # target. Only seed-clean alternatives are used so both turns' drafts are
        # validator-legal.
        other_actions = sorted(
            a for a in _LEGAL[kind][fires_at]
            if a != action and _seed_is_clean(kind, fires_at, a)
        )
        first_action = other_actions[0]
        turn1_answers = dict(answers)
        turn1_answers["q_action"] = first_action
        turns = [
            Turn(say=utter, answers=turn1_answers),
            Turn(say=_corrective_say(lang), answers={"q_action": action}),
        ]
        max_turns = 2
    else:  # none / happy
        turns = [
            Turn(say=utter, answers=answers),
        ]
        # A second turn is only needed when the first turn cannot supply every
        # non-singleton answer; here we pack them into turn 1, so ready lands on
        # turn 1 whenever the payload seed + auto-fill complete the draft.
        max_turns = 1

    draft_oracle: dict[str, Any] = {
        "what.kind": kind,
        "firesAt": fires_at,
        "action": action,
        "scope": scope,
    }
    return Scenario(
        id=_scenario_id(slots),
        flow="single_rule",
        archetype="corrective" if slots.quirk == "corrective" else "happy_path",
        language=lang,
        turns=turns,
        oracle=Oracle(
            expect_ready=True,
            max_turns_to_ready=max_turns,
            draft=draft_oracle,
            persisted={
                "rule_valid_and_clean": True,
                "policy_intent_is_first_utterance": True,
                "no_orphan_rules": True,
            },
            forbidden_strings="default",
        ),
        save="envelope", turn_budget=4,
        seed_draft=seed_draft,
        generated=_provenance(slots),
    )


def _corrective_say(lang: str) -> str:
    return {
        "en": "actually change what it does",
        "ko": "아니 동작을 바꿔줘",
    }[lang]


def _derive_flow_b(slots: Slots) -> Scenario:
    tool = slots.gated_tool
    domain = slots.domain
    lang = slots.language
    onu = slots.on_unavailable
    say1 = _flow_b_say(tool, domain, lang)
    say2 = _flow_b_label_say(lang)

    import json

    upd1 = {"gatedTool": tool, "allowlistDomains": [domain]}
    if onu == "ask":
        upd1["onUnavailable"] = "ask"
    llm_script = [
        json.dumps({"assistant_message": "One more detail.", "param_updates": upd1,
                    "questions": ["What are you verifying?"]}),
        json.dumps({"assistant_message": "Done.",
                    "param_updates": {"evidenceLabel": "source credibility"}}),
    ]
    on_unavailable_expected = "ask" if onu == "ask" else "deny"
    plan_oracle = {
        "gate.what.payload.match.tool": tool,
        "gate.what.payload.requireEvidence.onEvidenceUnavailable": on_unavailable_expected,
        "producer.trigger.domainAllowlist": [domain],
    }
    return Scenario(
        id=_scenario_id(slots),
        flow="linked_policy", archetype="linked", language=lang,
        turns=[Turn(say=say1), Turn(say=say2)],
        oracle=Oracle(
            expect_ready=True, max_turns_to_ready=2, plan=plan_oracle,
            persisted={"from_plan_triple": True,
                       "policy_intent_is_first_utterance": True,
                       "no_orphan_rules": True},
            forbidden_strings="default",
        ),
        save="from_plan", turn_budget=4,
        llm_script=llm_script,
        generated=_provenance(slots),
    )


def _flow_b_say(tool: str, domain: str, lang: str) -> str:
    if lang == "ko":
        return f"{domain} 출처가 검증되기 전에는 {tool} 을(를) 막아줘"
    return f"block {tool} until a source from {domain} is verified this session"


def _flow_b_label_say(lang: str) -> str:
    return "출처 신뢰도 라벨" if lang == "ko" else "the source credibility label"


def _provenance(slots: Slots) -> dict[str, Any]:
    slot_block: dict[str, Any] = {"quirk": slots.quirk}
    if slots.flow == "single_rule":
        slot_block.update(
            {"kind": slots.kind, "firesAt": slots.fires_at,
             "action": slots.action, "scope": slots.scope}
        )
    else:
        slot_block.update(
            {"gatedTool": slots.gated_tool, "domain": slots.domain,
             "onUnavailable": slots.on_unavailable}
        )
    return {
        "slots": slot_block,
        "generator_version": GENERATOR_VERSION,
        "paraphrase_model": None,
    }


def derive_scenario(slots: Slots) -> Scenario:
    """Pure, total slot -> Scenario. Every matrix point yields a valid Scenario."""
    if slots.flow == "single_rule":
        return _derive_flow_a(slots)
    return _derive_flow_b(slots)


# ---------------------------------------------------------------------------
# Stage 5: pairwise selection + dedup
# ---------------------------------------------------------------------------


def _pairs_of(slots: Slots) -> set[tuple[str, Any, str, Any]]:
    """Every unordered slot-VALUE PAIR in this combo (for pairwise coverage)."""
    if slots.flow == "single_rule":
        dims = {
            "kind": slots.kind, "firesAt": slots.fires_at, "action": slots.action,
            "scope": slots.scope, "lang": slots.language, "quirk": slots.quirk,
        }
    else:
        dims = {
            "gatedTool": slots.gated_tool, "domain": slots.domain,
            "onUnavailable": slots.on_unavailable, "lang": slots.language,
            "quirk": slots.quirk,
        }
    items = sorted(dims.items())
    pairs: set[tuple[str, Any, str, Any]] = set()
    for (na, va), (nb, vb) in itertools.combinations(items, 2):
        pairs.add((na, va, nb, vb))
    return pairs


def select_pairwise(matrix: list[Slots]) -> list[Slots]:
    """Greedy pairwise-covering subset: every slot PAIR appears >= once.

    Deterministic (matrix order + greedy max-new-pairs) so the generated corpus
    is reproducible. This is the standard combinatorial-coverage tradeoff over
    the full product (design section 8, stage 1).
    """
    all_pairs: set[tuple[str, Any, str, Any]] = set()
    for s in matrix:
        all_pairs |= _pairs_of(s)

    covered: set[tuple[str, Any, str, Any]] = set()
    chosen: list[Slots] = []
    remaining = list(matrix)
    while covered != all_pairs and remaining:
        # Pick the combo covering the most still-uncovered pairs (ties -> first).
        best = max(remaining, key=lambda s: len(_pairs_of(s) - covered))
        gain = _pairs_of(best) - covered
        if not gain:
            break
        covered |= _pairs_of(best)
        chosen.append(best)
        remaining.remove(best)
    return chosen


def _turn_signature(scenario: Scenario) -> tuple:
    """Normalized signature of the user-turn script for dedup (design stage 5)."""
    sig: list[tuple] = []
    for t in scenario.turns:
        say = " ".join((t.say or "").casefold().split())
        answers = tuple(sorted(t.answers.items()))
        sig.append((say, answers))
    return (scenario.flow, tuple(sig))


def dedup(scenarios: list[Scenario]) -> list[Scenario]:
    """Drop scenarios with an identical normalized turn script (first wins)."""
    seen: set[tuple] = set()
    out: list[Scenario] = []
    for s in scenarios:
        sig = _turn_signature(s)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# YAML emit
# ---------------------------------------------------------------------------


def scenario_to_doc(scenario: Scenario) -> dict[str, Any]:
    """Serialize a Scenario back to the DSL YAML mapping (round-trippable)."""
    doc: dict[str, Any] = {
        "schema_version": 1,
        "id": scenario.id,
        "flow": scenario.flow,
        "archetype": scenario.archetype,
        "language": scenario.language,
        "turn_budget": scenario.turn_budget,
    }
    if scenario.generated is not None:
        doc["generated"] = scenario.generated
    if scenario.seed_draft:
        doc["seed_draft"] = scenario.seed_draft
    if scenario.seed_params:
        doc["seed_params"] = scenario.seed_params
    turns_doc: list[dict[str, Any]] = []
    for t in scenario.turns:
        entry: dict[str, Any] = {}
        if t.say is not None:
            entry["say"] = t.say
        if t.answers:
            entry["answers"] = dict(t.answers)
        if t.answers_from_slots:
            entry["answers_from_slots"] = True
        turns_doc.append(entry)
    doc["turns"] = turns_doc
    if scenario.llm_script:
        doc["llm_script"] = list(scenario.llm_script)
    doc["save"] = scenario.save
    oracle = scenario.oracle
    oracle_doc: dict[str, Any] = {"expect_ready": oracle.expect_ready}
    if oracle.max_turns_to_ready is not None:
        oracle_doc["max_turns_to_ready"] = oracle.max_turns_to_ready
    if oracle.draft:
        oracle_doc["draft"] = oracle.draft
    if oracle.plan:
        oracle_doc["plan"] = oracle.plan
    if oracle.persisted:
        oracle_doc["persisted"] = oracle.persisted
    if oracle.forbidden_strings is not None:
        oracle_doc["forbidden_strings"] = oracle.forbidden_strings
    if oracle.never_persists:
        oracle_doc["never_persists"] = True
    if oracle.no_question_loop:
        oracle_doc["no_question_loop"] = True
    doc["oracle"] = oracle_doc
    return doc


def generate_corpus() -> list[Scenario]:
    """The full generated corpus: pairwise subset of both flows, deduped."""
    a = select_pairwise(flow_a_matrix())
    b = select_pairwise(flow_b_matrix())
    return dedup([derive_scenario(s) for s in a] + [derive_scenario(s) for s in b])


def _default_out_dir() -> Path:
    return Path(__file__).resolve().parent / "corpus" / f"v{CORPUS_VERSION}" / "generated"


def write_corpus(out_dir: Path | None = None) -> list[Path]:
    """Write the generated corpus to ``corpus/v1/generated/`` and return paths."""
    out_dir = out_dir or _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for scenario in generate_corpus():
        doc = scenario_to_doc(scenario)
        path = out_dir / f"{scenario.id}.yaml"
        path.write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Oracle-first authoring corpus generator")
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    parser.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    args = parser.parse_args(argv)
    if args.dry_run:
        scenarios = generate_corpus()
        print(f"would write {len(scenarios)} generated scenarios")
        return 0
    written = write_corpus(args.out)
    print(f"wrote {len(written)} generated scenarios to {(args.out or _default_out_dir())}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CORPUS_VERSION",
    "GENERATOR_VERSION",
    "Slots",
    "derive_scenario",
    "dedup",
    "flow_a_matrix",
    "flow_b_matrix",
    "generate_corpus",
    "scenario_to_doc",
    "select_pairwise",
    "write_corpus",
]
