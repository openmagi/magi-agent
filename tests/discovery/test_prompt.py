from __future__ import annotations

from magi_agent.discovery.models import (
    DiscoveryCorpus,
    DiscoveryPrediction,
    DiscoveryTemplate,
)
from magi_agent.discovery.prompt import build_discovery_prompt, parse_predictions

_CORPUS = DiscoveryCorpus(items={"e1": "alpha", "e2": "beta"})
_TEMPLATES = (
    DiscoveryTemplate(name="ClassA", pattern="patA", evidence_flow="flowA"),
)


def test_prompt_injects_corpus_templates_and_k() -> None:
    prompt = build_discovery_prompt(_CORPUS, _TEMPLATES, (), k=3)
    assert "e1: alpha" in prompt
    assert "e2: beta" in prompt
    assert "ClassA" in prompt
    assert "up to 3 NEW problems" in prompt


def test_prompt_injects_prior_predictions() -> None:
    prior = (
        DiscoveryPrediction(
            description="known problem",
            evidence_ids=("e1",),
            action="fix",
            problem_class="ClassA",
        ),
    )
    prompt = build_discovery_prompt(_CORPUS, _TEMPLATES, prior, k=2)
    assert "known problem" in prompt
    assert "ALREADY DISCOVERED" in prompt
    # explicit do-not-resurface instruction.
    assert "do NOT re-surface" in prompt or "Do not repeat" in prompt


def test_parse_valid_json_array() -> None:
    text = (
        '[{"description": "p1", "evidence_ids": ["e1"], "action": "a1", '
        '"problem_class": "ClassA"}]'
    )
    preds = parse_predictions(text, _CORPUS.ids())
    assert len(preds) == 1
    assert preds[0].description == "p1"
    assert preds[0].evidence_ids == ("e1",)
    assert preds[0].problem_class == "ClassA"


def test_parse_json_embedded_in_prose() -> None:
    text = (
        "Here are the problems I found:\n"
        '[{"description": "p1", "evidence_ids": ["e1"]}]\n'
        "Let me know if you need more."
    )
    preds = parse_predictions(text, _CORPUS.ids())
    assert len(preds) == 1
    assert preds[0].description == "p1"


def test_parse_skips_malformed_entries() -> None:
    text = (
        '[{"description": "good", "evidence_ids": ["e1"]}, '
        '{"no_description": true}, '
        '"not-an-object", '
        '{"description": ""}]'
    )
    preds = parse_predictions(text, _CORPUS.ids())
    # Only the first valid entry survives.
    assert len(preds) == 1
    assert preds[0].description == "good"


def test_parse_does_not_filter_by_corpus_membership() -> None:
    # Evidence id "e99" is NOT in the corpus, but parse must NOT filter it.
    text = '[{"description": "p", "evidence_ids": ["e99"]}]'
    preds = parse_predictions(text, _CORPUS.ids())
    assert len(preds) == 1
    assert preds[0].evidence_ids == ("e99",)


def test_parse_empty_array_and_no_json() -> None:
    assert parse_predictions("[]", _CORPUS.ids()) == ()
    assert parse_predictions("no json here", _CORPUS.ids()) == ()
    assert parse_predictions("[ broken json", _CORPUS.ids()) == ()
