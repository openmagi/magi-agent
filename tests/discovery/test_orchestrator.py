from __future__ import annotations

import json
from typing import AsyncGenerator

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from magi_agent.discovery.gate import GateDisabledError
from magi_agent.discovery.models import (
    DiscoveryConfig,
    DiscoveryCorpus,
    DiscoveryPrediction,
)
from magi_agent.discovery.orchestrator import dedup_against, run_discovery
from magi_agent.discovery.templates import (
    load_template_pack,
    static_template_provider,
)

_ENABLED = {"MAGI_DISCOVERY_ENABLED": "1"}
_CORPUS = DiscoveryCorpus(items={"e1": "alpha", "e2": "beta", "e3": "gamma"})
_PROVIDER = static_template_provider(load_template_pack("workspace"))


def _pred_json(description: str, evidence_ids: list[str], problem_class: str) -> dict:
    return {
        "description": description,
        "evidence_ids": evidence_ids,
        "action": "act",
        "problem_class": problem_class,
    }


class _ScriptedRunner:
    """A scripted single-turn driver matching the ``runner_factory`` contract.

    Returns the next canned response per call and records every prompt it saw so
    tests can assert cumulative-state conditioning.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    def __call__(self, prompt: str, *, model_factory=None, model: str = "x") -> str:
        self.prompts.append(prompt)
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return "[]"


def test_gate_raises_when_disabled() -> None:
    runner = _ScriptedRunner(["[]"])
    with pytest.raises(GateDisabledError):
        run_discovery(
            _CORPUS,
            config=DiscoveryConfig(rounds_T=2, batch_k=2),
            template_provider=_PROVIDER,
            runner_factory=runner,
            env={},  # gate OFF
        )
    assert runner.calls == 0


def test_empty_batch_early_stop() -> None:
    # Round 1 finds one problem; round 2 returns nothing new -> loop stops.
    r1 = json.dumps([_pred_json("p1", ["e1"], "Missing Deadline")])
    r2 = "[]"
    runner = _ScriptedRunner([r1, r2])
    report = run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=10, batch_k=2),
        template_provider=_PROVIDER,
        runner_factory=runner,
        env=_ENABLED,
    )
    assert report.total == 1
    # round 1 produced a new pred (rounds_used=1); round 2 was empty -> break.
    assert report.rounds_used == 1
    assert runner.calls == 2


def test_cumulative_conditioning_prompt_contains_prior() -> None:
    r1 = json.dumps([_pred_json("first problem", ["e1"], "Missing Deadline")])
    r2 = json.dumps([_pred_json("second problem", ["e2"], "Version Conflict")])
    r3 = "[]"
    runner = _ScriptedRunner([r1, r2, r3])
    run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=10, batch_k=2),
        template_provider=_PROVIDER,
        runner_factory=runner,
        env=_ENABLED,
    )
    # round-1 prompt has no prior; round-2 prompt must contain round-1's pred.
    assert "first problem" not in runner.prompts[0]
    assert "first problem" in runner.prompts[1]
    # round-3 prompt accumulates both.
    assert "first problem" in runner.prompts[2]
    assert "second problem" in runner.prompts[2]


def test_dedup_drops_repeat_across_rounds() -> None:
    # Round 2 re-surfaces the SAME (class, evidence) as round 1 -> dedup -> stop.
    r1 = json.dumps([_pred_json("p1", ["e1"], "Missing Deadline")])
    r2 = json.dumps([_pred_json("p1 reworded", ["e1"], "Missing Deadline")])
    runner = _ScriptedRunner([r1, r2])
    report = run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=10, batch_k=2),
        template_provider=_PROVIDER,
        runner_factory=runner,
        env=_ENABLED,
    )
    assert report.total == 1
    assert report.rounds_used == 1


def test_rounds_T_cutoff_respected() -> None:
    # Each round finds a brand-new problem; T=2 caps it at 2 rounds.
    responses = [
        json.dumps([_pred_json("p1", ["e1"], "Missing Deadline")]),
        json.dumps([_pred_json("p2", ["e2"], "Version Conflict")]),
        json.dumps([_pred_json("p3", ["e3"], "Stale Meeting")]),
    ]
    runner = _ScriptedRunner(responses)
    report = run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=2, batch_k=2),
        template_provider=_PROVIDER,
        runner_factory=runner,
        env=_ENABLED,
    )
    assert report.rounds_used == 2
    assert report.total == 2
    assert runner.calls == 2


def test_grounding_verifier_hook_applied() -> None:
    r1 = json.dumps(
        [
            _pred_json("keep", ["e1"], "Missing Deadline"),
            _pred_json("drop", ["e2"], "Version Conflict"),
        ]
    )
    runner = _ScriptedRunner([r1, "[]"])

    def verifier(batch, corpus):
        return [p for p in batch if p.description == "keep"]

    report = run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=5, batch_k=2),
        template_provider=_PROVIDER,
        runner_factory=runner,
        grounding_verifier=verifier,
        env=_ENABLED,
    )
    assert report.total == 1
    assert report.predictions[0].description == "keep"


def test_dedup_against_helper() -> None:
    prior = (DiscoveryPrediction(description="a", evidence_ids=("e1",), problem_class="C"),)
    batch = (
        DiscoveryPrediction(description="a2", evidence_ids=("e1",), problem_class="C"),
        DiscoveryPrediction(description="b", evidence_ids=("e2",), problem_class="C"),
        DiscoveryPrediction(description="b-dup", evidence_ids=("e2",), problem_class="C"),
    )
    out = dedup_against(batch, prior)
    # first dropped (matches prior); second kept; third dropped (intra-batch dup).
    assert len(out) == 1
    assert out[0].description == "b"


# --- GAIA-style fake-model integration: proves drive_runner_once seam works ---


class _ScriptedLlm(BaseLlm):
    """Minimal fake BaseLlm yielding a fixed JSON array (GAIA test pattern)."""

    payload: str

    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text=self.payload)]
            )
        )


def test_run_discovery_with_default_driver_and_fake_model() -> None:
    pytest.importorskip("google.adk")

    payload = json.dumps([_pred_json("real-driver problem", ["e1"], "Missing Deadline")])

    def model_factory(_cfg: object) -> object:
        return _ScriptedLlm(model="fake", payload=payload)

    # runner_factory=None -> uses the real drive_runner_once seam, but the fake
    # model means no provider traffic. Round 2 returns the same payload, which
    # dedups -> early stop at rounds_used==1.
    report = run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=3, batch_k=2),
        template_provider=_PROVIDER,
        model_factory=model_factory,
        env=_ENABLED,
    )
    assert report.total == 1
    assert report.rounds_used == 1
    assert report.predictions[0].description == "real-driver problem"
