"""Engine wiring for the force-merged HARD validators / evidence.

``recipes/reliability_policy._HARD_VALIDATORS`` /``_HARD_EVIDENCE`` force-merge
the BARE refs ``no_production_attachment`` / ``public_redaction`` (validators)
and ``redaction_audit`` (evidence) into EVERY recipe's final-gate policy. They
have no public-ref prefix and (before this change) no live producer, so the
pre-final gate always blocks even on a perfect turn.

These tests drive the real ``MagiEngineDriver`` pre-final gate over a narrow
policy whose ONLY outstanding requirements are those three bare hard refs, and
prove the three satisfiers make them legitimately satisfiable on a clean turn
and blockable on a real credential leak:

* Flag OFF (default): inert; the bare hard refs are never emitted, so the gate
  blocks (byte-identical to today).
* Flag ON + a clean final answer (no credential): all three bare labels are
  emitted into the harvested refs -> the requirements are satisfied -> pass.
* Flag ON + a final answer that leaks a real credential (API key / JWT):
  ``public_redaction`` and ``redaction_audit`` are NOT emitted -> the gate
  blocks on the credential.
* The satisfiers ONLY clear their own labels (an unrelated missing validator
  is untouched), and the credential scan does NOT match bare paths / emails /
  the bare word ``token``.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import AsyncIterator

import magi_agent.cli.engine as engine_module
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.runtime.events import RuntimeEvent

_HARD_VALIDATORS = ("no_production_attachment", "public_redaction")
_HARD_EVIDENCE = ("redaction_audit",)

# A real-shaped JWT and an OpenAI-style key, assembled at runtime so the file
# carries no committed secret-shaped literal (GH push-protection).
_REAL_JWT = ".".join(
    (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    )
)
_REAL_API_KEY = "sk-" + "proj-abcdef1234567890ABCDEFXYZ"
# A clean final answer that DELIBERATELY contains a filesystem path, an email,
# and the bare word "token" — none of which may count as a credential.
_CLEAN_FINAL = (
    "Here is what the file at /Users/kevin/notes.md says: the token economy is "
    "growing. Contact bob@example.com for details."
)


class _NoopRunner:
    async def run_async(self, **kwargs: object) -> AsyncIterator[object]:
        if False:
            yield kwargs


class _FakePart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, *, role: str, parts: list[object]) -> None:
        self.role = role
        self.parts = parts


class _FakeTypes:
    Content = _FakeContent
    Part = _FakePart


class _CapturedRunnerInput:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.harness_state = kwargs.get("harnessState")


class _TextEmittingAdapter:
    def __init__(self, *, runner: object) -> None:
        self.runner = runner

    async def run_turn(self, runner_input: object) -> AsyncIterator[object]:
        del runner_input
        text = getattr(self.runner, "final_text", "")
        if text:
            yield {"type": "text_delta", "delta": text}


class _PassthroughBridge:
    def __init__(self, *, live_compatible: bool) -> None:
        self.live_compatible = live_compatible

    def project_adk_event(self, adk_event: object, *, turn_id: str) -> object:
        del turn_id
        if isinstance(adk_event, Mapping):
            return type("Projection", (), {"agent_events": [dict(adk_event)]})()
        return type("Projection", (), {"agent_events": []})()


def _engine_deps() -> dict[str, object]:
    return {
        "types": _FakeTypes,
        "OpenMagiEventBridge": _PassthroughBridge,
        "OpenMagiRunnerAdapter": _TextEmittingAdapter,
        "RunnerTurnInput": _CapturedRunnerInput,
        "sanitize_agent_event": lambda event: event,
    }


class _TextRunner(_NoopRunner):
    def __init__(self, *, final_text: str) -> None:
        self.final_text = final_text


def _hard_only_assembly(
    *, extra_validators: tuple[str, ...] = ()
) -> RunnerPolicyAssembly:
    """Policy whose outstanding requirements are only the bare hard refs."""
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=("openmagi.source-grounded",),
        evidenceRequirements=_HARD_EVIDENCE,
        requiredValidators=_HARD_VALIDATORS + extra_validators,
        missingEvidenceAction="insufficient_evidence",
        repairPolicy={"action": "insufficient_evidence", "source": "recipe-materializer"},
        taskProfile={"taskType": "research"},
    )


def _drive(driver: MagiEngineDriver, *, prompt: str) -> list[object]:
    async def _run() -> list[object]:
        return [
            item
            async for item in driver.run_turn_stream(
                runtime=object(),
                turn_input={"prompt": prompt, "session_id": "s", "turn_id": "t"},
                cancel=asyncio.Event(),
            )
        ]

    return asyncio.run(_run())


def _gate_payload(items: list[object]) -> dict[str, object]:
    return next(
        item.payload
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "pre_final_evidence_gate"
    )


def test_flag_off_clean_turn_still_blocks_on_hard_refs(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_CLEAN_FINAL),
        runner_policy_assembly=_hard_only_assembly(),
        evidence_collector=lambda turn_id: (),
    )

    items = _drive(driver, prompt="summarize the file")
    terminal = items[-1]
    gate = _gate_payload(items)

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert gate["decision"] == "block"
    assert set(gate["missingValidators"]) == set(_HARD_VALIDATORS)
    assert set(gate["missingEvidence"]) == set(_HARD_EVIDENCE)


def test_flag_on_clean_turn_satisfies_all_three_hard_refs(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_CLEAN_FINAL),
        runner_policy_assembly=_hard_only_assembly(),
        evidence_collector=lambda turn_id: (),
    )

    items = _drive(driver, prompt="summarize the file")
    terminal = items[-1]
    gate = _gate_payload(items)

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert gate["decision"] == "pass"
    assert gate["missingValidators"] == []
    assert gate["missingEvidence"] == []
    for label in (*_HARD_VALIDATORS, *_HARD_EVIDENCE):
        assert label in gate["matchedRefs"]


def test_flag_on_credential_jwt_blocks_on_public_redaction(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    leak = f"The discovered token is {_REAL_JWT} — sharing it here."
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=leak),
        runner_policy_assembly=_hard_only_assembly(),
        evidence_collector=lambda turn_id: (),
    )

    items = _drive(driver, prompt="dump the secret")
    terminal = items[-1]
    gate = _gate_payload(items)

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert gate["decision"] == "block"
    # public_redaction stays missing (credential found) and redaction_audit too.
    assert "public_redaction" in gate["missingValidators"]
    assert "redaction_audit" in gate["missingEvidence"]
    # The no-production-attachment invariant is unrelated to the leak -> satisfied.
    assert "no_production_attachment" not in gate["missingValidators"]
    assert "no_production_attachment" in gate["matchedRefs"]


def test_flag_on_credential_apikey_blocks_on_public_redaction(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    leak = f"Here is the key you asked for: {_REAL_API_KEY}"
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=leak),
        runner_policy_assembly=_hard_only_assembly(),
        evidence_collector=lambda turn_id: (),
    )

    items = _drive(driver, prompt="dump the key")
    gate = _gate_payload(items)

    assert gate["decision"] == "block"
    assert "public_redaction" in gate["missingValidators"]
    assert "redaction_audit" in gate["missingEvidence"]
    assert "public_redaction" not in gate["matchedRefs"]


def test_flag_on_does_not_satisfy_unrelated_missing_validator(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_CLEAN_FINAL),
        runner_policy_assembly=_hard_only_assembly(
            extra_validators=("verifier:research-source-evidence",)
        ),
        evidence_collector=lambda turn_id: (),
    )

    items = _drive(driver, prompt="summarize the file")
    gate = _gate_payload(items)

    assert gate["decision"] == "block"
    # all three hard refs cleared; the unrelated named ref still missing.
    assert gate["missingValidators"] == ["verifier:research-source-evidence"]
    for label in (*_HARD_VALIDATORS, *_HARD_EVIDENCE):
        assert label in gate["matchedRefs"]
