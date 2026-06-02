from openmagi_core_agent.tools import ToolResult


def test_tool_result_accepts_prd_aliases_and_dumps_prd_wire_shape() -> None:
    result = ToolResult(
        status="ok",
        output={"raw": "value"},
        llmOutput={"summary": "visible to llm"},
        transcriptOutput={"summary": "visible in transcript"},
        durationMs=123,
        artifactRefs=("artifact-1",),
        fileRefs=("workspace/report.md",),
        deliveryReceipts=("receipt-1",),
        retryable=True,
        metadata={"toolName": "ExampleTool"},
    )

    assert result.llm_output == {"summary": "visible to llm"}
    assert result.transcript_output == {"summary": "visible in transcript"}
    assert result.duration_ms == 123
    assert result.artifact_refs == ("artifact-1",)
    assert result.file_refs == ("workspace/report.md",)
    assert result.delivery_receipts == ("receipt-1",)

    assert result.model_dump(by_alias=True) == {
        "status": "ok",
        "output": {"raw": "value"},
        "llmOutput": {"summary": "visible to llm"},
        "transcriptOutput": {"summary": "visible in transcript"},
        "errorCode": None,
        "errorMessage": None,
        "durationMs": 123,
        "artifactRefs": ("artifact-1",),
        "fileRefs": ("workspace/report.md",),
        "deliveryReceipts": ("receipt-1",),
        "retryable": True,
        "metadata": {"toolName": "ExampleTool"},
        "codingMutationReceipt": None,
    }


def test_tool_result_keeps_existing_python_field_names_as_compatibility_shims() -> None:
    result = ToolResult(
        status="error",
        llm={"summary": "legacy llm"},
        transcript={"summary": "legacy transcript"},
        error="legacy error message",
        artifact_refs=("artifact-1",),
        file_refs=("workspace/report.md",),
        delivery_receipts=("receipt-1",),
    )

    assert result.llm == {"summary": "legacy llm"}
    assert result.transcript == {"summary": "legacy transcript"}
    assert result.error == "legacy error message"
    assert result.llm_output == {"summary": "legacy llm"}
    assert result.transcript_output == {"summary": "legacy transcript"}
    assert result.error_message == "legacy error message"
    assert result.model_dump(by_alias=True)["llmOutput"] == {"summary": "legacy llm"}
    assert result.model_dump(by_alias=True)["transcriptOutput"] == {
        "summary": "legacy transcript"
    }
    assert result.model_dump(by_alias=True)["errorMessage"] == "legacy error message"
