from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.discovery.models import (
    DiscoveryConfig,
    DiscoveryCorpus,
    DiscoveryPrediction,
    DiscoveryReport,
    DiscoveryState,
    DiscoveryTemplate,
)


def test_prediction_is_frozen_and_forbids_extra() -> None:
    pred = DiscoveryPrediction(
        description="d", evidence_ids=("e1",), action="a", problem_class="C"
    )
    with pytest.raises(ValidationError):
        DiscoveryPrediction(description="d", bogus=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        pred.description = "x"  # type: ignore[misc]


def test_corpus_ids_and_render_index() -> None:
    corpus = DiscoveryCorpus(items={"e2": "second  item\nwith\twhitespace", "e1": "first"})
    assert corpus.ids() == frozenset({"e1", "e2"})
    index = corpus.render_index()
    # sorted by id, whitespace collapsed.
    assert index.splitlines()[0].startswith("- e1: first")
    assert "second item with whitespace" in index


def test_render_index_truncates_long_content() -> None:
    corpus = DiscoveryCorpus(items={"e1": "x" * 500})
    line = corpus.render_index(max_chars=20)
    assert line.endswith("…")
    assert len(line) <= len("- e1: ") + 20


def test_config_named_defaults() -> None:
    ws = DiscoveryConfig.workspace()
    assert ws.rounds_T == 10
    assert ws.batch_k == 3
    repo = DiscoveryConfig.repository()
    assert repo.rounds_T == 3


def test_state_empty_and_extend_is_immutable() -> None:
    state = DiscoveryState.empty()
    assert state.predictions == ()
    assert state.rounds_used == 0

    p1 = DiscoveryPrediction(description="a")
    next_state = state.extend([p1])
    assert next_state.predictions == (p1,)
    assert next_state.rounds_used == 1
    # original unchanged.
    assert state.predictions == ()
    assert state.rounds_used == 0


def test_report_summary() -> None:
    preds = (
        DiscoveryPrediction(description="a", problem_class="X"),
        DiscoveryPrediction(description="b", problem_class="X"),
        DiscoveryPrediction(description="c"),
    )
    report = DiscoveryReport(predictions=preds, rounds_used=2)
    assert report.total == 3
    counts = report.counts_by_class()
    assert counts["X"] == 2
    assert counts["(unclassified)"] == 1


def test_template_fields() -> None:
    tpl = DiscoveryTemplate(name="N", pattern="P", evidence_flow="F")
    assert tpl.name == "N"
    assert tpl.pattern == "P"
    assert tpl.evidence_flow == "F"
