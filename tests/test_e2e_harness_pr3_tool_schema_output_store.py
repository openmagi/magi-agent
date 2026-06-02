from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


def _manifest(
    *,
    input_schema: dict[str, object] | None = None,
    budget: Budget | None = None,
) -> ToolManifest:
    return ToolManifest(
        name="LocalFakeTool",
        description="Local fake test tool",
        kind="native",
        source=ToolSource(kind="native-plugin", package="test"),
        permission="read",
        inputSchema=input_schema
        or {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        timeoutMs=1000,
        budget=budget or Budget(outputChars=80, transcriptChars=40),
        enabled_by_default=True,
    )


def _context() -> ToolContext:
    return ToolContext(
        botId="bot-pr3",
        userId="user-pr3",
        sessionId="session-pr3",
        sessionKey="context-ref-pr3",
        turnId="turn-pr3",
        workspaceRoot="local-workspace-root",
    )


def test_schema_validation_accepts_adk_function_tool_object_schema() -> None:
    from magi_agent.tools.schema_validation import validate_tool_arguments

    decision = validate_tool_arguments(_manifest(), {"query": "hello", "limit": 3})

    assert decision.valid is True
    assert decision.reason_codes == ()
    assert decision.diagnostic_metadata["schemaType"] == "object"


def test_schema_validation_blocks_missing_extra_and_sensitive_values_without_raw_leakage() -> None:
    from magi_agent.tools.schema_validation import validate_tool_arguments

    missing = validate_tool_arguments(_manifest(), {"limit": 1})
    extra = validate_tool_arguments(
        _manifest(),
        {
            "query": "ok",
            "rawPrompt": "synthetic raw prompt marker",
            "unsafePath": "synthetic-private-location",
        },
    )
    dumped = f"{missing.public_projection()} {extra.public_projection()}"

    assert missing.valid is False
    assert "schema_required_field_missing" in missing.reason_codes
    assert extra.valid is False
    assert "schema_additional_property_blocked" in extra.reason_codes
    assert "query" not in dumped
    assert "synthetic raw prompt" not in dumped
    assert "synthetic-private-location" not in dumped
    assert "rawPrompt" not in dumped
    assert "unsafePath" not in dumped


def test_tool_kernel_validates_schema_before_fake_handler_execution() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    class FakeExecutor:
        openmagi_local_fake_provider = True

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def execute_tool(
            self,
            *,
            tool_name: str,
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            _ = tool_name, context
            self.calls.append(arguments)
            return ToolResult(status="ok", output="called")

    registry = ToolRegistry()
    registry.register(_manifest())
    executor = FakeExecutor()

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
            ),
            local_fake_executor=executor,
        ).execute(
            ToolExecutionRequest(
                toolName="LocalFakeTool",
                arguments={
                    "query": "safe",
                    "rawPrompt": "synthetic raw prompt marker",
                    "unsafePath": "synthetic-private-location",
                },
                context=_context(),
                mode="act",
                exposedToolNames=("LocalFakeTool",),
            )
        )
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert outcome.status == "blocked"
    assert outcome.reason_code == "tool_input_schema_invalid"
    assert outcome.handler_called is False
    assert outcome.executed is False
    assert executor.calls == []
    assert "schema_additional_property_blocked" in dumped
    assert "synthetic raw prompt" not in dumped
    assert "synthetic-private-location" not in dumped
    assert "rawPrompt" not in dumped
    assert "unsafePath" not in dumped


def test_output_budget_separates_full_result_previews_counts_and_digest_refs() -> None:
    from magi_agent.tools.output_budget import budget_tool_result

    result = ToolResult(
        status="ok",
        output={"rows": ["alpha", "beta", "gamma"], "body": "x" * 160},
        llmOutput="L" * 120,
        transcriptOutput="T" * 90,
        metadata={"safeCount": 3, "rawPrompt": "synthetic raw prompt marker"},
    )

    budgeted = budget_tool_result(
        result,
        llm_preview_chars=32,
        transcript_preview_chars=20,
    )
    projection = budgeted.public_projection()
    dumped = str(projection)

    assert budgeted.raw_result.output == result.output
    assert projection["status"] == "ok"
    assert projection["llmPreview"] == "L" * 32
    assert projection["transcriptPreview"] == "T" * 20
    assert projection["counts"]["rawBytes"] > projection["counts"]["llmPreviewBytes"]
    assert projection["truncation"]["llmPreviewTruncated"] is True
    assert projection["truncation"]["transcriptPreviewTruncated"] is True
    assert projection["resultRef"].startswith("result:sha256:")
    assert projection["digest"].startswith("sha256:")
    assert "synthetic raw prompt" not in dumped
    assert "rawPrompt" not in dumped


def test_budgeted_result_model_dump_and_repr_do_not_leak_raw_payload() -> None:
    from magi_agent.tools.output_budget import budget_tool_result

    result = ToolResult(
        status="ok",
        output="raw tool output marker: sensitive body",
        llmOutput="safe preview",
        transcriptOutput="safe transcript",
        metadata={"safe": "yes"},
    )

    budgeted = budget_tool_result(result, llm_preview_chars=20, transcript_preview_chars=20)
    dumped = str(budgeted.model_dump(by_alias=True, mode="json"))
    rendered = str(budgeted)

    for forbidden in (
        "sensitive body",
        "raw tool output",
    ):
        assert forbidden not in dumped
        assert forbidden not in rendered
    assert budgeted.raw_result.output == result.output
    assert budgeted.model_dump(by_alias=True, mode="json")["rawResult"]["storedOutOfBand"] is True


