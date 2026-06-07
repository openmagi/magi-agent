# tests/recipes/first_party/legal/test_recipe.py
from __future__ import annotations

from magi_agent.benchmarks.legalbench.models import Example, LegalTask
from magi_agent.recipes.first_party.legal.recipe import (
    LegalCheckpoints,
    build_prompt,
    parse_output,
)


def _task() -> LegalTask:
    train = (
        Example(fields={"text": "soft soap"}, answer="Yes"),
        Example(fields={"text": "STAR cars"}, answer="No"),
    )
    test = (Example(fields={"text": "ivory soap"}, answer="Yes"),)
    return LegalTask(
        task_id="abercrombie",
        reasoning_type="rule-conclusion",
        base_prompt="Mark: {text}\nAnswer:",
        train=train,
        test=test,
        labels=("No", "Yes"),
    )


def test_all_checkpoints_off_renders_only_base_prompt() -> None:
    cp = LegalCheckpoints(
        few_shot=False, rule_inject=False, prompt_variant=False, constrained_parse=False
    )
    prompt = build_prompt(_task(), _task().test[0], checkpoints=cp)
    assert prompt == "Mark: ivory soap\nAnswer:"


def test_checkpoints_on_inject_rule_and_fewshot() -> None:
    cp = LegalCheckpoints(few_shot=True, rule_inject=True, k=2, seed=0)
    prompt = build_prompt(_task(), _task().test[0], checkpoints=cp)
    assert prompt.startswith("Rule: Trademark")  # rule injected first
    assert "soft soap" in prompt  # few-shot exemplar present
    assert "Mark: ivory soap" in prompt  # test instance present


def test_parse_output_respects_toggle() -> None:
    cp_on = LegalCheckpoints(constrained_parse=True)
    cp_off = LegalCheckpoints(constrained_parse=False)
    assert parse_output("answer: No", _task(), checkpoints=cp_on) == "No"
    assert parse_output("answer: No", _task(), checkpoints=cp_off) == "answer: No"
