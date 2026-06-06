from __future__ import annotations

import json
from typing import AsyncGenerator

import pytest

from magi_agent.discovery.gate import GateDisabledError
from magi_agent.discovery.grounding import (
    GROUNDING_STRICT_ENV,
    make_grounding_verifier,
    verify_grounding,
)
from magi_agent.discovery.models import (
    DiscoveryConfig,
    DiscoveryCorpus,
    DiscoveryPrediction,
)
from magi_agent.discovery.orchestrator import run_discovery
from magi_agent.discovery.templates import (
    load_template_pack,
    static_template_provider,
)

_ENABLED = {"MAGI_DISCOVERY_ENABLED": "1"}
_CORPUS = DiscoveryCorpus(items={"e1": "alpha", "e2": "beta", "e3": "gamma"})
_PROVIDER = static_template_provider(load_template_pack("workspace"))


def _pred(description: str, evidence_ids: tuple[str, ...]) -> DiscoveryPrediction:
    return DiscoveryPrediction(
        description=description,
        evidence_ids=evidence_ids,
        action="act",
        problem_class="Missing Deadline",
    )


def _pred_json(description: str, evidence_ids: list[str], problem_class: str) -> dict:
    return {
        "description": description,
        "evidence_ids": evidence_ids,
        "action": "act",
        "problem_class": problem_class,
    }


# --- verify_grounding: status computation ---------------------------------


def test_valid_all_evidence_present_is_grounded_kept_both_modes() -> None:
    batch = (_pred("valid", ("e1", "e2")),)
    audited = verify_grounding(batch, _CORPUS, mode="audit")
    strict = verify_grounding(batch, _CORPUS, mode="strict")
    assert len(audited) == 1
    assert audited[0].grounding_status == "grounded"
    assert len(strict) == 1
    assert strict[0].grounding_status == "grounded"


def test_orphaned_no_evidence_present_is_ungrounded() -> None:
    batch = (_pred("orphan", ("missing-a", "missing-b")),)
    audited = verify_grounding(batch, _CORPUS, mode="audit")
    strict = verify_grounding(batch, _CORPUS, mode="strict")
    # audit keeps + tags; strict drops.
    assert len(audited) == 1
    assert audited[0].grounding_status == "ungrounded"
    assert strict == ()


def test_mixed_some_present_is_partial_kept_both_modes() -> None:
    batch = (_pred("mixed", ("e1", "missing")),)
    audited = verify_grounding(batch, _CORPUS, mode="audit")
    strict = verify_grounding(batch, _CORPUS, mode="strict")
    assert len(audited) == 1
    assert audited[0].grounding_status == "partial"
    assert len(strict) == 1
    assert strict[0].grounding_status == "partial"


def test_empty_evidence_ids_is_ungrounded() -> None:
    batch = (_pred("no-evidence", ()),)
    audited = verify_grounding(batch, _CORPUS, mode="audit")
    strict = verify_grounding(batch, _CORPUS, mode="strict")
    assert audited[0].grounding_status == "ungrounded"
    assert strict == ()


def test_audit_mode_never_drops_mixed_batch() -> None:
    batch = (
        _pred("grounded", ("e1",)),
        _pred("partial", ("e1", "x")),
        _pred("ungrounded", ("x",)),
    )
    out = verify_grounding(batch, _CORPUS, mode="audit")
    assert [p.grounding_status for p in out] == ["grounded", "partial", "ungrounded"]


def test_strict_mode_drops_only_ungrounded() -> None:
    batch = (
        _pred("grounded", ("e1",)),
        _pred("partial", ("e1", "x")),
        _pred("ungrounded", ("x",)),
    )
    out = verify_grounding(batch, _CORPUS, mode="strict")
    assert [p.description for p in out] == ["grounded", "partial"]


# --- make_grounding_verifier: env resolution + hook shape ----------------


def test_factory_default_resolves_to_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GROUNDING_STRICT_ENV, raising=False)
    verifier = make_grounding_verifier()
    batch = (_pred("orphan", ("x",)),)
    out = tuple(verifier(batch, _CORPUS))
    # audit keeps the ungrounded prediction, tagged.
    assert len(out) == 1
    assert out[0].grounding_status == "ungrounded"


