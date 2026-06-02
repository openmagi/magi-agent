from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from openmagi_core_agent.artifacts.output_registry_boundary import (
    OutputArtifactRegistryBoundary,
    OutputArtifactRegistryConfig,
    OutputArtifactRegistryRequest,
)


class FakeOutputRegistryProvider:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        *,
        status: str = "ok",
        provider_ref: str = "artifact:provider-1",
        fail: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.status = status
        self.provider_ref = provider_ref
        self.fail = fail

    async def execute(self, request: OutputArtifactRegistryRequest) -> dict[str, object]:
        self.calls.append(request.operation)
        if self.fail:
            raise RuntimeError("raw_child_transcript /Users/kevin/private sk-artifact-secret")
        return {
            "status": self.status,
            "artifactRef": self.provider_ref,
            "providerRecordId": "provider-record-1",
        }


def _request(
    operation: str,
    *,
    artifact_id: str | None = "artifact-1",
    filename: str | None = "report.md",
    title: str | None = "Quarterly report",
    output_format: str | None = "markdown",
    child_artifact_ref: str | None = None,
    metadata: dict[str, object] | None = None,
) -> OutputArtifactRegistryRequest:
    return OutputArtifactRegistryRequest(
        operation=operation,
        requestId="req-1",
        sessionId="session-1",
        turnId="turn-1",
        artifactId=artifact_id,
        title=title,
        filename=filename,
        format=output_format,
        contentDigest="sha256:" + ("a" * 64),
        childArtifactRef=child_artifact_ref,
        metadata=metadata or {},
    )


def test_output_artifact_registry_is_disabled_by_default() -> None:
    provider = FakeOutputRegistryProvider()
    decision = asyncio.run(
        OutputArtifactRegistryBoundary(OutputArtifactRegistryConfig()).execute(
            _request("artifact.create"),
            provider=provider,
        )
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("output_artifact_registry_disabled",)
    assert provider.calls == []
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


@pytest.mark.parametrize(
    "operation",
    (
        "artifact.create",
        "artifact.read",
        "artifact.list",
        "artifact.update",
        "artifact.delete",
    ),
)
def test_output_artifact_lifecycle_records_local_fake_intents_without_storage_authority(
    operation: str,
) -> None:
    provider = FakeOutputRegistryProvider()
    boundary = OutputArtifactRegistryBoundary(
        OutputArtifactRegistryConfig(enabled=True, localFakeRegistryEnabled=True),
    )

    decision = asyncio.run(boundary.execute(_request(operation), provider=provider))
    projection = decision.public_projection()

    assert decision.status == "recorded_local_fake"
    assert projection["record"]["generatedOutputPathPreview"].startswith("outputs/session-")
    assert "/Users/" not in str(projection)
    assert projection["authorityFlags"]["productionStorageWritten"] is False
    assert projection["authorityFlags"]["adkArtifactServiceAttached"] is False
    assert provider.calls == [operation]


@pytest.mark.parametrize(
    "output_format,filename",
    (
        ("markdown", "report.md"),
        ("txt", "notes.txt"),
        ("html", "page.html"),
        ("pdf", "report.pdf"),
        ("docx", "brief.docx"),
        ("hwpx", "brief.hwpx"),
        ("xlsx", "sheet.xlsx"),
        ("csv", "sheet.csv"),
        ("tsv", "sheet.tsv"),
    ),
)
def test_output_artifact_registry_supports_document_spreadsheet_format_matrix(
    output_format: str,
    filename: str,
) -> None:
    boundary = OutputArtifactRegistryBoundary(
        OutputArtifactRegistryConfig(enabled=True, localFakeRegistryEnabled=True),
    )

    decision = asyncio.run(
        boundary.execute(
            _request("artifact.create", filename=filename, output_format=output_format),
            provider=FakeOutputRegistryProvider(),
        )
    )

    assert decision.status == "recorded_local_fake"
    assert decision.record is not None
    assert decision.record.format == output_format
    assert decision.record.filename == filename


def test_child_artifact_import_rekeys_collision_and_redacts_child_payload() -> None:
    boundary = OutputArtifactRegistryBoundary(
        OutputArtifactRegistryConfig(enabled=True, localFakeRegistryEnabled=True),
    )

    decision = asyncio.run(
        boundary.execute(
            _request(
                "artifact.import_child",
                filename="child-summary.md",
                child_artifact_ref="child-envelope:abc123",
                metadata={
                    "collisionPolicy": "rekey",
                    "rawChildTranscript": "hidden_reasoning sk-artifact-secret",
                },
            ),
            provider=FakeOutputRegistryProvider(provider_ref="provider:/Users/kevin/raw"),
        )
    )
    projection = decision.public_projection()
    encoded = str(projection)

    assert decision.status == "recorded_local_fake"
    assert projection["record"]["provenanceRefs"]
    assert "child_raw_prompt" not in encoded
    assert "rawChildTranscript" not in encoded
    assert "/Users/kevin" not in encoded
    assert "sk-artifact-secret" not in encoded
    assert projection["diagnosticMetadata"]["collisionPolicy"] == "rekey"


def test_output_artifact_registry_blocks_raw_paths_private_payloads_and_failed_ack() -> None:
    boundary = OutputArtifactRegistryBoundary(
        OutputArtifactRegistryConfig(enabled=True, localFakeRegistryEnabled=True),
    )

    raw_path = asyncio.run(
        boundary.execute(
            _request("artifact.create", filename="/Users/kevin/private/report.md"),
            provider=FakeOutputRegistryProvider(),
        )
    )
    private = asyncio.run(
        boundary.execute(
            _request(
                "artifact.create",
                title="raw_tool_log /Users/kevin/private ghp_artifactSecret",
            ),
            provider=FakeOutputRegistryProvider(),
        )
    )
    failed = asyncio.run(
        boundary.execute(
            _request("artifact.create"),
            provider=FakeOutputRegistryProvider(status="failed"),
        )
    )

    assert raw_path.status == "blocked"
    assert raw_path.reason_codes == ("unsafe_filename_blocked",)
    assert private.status == "blocked"
    assert private.reason_codes == ("private_artifact_payload_blocked",)
    assert failed.status == "blocked"
    assert failed.reason_codes == ("output_registry_ack_failed",)


def test_output_artifact_registry_rejects_unmarked_provider_and_sanitizes_provider_errors() -> None:
    class UnmarkedProvider(FakeOutputRegistryProvider):
        openmagi_local_fake_provider = False

    boundary = OutputArtifactRegistryBoundary(
        OutputArtifactRegistryConfig(enabled=True, localFakeRegistryEnabled=True),
    )

    untrusted = asyncio.run(
        boundary.execute(_request("artifact.create"), provider=UnmarkedProvider())
    )
    errored = asyncio.run(
        boundary.execute(
            _request("artifact.create"),
            provider=FakeOutputRegistryProvider(fail=True),
        )
    )
    encoded = str(errored.public_projection())

    assert untrusted.status == "blocked"
    assert untrusted.reason_codes == ("local_fake_registry_provider_untrusted",)
    assert errored.status == "error"
    assert "raw_child_transcript" not in encoded
    assert "/Users/kevin" not in encoded
    assert "sk-artifact-secret" not in encoded


def test_output_artifact_registry_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.artifacts.output_registry_boundary")
forbidden = (
    "google.adk.artifacts",
    "subprocess",
    "docx",
    "weasyprint",
    "requests",
    "httpx",
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
