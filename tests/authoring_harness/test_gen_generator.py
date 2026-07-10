"""U5 generator + paraphrase unit tests (offline, CI).

The generator (``benchmarks.authoring.gen``) is a PURE, TOTAL slot -> Scenario
function; the paraphrase expander (``benchmarks.authoring.paraphrase``) is an
offline breadth stage whose guardrails DROP (never repair) a paraphrase that
would drift a label. Neither touches the network; both run in normal CI.

The generated corpus itself is replayed by test_t1_golden_corpus.py (design 12
U5: no new CI entry); THIS module unit-tests the machinery that produced it.
"""
from __future__ import annotations

import pytest

from benchmarks.authoring.gen import (
    GENERATOR_VERSION,
    Slots,
    dedup,
    derive_scenario,
    flow_a_matrix,
    flow_b_matrix,
    generate_corpus,
    scenario_to_doc,
    select_pairwise,
)
from benchmarks.authoring.paraphrase import (
    ScriptedParaphraser,
    expand_corpus,
    expand_scenario,
    paraphrase_ok,
    slot_literals,
)
from benchmarks.authoring.scenario import load_scenario
from magi_agent.customize.custom_rules import _LEGAL, validate_custom_rule

# ---------------------------------------------------------------------------
# Purity + totality of the slot -> oracle function.
# ---------------------------------------------------------------------------


def test_derive_is_pure() -> None:
    """Same slots -> byte-identical scenario doc (no hidden state / randomness)."""
    slots = flow_a_matrix()[0]
    a = scenario_to_doc(derive_scenario(slots))
    b = scenario_to_doc(derive_scenario(slots))
    assert a == b


def test_derive_is_total_over_the_matrix() -> None:
    """Every matrix point yields a valid, loadable Scenario (no raises)."""
    matrix = flow_a_matrix() + flow_b_matrix()
    assert matrix, "matrix is empty"
    for slots in matrix:
        scenario = derive_scenario(slots)
        assert scenario.id
        assert scenario.flow == slots.flow
        assert scenario.generated is not None
        assert scenario.generated["generator_version"] == GENERATOR_VERSION


@pytest.mark.parametrize("slots", flow_a_matrix())
def test_flow_a_oracle_targets_a_validator_clean_rule(slots: Slots) -> None:
    """The derived draft oracle describes a rule that validate_custom_rule
    accepts — the label is derived from slots and is honest by construction."""
    scenario = derive_scenario(slots)
    if scenario.archetype == "out_of_scope":
        assert scenario.oracle.expect_ready is False
        assert scenario.oracle.never_persists is True
        return
    # Reconstruct the target rule from the oracle + seed and validate it.
    d = scenario.oracle.draft
    rule = {
        "id": "cr_probe",
        "scope": d["scope"],
        "enabled": True,
        "firesAt": d["firesAt"],
        "action": d["action"],
        "what": {
            "kind": d["what.kind"],
            "payload": (scenario.seed_draft.get("what") or {}).get("payload"),
        },
    }
    assert validate_custom_rule(rule) == [], f"{slots}: derived oracle is not clean"


def test_flow_a_answers_only_use_legal_vocab() -> None:
    """Every answer value the generator emits is a legal vocabulary member for
    its slot (so operator answers land, never silently dropped)."""
    from magi_agent.customize.custom_rules import ACTIONS, FIRES_AT, KINDS, SCOPES

    vocab = {"q_what.kind": KINDS, "q_firesAt": FIRES_AT, "q_action": ACTIONS, "q_scope": SCOPES}
    for slots in flow_a_matrix():
        scenario = derive_scenario(slots)
        for turn in scenario.turns:
            for qid, val in turn.answers.items():
                if qid in vocab:
                    assert val in vocab[qid], f"{scenario.id}: {qid}={val!r} out of vocab"


def test_corrective_retargets_between_two_clean_actions() -> None:
    """A corrective scenario's first answer differs from the final action, and
    both are legal for the (kind, firesAt)."""
    correctives = [
        derive_scenario(s) for s in flow_a_matrix() if s.quirk == "corrective"
    ]
    assert correctives, "no corrective scenarios generated"
    for sc in correctives:
        gen = sc.generated["slots"]
        legal = _LEGAL[gen["kind"]][gen["firesAt"]]
        first = sc.turns[0].answers.get("q_action")
        final = sc.turns[1].answers.get("q_action")
        assert first in legal and final in legal
        assert first != final, f"{sc.id}: corrective did not retarget the action"
        assert final == gen["action"]


# ---------------------------------------------------------------------------
# Pairwise selection + dedup.
# ---------------------------------------------------------------------------


def _all_pairs(matrix):
    from benchmarks.authoring.gen import _pairs_of

    pairs = set()
    for s in matrix:
        pairs |= _pairs_of(s)
    return pairs


def test_pairwise_covers_every_slot_pair() -> None:
    """The pairwise subset covers EVERY slot value-pair the full matrix has."""
    for matrix in (flow_a_matrix(), flow_b_matrix()):
        subset = select_pairwise(matrix)
        assert _all_pairs(subset) == _all_pairs(matrix), "pairwise subset drops a pair"
        assert len(subset) < len(matrix), "pairwise selection did not shrink the matrix"


