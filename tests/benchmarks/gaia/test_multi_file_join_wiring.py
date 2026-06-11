"""GAIA bench-path wiring for the multi-file cross-reference lever.

The production CLI/serve path appends the gated block via build_cli_instruction.
The GAIA harness builds its OWN instruction and passes it as instruction= to
build_cli_model_runner, so build_cli_instruction is NEVER called on the GAIA
path. This test pins that the GAIA harness flag-gates and appends the SAME
domain-neutral block, so:
  - flag OFF -> harness instruction is byte-identical to pre-change,
  - flag ON  -> the SAME <multi_file_join> block the production path emits is
    appended, so the A/B plan measures the lever the flag actually exercises.

Run with:
    MAGI_CONFIG=$(mktemp) uv run --extra dev pytest \
        tests/benchmarks/gaia/test_multi_file_join_wiring.py -q
"""
from __future__ import annotations

import os
from unittest import mock

from benchmarks.gaia.answer import (
    GAIA_FORMAT_ADHERENCE_NOTE,
    GAIA_SYSTEM_PROMPT,
)
from benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.cli.tool_runtime import multi_file_join_block


def _capture_instruction(question: GaiaQuestion, env: dict[str, str]) -> str:
    from benchmarks.gaia import harness as harness_mod

    captured: dict[str, str] = {}

    def _fake_runner(config, *, instruction, **kwargs):  # noqa: ANN001
        captured["instruction"] = instruction

        class _Runner:
            async def run_async(self, **_kw):  # noqa: ANN003
                if False:  # pragma: no cover - empty async generator
                    yield None

        return _Runner()

    with mock.patch.object(harness_mod, "build_cli_model_runner", _fake_runner):
        with mock.patch.dict(os.environ, env, clear=True):
            harness_mod.run_gaia_question(
                question,
                workspace_root=os.getcwd(),
                model_factory=lambda cfg: object(),
            )
    return captured["instruction"]


def _question() -> GaiaQuestion:
    return GaiaQuestion(
        task_id="x", question="Which category?", level=3, final_answer=""
    )


def test_gaia_instruction_byte_identical_when_off() -> None:
    q = _question()
    off = _capture_instruction(q, {"MAGI_MULTI_FILE_JOIN_ENABLED": "0"})
    unset = _capture_instruction(q, {})
    assert "<multi_file_join>" not in off
    assert off == unset
    # The OFF instruction is exactly the GAIA baseline: the format-adherence note
    # is always advertised on the GAIA path; the multi_file_join flag adds nothing
    # when OFF. (All other gated blocks are off under the cleared env.)
    legacy = (
        f"{GAIA_SYSTEM_PROMPT}\n\n{GAIA_FORMAT_ADHERENCE_NOTE}"
        f"\n\nQUESTION:\n{q.question}"
    )
    assert off == legacy


def test_gaia_instruction_appends_same_block_when_on() -> None:
    q = _question()
    off = _capture_instruction(q, {})
    on = _capture_instruction(q, {"MAGI_MULTI_FILE_JOIN_ENABLED": "1"})
    assert "<multi_file_join>" in on
    block = multi_file_join_block({"MAGI_MULTI_FILE_JOIN_ENABLED": "1"})
    # Identical text to the production CLI path, appended after the GAIA prompt.
    assert on == off + "\n\n" + block
