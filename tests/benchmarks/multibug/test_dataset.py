from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from benchmarks.multibug.dataset import (
    GoldProblem,
    MultiProblemInstance,
    build_instances_from_swebench,
    load_instances,
)


def _instance_obj(instance_id: str, n_gold: int) -> dict:
    return {
        "instance_id": instance_id,
        "repo": "octo/cat",
        "anchor_commit": "deadbeef",
        "candidates": {"c1": "def a(): ...", "c2": "def b(): ...", "d1": "distractor"},
        "gold_problems": [
            {
                "problem_id": f"{instance_id}-bug{i}",
                "evidence_ids": [f"c{i + 1}"],
                "gold_patch": "@@ patch @@",
                "description": f"bug {i}",
            }
            for i in range(n_gold)
        ],
    }


def test_load_instances_jsonl_roundtrip(tmp_path) -> None:
    path = tmp_path / "inst.jsonl"
    path.write_text(
        "\n".join(json.dumps(_instance_obj(f"i{i}", 2)) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    instances = load_instances(str(path))
    assert len(instances) == 3
    assert instances[0].instance_id == "i0"
    assert len(instances[0].gold_problems) == 2
    corpus = instances[0].to_corpus()
    assert corpus.ids() == frozenset({"c1", "c2", "d1"})


def test_load_instances_json_array(tmp_path) -> None:
    path = tmp_path / "inst.json"
    path.write_text(
        json.dumps([_instance_obj("only", 3)]), encoding="utf-8"
    )
    instances = load_instances(str(path))
    assert len(instances) == 1
    assert len(instances[0].gold_problems) == 3


def test_instance_with_one_gold_rejected(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(_instance_obj("bad", 1)) + "\n", encoding="utf-8")
    with pytest.raises((ValidationError, ValueError)):
        load_instances(str(path))


def test_build_instances_from_swebench_groups_and_filters() -> None:
    issues = [
        {
            "repo": "octo/cat",
            "anchor_commit": "abc",
            "problem_id": "bug1",
            "evidence_ids": ["c1"],
            "gold_patch": "p1",
            "candidates": {"c1": "fn1"},
            "functions": ["fn1"],
        },
        {
            "repo": "octo/cat",
            "anchor_commit": "abc",
            "problem_id": "bug2",
            "evidence_ids": ["c2"],
            "gold_patch": "p2",
            "candidates": {"c2": "fn2"},
            "functions": ["fn2"],
        },
        # lone issue at a different commit -> dropped (< 2 problems)
        {
            "repo": "octo/cat",
            "anchor_commit": "xyz",
            "problem_id": "bug3",
            "evidence_ids": ["c3"],
            "gold_patch": "p3",
            "candidates": {"c3": "fn3"},
            "functions": ["fn3"],
        },
    ]
    instances = build_instances_from_swebench(issues)
    assert len(instances) == 1
    inst = instances[0]
    assert inst.anchor_commit == "abc"
    assert len(inst.gold_problems) == 2
    assert inst.candidates == {"c1": "fn1", "c2": "fn2"}


def test_build_instances_drops_single_function_groups() -> None:
    issues = [
        {
            "repo": "octo/cat",
            "anchor_commit": "abc",
            "problem_id": "bug1",
            "evidence_ids": ["c1"],
            "candidates": {"c1": "fn1"},
            "functions": ["fn1"],
        },
        {
            "repo": "octo/cat",
            "anchor_commit": "abc",
            "problem_id": "bug2",
            "evidence_ids": ["c1"],
            "candidates": {"c1": "fn1"},
            "functions": ["fn1"],  # same single function -> < 2 functions
        },
    ]
    assert build_instances_from_swebench(issues) == ()


def test_gold_problem_defaults() -> None:
    g = GoldProblem(problem_id="x")
    assert g.evidence_ids == ()
    assert g.gold_patch == ""
