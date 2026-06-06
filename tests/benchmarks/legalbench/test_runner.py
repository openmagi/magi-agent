# tests/benchmarks/legalbench/test_runner.py
from __future__ import annotations

from magi_agent.benchmarks.legalbench.models import Example, LegalTask
from magi_agent.benchmarks.legalbench.runner import (
    ablation_configs,
    baseline_checkpoints,
    run_subset,
)
from magi_agent.recipes.first_party.legal.recipe import LegalCheckpoints


def _task() -> LegalTask:
    return LegalTask(
        task_id="abercrombie",
        reasoning_type="rule-conclusion",
        base_prompt="Mark: {text}\nAnswer:",
        train=(Example(fields={"text": "soft soap"}, answer="Yes"),),
        test=(
            Example(fields={"text": "ivory"}, answer="Yes"),
            Example(fields={"text": "STAR"}, answer="No"),
        ),
        labels=("No", "Yes"),
    )


def test_run_subset_produces_one_record_per_test_instance() -> None:
    def fake_complete(prompt: str) -> str:
        return "Yes" if "ivory" in prompt else "No"

    records = run_subset(
        [_task()], complete=fake_complete, checkpoints=LegalCheckpoints()
    )
    assert len(records) == 2
    assert records[0].predicted == "Yes"
    assert records[0].gold == "Yes"
    assert records[1].predicted == "No"
    assert records[0].reasoning_type == "rule-conclusion"
    assert records[1].index == 1
    assert records[1].gold == "No"


def test_baseline_disables_all_checkpoints() -> None:
    cp = baseline_checkpoints()
    assert not (cp.few_shot or cp.rule_inject or cp.prompt_variant or cp.constrained_parse)


def test_ablation_yields_one_config_per_checkpoint() -> None:
    cells = ablation_configs(LegalCheckpoints())
    assert {c.disabled for c in cells} == {
        "few_shot",
        "rule_inject",
        "prompt_variant",
        "constrained_parse",
    }
    fs = next(c for c in cells if c.disabled == "few_shot")
    assert fs.checkpoints.few_shot is False
    assert fs.checkpoints.rule_inject is True