def test_local_result_store_is_in_memory_content_addressed_and_metadata_safe() -> None:
    from magi_agent.artifacts.local_result_store import (
        LocalResultStore,
        LocalResultStoreConfig,
    )
    from magi_agent.tools.output_budget import budget_tool_result

    store = LocalResultStore(LocalResultStoreConfig(enabled=True, localFakeStoreEnabled=True))
    budgeted = budget_tool_result(
        ToolResult(status="ok", output="result body", metadata={"safe": "yes"}),
        llm_preview_chars=20,
        transcript_preview_chars=20,
    )

    receipt = store.put_tool_result(
        budgeted,
        metadata={
            "purpose": "test",
            "privatePath": "synthetic-private-location",
            "rawPrompt": "synthetic raw prompt marker",
        },
    )
    fetched = store.get(receipt.ref)
    dumped = str(receipt.public_projection())

    assert receipt.status == "stored_local_fake"
    assert receipt.ref.startswith("result:sha256:")
    assert receipt.content_digest == budgeted.digest
    assert fetched is not None
    assert fetched.blob == budgeted.raw_blob
    assert store.production_write_count == 0
    assert set(receipt.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert "purpose" in dumped
    assert "synthetic-private-location" not in dumped
    assert "synthetic raw prompt" not in dumped
    assert "rawPrompt" not in dumped
    assert "privatePath" not in dumped


def test_local_result_store_default_off_does_not_store_or_write() -> None:
    from magi_agent.artifacts.local_result_store import LocalResultStore
    from magi_agent.tools.output_budget import budget_tool_result

    store = LocalResultStore()
    budgeted = budget_tool_result(ToolResult(status="ok", output="result body"))

    receipt = store.put_tool_result(budgeted)

    assert receipt.status == "disabled"
    assert receipt.ref.startswith("result:sha256:")
    assert store.get(receipt.ref) is None
    assert store.production_write_count == 0
    assert set(receipt.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_pr3_local_loop_validates_budgets_stores_and_projects_without_live_authority() -> None:
    from magi_agent.artifacts.local_result_store import (
        LocalResultStore,
        LocalResultStoreConfig,
    )
    from magi_agent.tools.output_budget import budget_tool_result
    from magi_agent.tools.schema_validation import validate_tool_arguments

    manifest = _manifest(budget=Budget(outputChars=48, transcriptChars=24))
    arguments = {"query": "budget me", "limit": 2}
    validation = validate_tool_arguments(manifest, arguments)
    assert validation.valid is True

    fake_result = ToolResult(
        status="ok",
        output={"items": ["one", "two"], "raw": "R" * 120},
        llmOutput="L" * 100,
        transcriptOutput="T" * 80,
    )
    budgeted = budget_tool_result(fake_result, budget=manifest.budget)
    receipt = LocalResultStore(
        LocalResultStoreConfig(enabled=True, localFakeStoreEnabled=True),
    ).put_tool_result(budgeted, metadata={"toolName": manifest.name})
    projection = budgeted.public_projection(
        store_receipt=receipt,
        validation_decision=validation,
    )
    dumped = str(projection)

    assert projection["validation"]["valid"] is True
    assert projection["llmPreview"] == "L" * 48
    assert projection["transcriptPreview"] == "T" * 24
    assert projection["storeRef"] == receipt.ref
    assert projection["authorityFlags"]["productionStorageWritten"] is False
    assert projection["authorityFlags"]["adkArtifactServiceAttached"] is False
    assert "RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR" not in dumped


def test_budget_projection_does_not_trust_forged_store_receipt_authority() -> None:
    from magi_agent.tools.output_budget import budget_tool_result

    class ForgedReceipt:
        def public_projection(self) -> dict[str, object]:
            return {
                "ref": "raw-tool-output/private-location",
                "authorityFlags": {
                    "productionStorageWritten": True,
                    "adkArtifactServiceAttached": True,
                    "liveAttachmentEnabled": True,
                    "userVisibleOutputAllowed": True,
                },
            }

    budgeted = budget_tool_result(ToolResult(status="ok", output="safe"))

    projection = budgeted.public_projection(store_receipt=ForgedReceipt())
    dumped = str(projection)

    assert projection["storeRef"].startswith("result:")
    assert set(projection["authorityFlags"].values()) == {False}
    assert "raw-tool-output" not in dumped
    assert "private-location" not in dumped


def test_pr3_modules_import_without_live_adk_runner_or_network_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

before = set(sys.modules)
modules = (
    "magi_agent.tools.schema_validation",
    "magi_agent.tools.output_budget",
    "magi_agent.artifacts.local_result_store",
)
for module in modules:
    imported = importlib.import_module(module)
    assert imported is not None

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.models",
    "google.adk.Runner",
    "fastapi",
    "uvicorn",
    "supabase",
    "psycopg",
    "asyncpg",
    "kubernetes",
    "httpx",
    "requests",
    "socket",
)
loaded = [
    name
    for name in set(sys.modules) - before
    if name in forbidden_exact
    or any(name.startswith(f"{prefix}.") for prefix in forbidden_exact)
]
if loaded:
    raise AssertionError(f"PR3 modules loaded forbidden modules: {loaded}")

from magi_agent.artifacts.local_result_store import ResultStoreAuthorityFlags

flags = ResultStoreAuthorityFlags.model_validate(
    {
        "adkArtifactServiceAttached": True,
        "productionStorageWritten": True,
        "liveAttachmentEnabled": True,
    }
)
if any(flags.model_dump(by_alias=True).values()):
    raise AssertionError("authority flags must remain false")
""",
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
