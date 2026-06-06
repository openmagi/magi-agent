from __future__ import annotations

import json
from typing import AsyncGenerator

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from magi_agent.benchmarks.multibug.dataset import GoldProblem, MultiProblemInstance
from magi_agent.benchmarks.multibug.harness import run_multiproblem
from magi_agent.discovery.models import DiscoveryConfig


def _instance() -> MultiProblemInstance:
    return MultiProblemInstance(
        instance_id="i1",
        repo="octo/cat",
        anchor_commit="abc",
        candidates={"c1": "def a(): ...", "c2": "def b(): ...", "d1": "noise"},
        gold_problems=(
            GoldProblem(problem_id="bug1", evidence_ids=("c1",)),
            GoldProblem(problem_id="bug2", evidence_ids=("c2",)),
        ),
    )


def _payload(*specs: tuple[str, list[str], str]) -> str:
    return json.dumps(
        [
            {
                "description": desc,
                "evidence_ids": ev,
                "action": "fix",
                "problem_class": cls,
            }
            for desc, ev, cls in specs
        ]
    )


class _ScriptedLlm(BaseLlm):
    """Fake BaseLlm yielding a fixed JSON-array payload (GAIA test pattern)."""

    payload: str

    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text=self.payload)]
            )
        )


def test_tide_mode_returns_scorable_predictions() -> None:
    pytest.importorskip("google.adk")
    payload = _payload(
        ("bug in a", ["c1"], "Logic Error"),
        ("bug in b", ["c2"], "Logic Error"),
    )

    def model_factory(_cfg: object) -> object:
        return _ScriptedLlm(model="fake", payload=payload)

    preds = run_multiproblem(
        _instance(),
        mode="tide",
        model_factory=model_factory,
        config=DiscoveryConfig(rounds_T=3, batch_k=2),
    )
    # round 1 yields 2 new preds; round 2 dedups -> early stop.
    assert len(preds) == 2
    evidence = {tuple(p.evidence_ids) for p in preds}
    assert evidence == {("c1",), ("c2",)}
    # grounding verifier ran (audit mode tags every prediction).
    assert all(p.grounding_status is not None for p in preds)


def test_single_agent_mode_one_pass() -> None:
    pytest.importorskip("google.adk")
    payload = _payload(("only", ["c1"], "Logic Error"))

    calls = {"n": 0}

    def runner_factory(prompt: str, *, model_factory=None, model: str = "x") -> str:
        calls["n"] += 1
        return payload

    preds = run_multiproblem(
        _instance(),
        mode="single_agent",
        runner_factory=runner_factory,
        config=DiscoveryConfig(rounds_T=3, batch_k=2),
    )
    # single_agent forces rounds_T=1 -> exactly one driver call.
    assert calls["n"] == 1
    assert len(preds) == 1


def test_multi_agent_mode_does_n_passes() -> None:
    pytest.importorskip("google.adk")
    # Each pass returns a DISTINCT prediction so the union grows; if state were
    # shared they'd dedup. N = config.rounds_T = 3.
    payloads = [
        _payload(("p1", ["c1"], "A")),
        _payload(("p2", ["c2"], "B")),
        _payload(("p3", ["d1"], "C")),
    ]
    seq = {"i": 0}

    def runner_factory(prompt: str, *, model_factory=None, model: str = "x") -> str:
        # Each single-pass run is rounds_T=1 -> one call per pass; but the inner
        # loop may call once per round. Return the payload indexed by call count
        # capped, so pass k returns payloads[k].
        idx = min(seq["i"], len(payloads) - 1)
        seq["i"] += 1
        return payloads[idx]

    preds = run_multiproblem(
        _instance(),
        mode="multi_agent",
        runner_factory=runner_factory,
        config=DiscoveryConfig(rounds_T=3, batch_k=2),
    )
    # 3 independent single-shot passes -> 3 driver calls, union of 3 distinct.
    assert seq["i"] == 3
    assert len(preds) == 3
    assert {tuple(p.evidence_ids) for p in preds} == {("c1",), ("c2",), ("d1",)}


def test_multi_agent_passes_are_not_cumulatively_conditioned() -> None:
    pytest.importorskip("google.adk")
    # Each pass emits a DISTINCT prediction whose description is unique. If the
    # baseline were (wrongly) using the tide path, a later round's prompt would
    # contain an earlier round's emitted description (cumulative-state
    # conditioning). For multi_agent each pass is an INDEPENDENT rounds_T=1 run
    # that starts with prior=() -> no earlier description may ever appear.
    descriptions = ["UNIQUE_PASS_A", "UNIQUE_PASS_B", "UNIQUE_PASS_C"]
    payloads = [
        _payload((descriptions[0], ["c1"], "A")),
        _payload((descriptions[1], ["c2"], "B")),
        _payload((descriptions[2], ["d1"], "C")),
    ]
    seq = {"i": 0}
    prompts: list[str] = []

    def runner_factory(prompt: str, *, model_factory=None, model: str = "x") -> str:
        prompts.append(prompt)
        idx = min(seq["i"], len(payloads) - 1)
        seq["i"] += 1
        return payloads[idx]

    preds = run_multiproblem(
        _instance(),
        mode="multi_agent",
        runner_factory=runner_factory,
        config=DiscoveryConfig(rounds_T=3, batch_k=2),
    )

    # 3 independent passes, each rounds_T=1 -> exactly one prompt per pass.
    assert len(prompts) == 3
    # No pass's prompt may contain a description emitted by an EARLIER pass:
    # each pass starts fresh (prior empty). This is what distinguishes
    # multi_agent from the cumulatively-conditioned tide path.
    for pass_idx, prompt in enumerate(prompts):
        for earlier_idx in range(pass_idx):
            assert descriptions[earlier_idx] not in prompt, (
                f"pass {pass_idx} prompt leaked pass {earlier_idx}'s prediction "
                "-> baseline was cumulatively conditioned (tide contamination)"
            )
    # Sanity: all three distinct predictions survive the final union.
    assert {tuple(p.evidence_ids) for p in preds} == {("c1",), ("c2",), ("d1",)}


def test_multi_agent_final_dedup_unions_duplicate_predictions() -> None:
    pytest.importorskip("google.adk")
    # Two passes emit the SAME (problem_class, evidence_ids) prediction. The
    # final union must dedup them to ONE (dedup key = class + evidence set),
    # even though descriptions differ.
    payloads = [
        _payload(("same bug worded one way", ["c1"], "Logic Error")),
        _payload(("same bug worded another way", ["c1"], "Logic Error")),
    ]
    seq = {"i": 0}

    def runner_factory(prompt: str, *, model_factory=None, model: str = "x") -> str:
        idx = min(seq["i"], len(payloads) - 1)
        seq["i"] += 1
        return payloads[idx]

    preds = run_multiproblem(
        _instance(),
        mode="multi_agent",
        runner_factory=runner_factory,
        config=DiscoveryConfig(rounds_T=2, batch_k=2),
    )
    # 2 passes ran, but the duplicate (Logic Error, {c1}) collapses to one.
    assert seq["i"] == 2
    assert len(preds) == 1
    assert preds[0].evidence_ids == ("c1",)
    assert preds[0].problem_class == "Logic Error"


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        run_multiproblem(
            _instance(),
            mode="bogus",
            runner_factory=lambda *a, **k: "[]",
        )
