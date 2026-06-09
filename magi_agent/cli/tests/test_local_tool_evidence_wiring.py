from __future__ import annotations

import asyncio

from magi_agent.cli.tool_runtime import wrap_cli_adk_tools_with_evidence_collector
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus


class _FakeAdkTool:
    name = "GitDiff"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.func = self._func

    async def _func(
        self,
        arguments: dict[str, object],
        tool_context: object,
    ) -> dict[str, object]:
        self.calls.append(arguments)
        return {
            "status": "ok",
            "output": {"digest": "sha256:" + "a" * 64},
            "metadata": {
                "toolName": "GitDiff",
                "toolCallId": "call-diff",
                "evidenceRefs": ["evidence:git-diff"],
                "validatorRefs": ["verifier:dev-coding:test-evidence"],
                "toolExecutionReceipt": {
                    "receiptId": "receipt:local-git-diff",
                    "toolName": "GitDiff",
                    "status": "success",
                },
            },
        }


class _FakeInvocationContext:
    invocation_id = "turn-1"

    function_call = {"id": "call-diff", "name": "GitDiff"}


def test_cli_tool_wrapper_records_tool_result_for_engine_collector() -> None:
    collector = LocalToolEvidenceCollector()
    tool = _FakeAdkTool()

    wrapped = wrap_cli_adk_tools_with_evidence_collector(
        [tool],
        collector=collector,
        session_id="session-1",
    )

    result = asyncio.run(
        wrapped[0].func({"diffRef": "fixture-1"}, _FakeInvocationContext())
    )
    bus = execute_pre_final_verifier_bus(
        required_evidence=("evidence:git-diff",),
        required_validators=("verifier:dev-coding:test-evidence",),
        observed_public_refs=(),
        evidence_records=collector.collect_for_turn("turn-1"),
    )

    assert result["status"] == "ok"
    assert tool.calls == [{"diffRef": "fixture-1"}]
    assert bus["decision"] == "pass"
