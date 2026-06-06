# tests/recipes/first_party/legal/test_fewshot.py
from __future__ import annotations

from magi_agent.benchmarks.legalbench.models import Example, LegalTask
from magi_agent.recipes.first_party.legal.fewshot import select_fewshot


def _task() -> LegalTask:
    train = tuple(
        Example(fields={"text": f"ex{i}"}, answer="Yes" if i % 2 else "No")
        for i in range(5)
    )
    return LegalTask(
        task_id="t",
        reasoning_type="rule-conclusion",
        base_prompt="{text}\nAnswer:",
        train=train,
        test=(),
        labels=("No", "Yes"),
    )


def test_curated_indices_are_honored_in_order() -> None:
    chosen = select_fewshot(_task(), k=2, seed=0, curated_indices=(3, 1))
    assert [e.fields["text"] for e in chosen] == ["ex3", "ex1"]


def test_seeded_selection_is_deterministic() -> None:
    a = select_fewshot(_task(), k=3, seed=7)
    b = select_fewshot(_task(), k=3, seed=7)
    assert [e.fields["text"] for e in a] == [e.fields["text"] for e in b]
    assert len(a) == 3
