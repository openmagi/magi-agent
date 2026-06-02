from __future__ import annotations

import subprocess
import sys

from openmagi_core_agent.harness.discipline_boundary import (
    DisciplineBoundary,
    DisciplineBoundaryConfig,
    DisciplineRequest,
)


def _request(
    check: str,
    *,
    output_text: str = "done",
    evidence_refs: tuple[str, ...] = (),
    metadata: dict[str, object] | None = None,
) -> DisciplineRequest:
    return DisciplineRequest(
        requestId="req-discipline-1",
        turnId="turn-1",
        check=check,
        outputText=output_text,
        evidenceRefs=evidence_refs,
        metadata=metadata or {},
    )


def test_discipline_boundary_is_disabled_by_default() -> None:
    decision = DisciplineBoundary(DisciplineBoundaryConfig()).evaluate(
        _request("self_claim")
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("discipline_boundary_disabled",)
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_self_claim_and_coding_hard_mode_require_evidence_refs() -> None:
    boundary = DisciplineBoundary(DisciplineBoundaryConfig(enabled=True))

    self_claim = boundary.evaluate(_request("self_claim", output_text="Tests passed."))
    hard_mode = boundary.evaluate(
        _request("coding_hard_mode", output_text="Implementation complete.")
    )
    with_evidence = boundary.evaluate(
        _request(
            "self_claim",
            output_text="Tests passed.",
            evidence_refs=("test:pytest-priority-a",),
        )
    )

    assert self_claim.status == "blocked"
    assert self_claim.reason_codes == ("self_claim_requires_evidence",)
    assert hard_mode.status == "blocked"
    assert hard_mode.reason_codes == ("coding_hard_mode_evidence_required",)
    assert with_evidence.status == "passed"


def test_pre_refusal_output_purity_and_language_gates_block_bad_outputs() -> None:
    boundary = DisciplineBoundary(DisciplineBoundaryConfig(enabled=True))

    refusal = boundary.evaluate(
        _request(
            "pre_refusal",
            output_text="I can't help with that.",
            metadata={"availableAction": True},
        )
    )
    purity = boundary.evaluate(
        _request("output_purity", output_text="raw_tool_args /Users/kevin/private")
    )
    language = boundary.evaluate(
        _request(
            "response_language",
            output_text="This is English only.",
            metadata={"expectedLanguage": "ko"},
        )
    )

    assert refusal.status == "blocked"
    assert refusal.reason_codes == ("premature_refusal_requires_alternative",)
    assert purity.status == "blocked"
    assert purity.reason_codes == ("private_output_purity_violation",)
    assert language.status == "blocked"
    assert language.reason_codes == ("response_language_mismatch",)


def test_debug_checkpoint_and_discipline_prompt_block_are_metadata_only() -> None:
    boundary = DisciplineBoundary(DisciplineBoundaryConfig(enabled=True))

    checkpoint = boundary.evaluate(
        _request(
            "debug_checkpoint",
            output_text="Investigated fixture failure",
            evidence_refs=("snapshot:turn-1",),
            metadata={"checkpointId": "debug:checkpoint-1"},
        )
    )
    prompt_block = boundary.evaluate(
        _request(
            "discipline_prompt_block",
            output_text="Follow verification discipline",
            metadata={"rawPrompt": "hidden_reasoning sk-discipline-secret"},
        )
    )
    encoded = str(prompt_block.public_projection())

    assert checkpoint.status == "checkpoint_recorded"
    assert checkpoint.public_projection()["checkpointRef"] == "debug:checkpoint-1"
    assert prompt_block.status == "checkpoint_recorded"
    assert prompt_block.public_projection()["authorityFlags"]["promptInjected"] is False
    assert "rawPrompt" not in encoded
    assert "sk-discipline-secret" not in encoded


def test_discipline_boundary_rejects_forged_private_refs_and_redacts_projection() -> None:
    boundary = DisciplineBoundary(DisciplineBoundaryConfig(enabled=True))

    decision = boundary.evaluate(
        _request(
            "debug_checkpoint",
            output_text="raw_child_output /Users/kevin/private ghp_disciplineSecret",
            evidence_refs=("evidence:/Users/kevin/raw",),
            metadata={"token": "ghp_disciplineSecret"},
        )
    )
    encoded = str(decision.public_projection())

    assert decision.status == "blocked"
    assert decision.reason_codes == ("private_debug_payload_blocked",)
    assert "raw_child_output" not in encoded
    assert "/Users/kevin" not in encoded
    assert "ghp_disciplineSecret" not in encoded


def test_discipline_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.harness.discipline_boundary")
forbidden = (
    "google.adk.runners",
    "google.adk.evals",
    "requests",
    "httpx",
    "subprocess",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