def test_factory_env_truthy_resolves_to_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GROUNDING_STRICT_ENV, "1")
    verifier = make_grounding_verifier()
    batch = (_pred("orphan", ("x",)),)
    out = tuple(verifier(batch, _CORPUS))
    # strict drops the ungrounded prediction.
    assert out == ()


def test_factory_explicit_mode_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GROUNDING_STRICT_ENV, "1")
    verifier = make_grounding_verifier(mode="audit")
    batch = (_pred("orphan", ("x",)),)
    out = tuple(verifier(batch, _CORPUS))
    assert len(out) == 1
    assert out[0].grounding_status == "ungrounded"


def test_verifier_conforms_to_hook_signature() -> None:
    # The orchestrator calls verifier(batch, corpus) -> Sequence[Prediction].
    verifier = make_grounding_verifier(mode="strict")
    out = verifier((_pred("ok", ("e1",)),), _CORPUS)
    assert tuple(out)[0].grounding_status == "grounded"


# --- integration: hook plugs into run_discovery --------------------------


class _ScriptedRunner:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def __call__(self, prompt: str, *, model_factory=None, model: str = "x") -> str:
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return "[]"


def test_integration_strict_drops_ungrounded_from_report() -> None:
    r1 = json.dumps(
        [
            _pred_json("grounded one", ["e1"], "Missing Deadline"),
            _pred_json("orphaned one", ["nope"], "Version Conflict"),
        ]
    )
    runner = _ScriptedRunner([r1, "[]"])
    report = run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=5, batch_k=2),
        template_provider=_PROVIDER,
        runner_factory=runner,
        grounding_verifier=make_grounding_verifier(mode="strict"),
        env=_ENABLED,
    )
    assert report.total == 1
    assert report.predictions[0].description == "grounded one"
    assert report.predictions[0].grounding_status == "grounded"


def test_integration_audit_keeps_ungrounded_tagged() -> None:
    r1 = json.dumps(
        [
            _pred_json("grounded one", ["e1"], "Missing Deadline"),
            _pred_json("orphaned one", ["nope"], "Version Conflict"),
        ]
    )
    runner = _ScriptedRunner([r1, "[]"])
    report = run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=5, batch_k=2),
        template_provider=_PROVIDER,
        runner_factory=runner,
        grounding_verifier=make_grounding_verifier(mode="audit"),
        env=_ENABLED,
    )
    assert report.total == 2
    by_desc = {p.description: p.grounding_status for p in report.predictions}
    assert by_desc == {"grounded one": "grounded", "orphaned one": "ungrounded"}


# --- GAIA-style fake-model integration: real drive_runner_once seam ------


def test_integration_strict_with_fake_model_drives_seam() -> None:
    pytest.importorskip("google.adk")
    from google.adk.models import BaseLlm, LlmResponse  # noqa: PLC0415
    from google.genai import types  # noqa: PLC0415

    class _ScriptedLlm(BaseLlm):
        payload: str

        async def generate_content_async(
            self, llm_request: object, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            yield LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part(text=self.payload)]
                )
            )

    payload = json.dumps(
        [
            _pred_json("grounded driver", ["e1"], "Missing Deadline"),
            _pred_json("orphan driver", ["ghost"], "Version Conflict"),
        ]
    )

    def model_factory(_cfg: object) -> object:
        return _ScriptedLlm(model="fake", payload=payload)

    report = run_discovery(
        _CORPUS,
        config=DiscoveryConfig(rounds_T=3, batch_k=2),
        template_provider=_PROVIDER,
        model_factory=model_factory,
        grounding_verifier=make_grounding_verifier(mode="strict"),
        env=_ENABLED,
    )
    assert report.total == 1
    assert report.predictions[0].description == "grounded driver"


def test_gate_still_applies_with_verifier() -> None:
    runner = _ScriptedRunner(["[]"])
    with pytest.raises(GateDisabledError):
        run_discovery(
            _CORPUS,
            config=DiscoveryConfig(rounds_T=2, batch_k=2),
            template_provider=_PROVIDER,
            runner_factory=runner,
            grounding_verifier=make_grounding_verifier(mode="strict"),
            env={},
        )
    assert runner.calls == 0