def test_pairwise_is_deterministic() -> None:
    a = [s.__dict__ for s in select_pairwise(flow_a_matrix())]
    b = [s.__dict__ for s in select_pairwise(flow_a_matrix())]
    assert a == b


def test_dedup_drops_identical_turn_scripts() -> None:
    s = flow_a_matrix()[0]
    one = derive_scenario(s)
    assert len(dedup([one, one])) == 1


def test_generate_corpus_is_nonempty_and_deduped() -> None:
    corpus = generate_corpus()
    assert len(corpus) >= 40
    # No two scenarios share an id.
    ids = [s.id for s in corpus]
    assert len(ids) == len(set(ids))


def test_scenario_to_doc_round_trips(tmp_path) -> None:
    """A generated scenario serializes to YAML the loader accepts unchanged."""
    import yaml

    scenario = derive_scenario(flow_a_matrix()[0])
    doc = scenario_to_doc(scenario)
    f = tmp_path / f"{scenario.id}.yaml"
    f.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    reloaded = load_scenario(f)
    assert reloaded.id == scenario.id
    assert reloaded.flow == scenario.flow
    assert reloaded.oracle.expect_ready == scenario.oracle.expect_ready


# ---------------------------------------------------------------------------
# Paraphrase expander (offline, scripted fake).
# ---------------------------------------------------------------------------


def _a_flow_b_scenario():
    for s in flow_b_matrix():
        sc = derive_scenario(s)
        if sc.turns and sc.turns[0].say:
            return sc
    raise AssertionError("no flow-B scenario")


def test_paraphrase_accepts_a_literal_preserving_rewrite() -> None:
    scenario = _a_flow_b_scenario()
    original = scenario.turns[0].say
    # A rewrite that keeps the tool + domain literals.
    lit = slot_literals(scenario)
    assert lit, "flow-B scenario has no slot literals"
    rewritten = f"please gate access so that {original}"
    para = ScriptedParaphraser({original: rewritten})
    out = expand_scenario(scenario, para)
    assert out is not None
    assert out.turns[0].say == rewritten
    assert out.generated["paraphrase_model"] == "scripted-fake"
    assert out.id.endswith("_pp")
    # oracle + llm_script are copied untouched (labels never drift).
    assert out.oracle.plan == scenario.oracle.plan
    assert out.llm_script == scenario.llm_script


def test_paraphrase_drops_when_a_slot_literal_is_lost() -> None:
    scenario = _a_flow_b_scenario()
    original = scenario.turns[0].say
    lit = slot_literals(scenario)[0]  # e.g. the gated tool
    # Strip the literal out of the paraphrase -> must be dropped.
    dropped = original.replace(lit, "SOME_TOOL")
    assert lit not in dropped
    para = ScriptedParaphraser({original: dropped})
    assert expand_scenario(scenario, para) is None


def test_paraphrase_drops_on_language_drift() -> None:
    # A ko scenario paraphrased into ASCII-only English must be dropped.
    ko = next(
        derive_scenario(s) for s in flow_b_matrix()
        if s.language == "ko" and derive_scenario(s).turns[0].say
    )
    original = ko.turns[0].say
    # Keep the literals but drop all Hangul -> language-family check fails.
    lits = slot_literals(ko)
    english = "block " + " ".join(lits) + " until verified"
    para = ScriptedParaphraser({original: english})
    assert expand_scenario(ko, para) is None


def test_paraphrase_drops_on_length_blowup() -> None:
    scenario = _a_flow_b_scenario()
    original = scenario.turns[0].say
    lits = slot_literals(scenario)
    runaway = " ".join(lits) + (" padding" * 500)
    para = ScriptedParaphraser({original: runaway})
    assert expand_scenario(scenario, para) is None


def test_paraphrase_drops_a_noop() -> None:
    """An identity paraphrase adds no breadth and is dropped."""
    scenario = _a_flow_b_scenario()
    para = ScriptedParaphraser()  # default is identity
    assert expand_scenario(scenario, para) is None


def test_paraphrase_ok_unit() -> None:
    assert paraphrase_ok("block execute_trade now", "please block execute_trade now",
                         language="en", literals=["execute_trade"])
    # literal lost
    assert not paraphrase_ok("block execute_trade", "block the trade",
                             language="en", literals=["execute_trade"])
    # empty
    assert not paraphrase_ok("x", "", language="en", literals=[])


def test_expand_corpus_dedups_and_drops() -> None:
    scenarios = [derive_scenario(s) for s in flow_b_matrix()[:4]]
    # A paraphraser that maps every first-say to the SAME text but keeps literals
    # would still differ per scenario (different literals), so map identity for
    # some (dropped as no-op) and a valid rewrite for others.
    mapping = {}
    for sc in scenarios:
        say = sc.turns[0].say
        mapping[say] = f"kindly {say}"  # keeps all literals, changes text
    para = ScriptedParaphraser(mapping)
    out = expand_corpus(scenarios, para)
    assert len(out) >= 1
    assert all(o.id.endswith("_pp") for o in out)
    assert len({o.id for o in out}) == len(out)
