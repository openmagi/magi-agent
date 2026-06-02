from __future__ import annotations

import asyncio
import subprocess
import sys

from openmagi_core_agent.knowledge.provider_boundary import (
    KnowledgeBoundary,
    KnowledgeBoundaryConfig,
    KnowledgeBoundaryDecision,
    KnowledgeBoundaryRequest,
    KnowledgeSourceRecord,
)


class FakeKnowledgeProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def execute(self, request: KnowledgeBoundaryRequest) -> dict[str, object]:
        self.calls.append(request.operation)
        if self.fail:
            raise RuntimeError("provider raw_tool_log /Users/kevin/private ghp_kbSecret")
        return {
            "records": [
                {
                    "sourceRef": "kb:policy-doc",
                    "title": "Policy",
                    "snippet": (
                        "Public KB summary.\n"
                        "raw_source: Authorization: Bearer unsafe\n"
                        "/Users/kevin/private/kb.md"
                    ),
                    "publicPreview": "Public KB summary.",
                    "metadata": {
                        "quality": 0.91,
                        "visibility": "public-safe",
                        "rawSource": "/workspace/private",
                        "note": "safe",
                    },
                }
            ]
        }


def test_knowledge_boundary_is_disabled_by_default() -> None:
    provider = FakeKnowledgeProvider()

    decision = asyncio.run(
        KnowledgeBoundary(KnowledgeBoundaryConfig()).execute(
            KnowledgeBoundaryRequest(operation="knowledge.search", query="policy"),
            provider=provider,
        )
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("knowledge_boundary_disabled",)
    assert provider.calls == []
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_knowledge_search_and_external_read_project_sanitized_source_ledger_records() -> None:
    provider = FakeKnowledgeProvider()
    boundary = KnowledgeBoundary(
        KnowledgeBoundaryConfig(enabled=True, localFakeProviderEnabled=True),
    )

    search = asyncio.run(
        boundary.execute(
            KnowledgeBoundaryRequest(operation="knowledge.search", query="policy"),
            provider=provider,
        )
    )
    external = asyncio.run(
        boundary.execute(
            KnowledgeBoundaryRequest(
                operation="external_source.read",
                sourceRef="external:repo-readme",
            ),
            provider=provider,
        )
    )
    encoded = str([search.public_projection(), external.public_projection()])

    assert provider.calls == ["knowledge.search", "external_source.read"]
    assert search.status == "ok"
    assert search.records[0].source_ref == "kb:policy-doc"
    assert search.records[0].evidence_ref == "evidence:knowledge:1"
    assert "Public KB summary" in encoded
    assert "raw_source" not in encoded
    assert "Authorization" not in encoded
    assert "/Users/kevin" not in encoded
    assert "/workspace/private" not in encoded
    assert "quality" in encoded
    assert "note" in encoded
    assert external.public_projection()["authorityFlags"]["externalSourceFetched"] is False


def test_knowledge_write_and_cache_require_scope_and_block_private_payloads() -> None:
    provider = FakeKnowledgeProvider()
    boundary = KnowledgeBoundary(
        KnowledgeBoundaryConfig(enabled=True, localFakeProviderEnabled=True),
    )

    no_scope = asyncio.run(
        boundary.execute(
            KnowledgeBoundaryRequest(operation="knowledge.write", content="safe"),
            provider=provider,
        )
    )
    private_payload = asyncio.run(
        boundary.execute(
            KnowledgeBoundaryRequest(
                operation="external_source.cache",
                sourceRef="external:doc",
                content="raw_tool_log: Cookie: session=unsafe",
                writeScopeApproved=True,
            ),
            provider=provider,
        )
    )
    allowed = asyncio.run(
        boundary.execute(
            KnowledgeBoundaryRequest(
                operation="knowledge.write",
                content="Durable note",
                sourceRef="kb:note",
                writeScopeApproved=True,
            ),
            provider=provider,
        )
    )

    assert no_scope.status == "blocked"
    assert no_scope.reason_codes == ("knowledge_write_scope_required",)
    assert private_payload.status == "blocked"
    assert private_payload.reason_codes == ("private_source_payload_blocked",)
    assert allowed.status == "ok"
    assert allowed.public_projection()["authorityFlags"]["knowledgeWritePerformed"] is False


def test_knowledge_boundary_blocks_private_external_refs_and_untrusted_provider() -> None:
    class UnmarkedProvider(FakeKnowledgeProvider):
        openmagi_local_fake_provider = False

    provider = UnmarkedProvider()
    boundary = KnowledgeBoundary(
        KnowledgeBoundaryConfig(enabled=True, localFakeProviderEnabled=True),
    )

    private_ref = asyncio.run(
        boundary.execute(
            KnowledgeBoundaryRequest(
                operation="external_source.read",
                sourceRef="s3://private-bucket/object?token=unsafe",
            ),
            provider=provider,
        )
    )
    untrusted = asyncio.run(
        boundary.execute(
            KnowledgeBoundaryRequest(operation="knowledge.search", query="policy"),
            provider=provider,
        )
    )

    assert private_ref.status == "blocked"
    assert private_ref.reason_codes == ("private_external_source_blocked",)
    assert untrusted.status == "blocked"
    assert untrusted.reason_codes == ("local_fake_knowledge_provider_untrusted",)
    assert provider.calls == []


def test_knowledge_boundary_provider_errors_are_sanitized_before_projection() -> None:
    decision = asyncio.run(
        KnowledgeBoundary(
            KnowledgeBoundaryConfig(enabled=True, localFakeProviderEnabled=True),
        ).execute(
            KnowledgeBoundaryRequest(operation="knowledge.search", query="policy"),
            provider=FakeKnowledgeProvider(fail=True),
        )
    )
    encoded = str(decision.public_projection())

    assert decision.status == "error"
    assert decision.reason_codes == ("local_fake_knowledge_provider_error",)
    assert "raw_tool_log" not in encoded
    assert "/Users/kevin" not in encoded
    assert "ghp_kbSecret" not in encoded


def test_knowledge_projection_sanitizes_forged_parent_refs_and_authority_metadata() -> None:
    record = KnowledgeSourceRecord.model_construct(
        source_ref="source:/Users/kevin/private",
        evidence_ref="evidence:Authorization: Bearer unsafe-token",
        operation="knowledge.search",
        provider="provider sk-kb-secret",
        content_digest="sha256:" + ("a" * 64),
        title="safe title",
        preview="Public preview",
        metadata={"routeAttached": True, "note": "safe"},
    )
    decision = KnowledgeBoundaryDecision(
        status="ok",
        operation="knowledge.search",
        records=(record,),
        diagnosticMetadata={
            "productionWritesEnabled": True,
            "routeAttached": True,
            "trusted": True,
            "authoritative": True,
            "note": "safe",
        },
    )

    projection = decision.public_projection()
    encoded = str(projection)
    diagnostic = str(projection["diagnosticMetadata"])

    assert projection["parentOutputRefs"][0].startswith("source:")
    assert projection["parentOutputRefs"][1].startswith("evidence:")
    assert "/Users/kevin" not in encoded
    assert "Authorization" not in encoded
    assert "unsafe-token" not in encoded
    assert "sk-kb-secret" not in encoded
    assert "productionWritesEnabled" not in diagnostic
    assert "routeAttached" not in diagnostic
    assert "trusted" not in diagnostic
    assert "authoritative" not in diagnostic
    assert projection["diagnosticMetadata"]["note"] == "safe"


def test_knowledge_record_validation_preserves_evidence_ref_namespace() -> None:
    record = KnowledgeSourceRecord(
        sourceRef="/Users/kevin/private-source",
        evidenceRef="/Users/kevin/private-evidence",
        operation="knowledge.search",
        provider="fake",
        contentDigest="sha256:" + ("a" * 64),
    )
    decision = KnowledgeBoundaryDecision(
        status="ok",
        operation="knowledge.search",
        records=(record,),
    )
    projection = decision.public_projection()

    assert projection["sourceRecords"][0]["sourceRef"].startswith("source:")
    assert projection["sourceRecords"][0]["evidenceRef"].startswith("evidence:")
    assert projection["parentOutputRefs"][0].startswith("source:")
    assert projection["parentOutputRefs"][1].startswith("evidence:")


def test_knowledge_public_projection_keeps_private_snippets_ref_only() -> None:
    record = KnowledgeSourceRecord(
        sourceRef="kb:customer-acme-secret-note",
        evidenceRef="evidence:knowledge:private",
        operation="knowledge.search",
        provider="fake",
        contentDigest="sha256:" + ("a" * 64),
        title="ACME breach plan",
        preview="internal roadmap sentence without regex secrets",
        metadata={"visibility": "private", "topic": "ACME breach plan"},
    )
    decision = KnowledgeBoundaryDecision(
        status="ok",
        operation="knowledge.search",
        records=(record,),
    )
    projection = decision.public_projection()
    encoded = str(projection)

    assert projection["sourceRecords"][0]["preview"] is None
    assert projection["sourceRecords"][0]["title"] is None
    assert projection["sourceRecords"][0]["sourceRef"] != "kb:customer-acme-secret-note"
    assert projection["sourceRecords"][0]["metadata"] == {"visibility": "private"}
    assert "internal roadmap sentence" not in encoded
    assert "ACME breach plan" not in encoded
    assert "kb:customer-acme-secret-note" not in encoded


def test_knowledge_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.knowledge.provider_boundary")
forbidden = (
    "google.adk.runners",
    "requests",
    "httpx",
    "socket",
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
