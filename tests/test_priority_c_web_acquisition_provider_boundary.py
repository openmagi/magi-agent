from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path


class FakeWebProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search(self, request: object) -> dict[str, object]:
        self.calls.append("search")
        return {
            "results": [
                {
                    "title": "Current source",
                    "url": "https://docs.example.com/current?utm=1",
                    "snippet": "Visible source summary.",
                    "metadata": {
                        "quality": 0.91,
                        "rawUrl": "https://signed.example.com/object?token=unsafe",
                        "providerLog": "Authorization: Bearer unsafe",
                    },
                }
            ],
            "preview": "Visible source summary with sk-unsafe-secret",
        }

    async def fetch(self, request: object) -> dict[str, object]:
        self.calls.append("fetch")
        return {
            "url": "https://docs.example.com/current",
            "title": "Docs",
            "content": (
                "Rendered public content.\n"
                "raw_tool_log: Cookie: session=unsafe\n"
                "/Users/kevin/private/source.txt"
            ),
            "metadata": {"jsonLdType": "Article", "cookie": "session=unsafe"},
        }

    async def extract(self, request: object) -> dict[str, object]:
        self.calls.append("extract")
        return {
            "url": "https://docs.example.com/current",
            "content": "Reader extracted content.",
            "metadata": {"reader": "jina-style"},
        }

    async def metadata(self, request: object) -> dict[str, object]:
        self.calls.append("metadata")
        return {
            "url": "https://docs.example.com/current",
            "content": '{"@type":"Article"}',
            "metadata": {"jsonLdType": "Article"},
        }

    async def acquire(self, request: object) -> dict[str, object]:
        self.calls.append("acquire")
        return {
            "url": "https://docs.example.com/current",
            "content": "Acquired via fallback.",
            "metadata": {"strategy": "fetch-reader-browser"},
        }


def test_web_acquisition_defaults_off_and_never_calls_provider() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        LocalWebAcquisitionRuntime,
        WebAcquisitionConfig,
        WebAcquisitionRequest,
    )

    provider = FakeWebProvider()
    runtime = LocalWebAcquisitionRuntime(WebAcquisitionConfig(), provider=provider)

    result = asyncio.run(
        runtime.run(WebAcquisitionRequest(operation="web.search", query=" latest model "))
    )

    assert result.status == "disabled"
    assert result.error_code == "web_acquisition_disabled"
    assert provider.calls == []
    assert result.diagnostic_metadata["productionNetworkEnabled"] is False
    assert result.diagnostic_metadata["productionWritesEnabled"] is False


def test_web_search_fake_provider_records_sanitized_source_refs_and_budget_metadata() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        LocalWebAcquisitionRuntime,
        WebAcquisitionConfig,
        WebAcquisitionRequest,
    )

    provider = FakeWebProvider()
    runtime = LocalWebAcquisitionRuntime(
        WebAcquisitionConfig(enabled=True, local_fake_provider_enabled=True, max_results=3),
        provider=provider,
    )

    result = asyncio.run(
        runtime.run(
            WebAcquisitionRequest(
                operation="web.search",
                query="  current   source   ",
                metadata={"budget": "local"},
            )
        )
    )
    public_projection = result.public_projection()
    encoded = json.dumps(public_projection, sort_keys=True)

    assert provider.calls == ["search"]
    assert result.status == "ok"
    assert result.records[0].source_ref == "source:web:src_1"
    assert result.records[0].evidence_ref == "evidence:web:src_1"
    assert result.records[0].proof_type == "observed"
    assert result.records[0].content_digest.startswith("sha256:")
    assert public_projection["parentOutputRefs"] == ["source:web:src_1", "evidence:web:src_1"]
    assert "Visible source summary" in encoded
    assert "sk-unsafe-secret" not in encoded
    assert "token=unsafe" not in encoded
    assert "Authorization" not in encoded
    assert public_projection["attachmentFlags"]["networkFetched"] is False
    assert public_projection["attachmentFlags"]["liveToolDispatched"] is False


def test_web_fetch_reader_and_metadata_paths_use_fake_provider_only_and_strip_raw_payloads() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        LocalWebAcquisitionRuntime,
        WebAcquisitionConfig,
        WebAcquisitionRequest,
    )

    provider = FakeWebProvider()
    runtime = LocalWebAcquisitionRuntime(
        WebAcquisitionConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    fetch = asyncio.run(
        runtime.run(
            WebAcquisitionRequest(operation="web.fetch", url="https://docs.example.com/current")
        )
    )
    reader = asyncio.run(
        runtime.run(
            WebAcquisitionRequest(
                operation="reader.extract",
                url="https://docs.example.com/current",
            )
        )
    )
    metadata = asyncio.run(
        runtime.run(
            WebAcquisitionRequest(
                operation="metadata.jsonld",
                url="https://docs.example.com/current",
            )
        )
    )
    encoded = json.dumps(
        [
            fetch.public_projection(),
            reader.public_projection(),
            metadata.public_projection(),
        ],
        sort_keys=True,
    )

    assert provider.calls == ["fetch", "extract", "metadata"]
    assert fetch.records[0].proof_type == "opened"
    assert reader.records[0].method == "reader.extract"
    assert metadata.records[0].method == "metadata.jsonld"
    assert "Rendered public content" in encoded
    assert "raw_tool_log" not in encoded
    assert "Cookie:" not in encoded
    assert "/Users/kevin" not in encoded
    assert "session=unsafe" not in encoded


def test_web_acquisition_blocks_private_auth_captcha_and_cluster_urls_before_provider_calls() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        LocalWebAcquisitionRuntime,
        WebAcquisitionConfig,
        WebAcquisitionRequest,
    )

    provider = FakeWebProvider()
    runtime = LocalWebAcquisitionRuntime(
        WebAcquisitionConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    blocked = [
        ("http://localhost:3000", "local_url_blocked"),
        ("http://localhost.:3000", "local_url_blocked"),
        ("http://2130706433:3000", "local_url_blocked"),
        ("http://0177.0.0.1:3000", "local_url_blocked"),
        ("http://host.docker.internal:3000", "local_url_blocked"),
        ("http://169.254.169.254/latest/meta-data", "metadata_url_blocked"),
        ("http://browser-worker:3003/health", "cluster_url_blocked"),
        ("https://kubernetes.default.svc/api", "cluster_url_blocked"),
        ("https://kubernetes.default.svc./api", "cluster_url_blocked"),
        ("https://user:pass@example.com/private", "auth_bypass_blocked"),
        ("https://docs.example.com/page?token=unsafe", "credential_url_blocked"),
        ("https://docs.example.com/page?X-Amz-Signature=unsafe", "credential_url_blocked"),
        ("https://docs.example.com/page?AWSAccessKeyId=unsafe", "credential_url_blocked"),
        ("https://docs.example.com/page?GoogleAccessId=unsafe", "credential_url_blocked"),
        ("https://docs.example.com/captcha", "captcha_flow_blocked"),
    ]

    for url, error_code in blocked:
        result = asyncio.run(
            runtime.run(WebAcquisitionRequest(operation="web.fetch", url=url))
        )
        assert result.status == "blocked"
        assert result.error_code == error_code

    assert provider.calls == []


def test_provider_returned_blocked_urls_are_not_recorded_raw_or_projected() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        LocalWebAcquisitionRuntime,
        WebAcquisitionConfig,
        WebAcquisitionRequest,
    )

    class HostileProvider:
        openmagi_local_fake_provider = True

        async def search(self, request: object) -> dict[str, object]:
            return {
                "results": [
                    {
                        "title": "Storage object",
                        "url": "https://storage.googleapis.com/private-bucket/object",
                        "snippet": "Public summary includes https://docs.example.com/?token=unsafe",
                        "metadata": {"providerLog": "raw signed url"},
                    }
                ],
                "preview": "Preview s3://private-bucket/object?X-Amz-Signature=unsafe",
            }

    runtime = LocalWebAcquisitionRuntime(
        WebAcquisitionConfig(enabled=True, local_fake_provider_enabled=True),
        provider=HostileProvider(),
    )

    result = asyncio.run(
        runtime.run(WebAcquisitionRequest(operation="web.search", query="storage object"))
    )
    encoded = json.dumps(result.public_projection(), sort_keys=True)

    assert result.records[0].url.startswith("blocked-source:")
    assert "storage.googleapis.com" not in encoded
    assert "private-bucket" not in encoded
    assert "s3://private-bucket" not in encoded
    assert "X-Amz-Signature" not in encoded
    assert "token=unsafe" not in encoded
    assert "providerLog" not in encoded


def test_web_acquisition_rejects_unmarked_local_fake_provider() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        LocalWebAcquisitionRuntime,
        WebAcquisitionConfig,
        WebAcquisitionRequest,
    )

    class UnmarkedProvider(FakeWebProvider):
        openmagi_local_fake_provider = False

    provider = UnmarkedProvider()
    runtime = LocalWebAcquisitionRuntime(
        WebAcquisitionConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    result = asyncio.run(
        runtime.run(WebAcquisitionRequest(operation="web.search", query="safe query"))
    )

    assert result.status == "blocked"
    assert result.error_code == "local_fake_provider_untrusted"
    assert provider.calls == []


def test_public_projection_redacts_diagnostic_metadata_and_provider_log_aliases() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        WebAcquisitionResult,
    )

    result = WebAcquisitionResult(
        status="ok",
        operation="web.search",
        diagnosticMetadata={
            "providerLog": "raw provider log",
            "debugTrace": "trace data",
            "trusted": True,
            "authoritative": True,
            "safeBudget": 100,
            "url": "https://docs.example.com/page?token=unsafe",
        },
    )

    encoded = json.dumps(result.public_projection(), sort_keys=True)

    assert "providerLog" not in encoded
    assert "debugTrace" not in encoded
    assert "raw provider log" not in encoded
    assert "trace data" not in encoded
    assert "trusted" not in encoded
    assert "authoritative" not in encoded
    assert "token=unsafe" not in encoded
    assert "safeBudget" in encoded


def test_public_projection_redacts_forged_preview_records_and_parent_refs() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        WebAcquisitionResult,
        WebAcquisitionSourceRecord,
    )

    record = WebAcquisitionSourceRecord.model_construct(
        source_ref="source:/Users/kevin/private",
        evidence_ref="evidence:Authorization: Bearer unsafe-token",
        method="web.fetch",
        provider="provider sk-web-secret",
        url="https://docs.example.com/private?token=unsafe",
        normalized_url="https://docs.example.com/private?token=unsafe",
        content_digest="/Users/kevin/raw",
        proof_type="opened",
        title="raw_tool_log /Users/kevin/private",
        metadata={"routeAttached": True, "note": "safe"},
    )
    result = WebAcquisitionResult(
        status="ok",
        operation="web.fetch",
        records=(record,),
        publicPreview="Authorization: Bearer unsafe-token\nraw_content /Users/kevin/private",
        diagnosticMetadata={"productionNetworkEnabled": True, "safeBudget": 1},
    )

    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["parentOutputRefs"][0].startswith("source:")
    assert projection["parentOutputRefs"][1].startswith("evidence:")
    assert "/Users/kevin" not in encoded
    assert "Authorization" not in encoded
    assert "unsafe-token" not in encoded
    assert "sk-web-secret" not in encoded
    assert "routeAttached" not in encoded
    assert "productionNetworkEnabled" not in encoded
    assert projection["diagnosticMetadata"]["safeBudget"] == 1


def test_browser_fallback_requires_approval_before_fake_acquire_provider_call() -> None:
    from magi_agent.web_acquisition.provider_boundary import (
        LocalWebAcquisitionRuntime,
        WebAcquisitionConfig,
        WebAcquisitionRequest,
    )

    provider = FakeWebProvider()
    runtime = LocalWebAcquisitionRuntime(
        WebAcquisitionConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    blocked = asyncio.run(
        runtime.run(
            WebAcquisitionRequest(
                operation="web.acquire",
                url="https://docs.example.com/current",
                allow_browser_fallback=True,
            )
        )
    )
    allowed = asyncio.run(
        runtime.run(
            WebAcquisitionRequest(
                operation="web.acquire",
                url="https://docs.example.com/current",
                allow_browser_fallback=True,
                approval_granted=True,
            )
        )
    )

    assert blocked.status == "approval_required"
    assert blocked.error_code == "browser_fallback_requires_approval"
    assert provider.calls == ["acquire"]
    assert allowed.status == "ok"
    assert allowed.records[0].source_ref == "source:web:src_1"


def test_web_acquisition_import_boundary_has_no_live_network_or_runtime_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "web_acquisition"
        / "provider_boundary.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "google.adk.runners",
        "magi_agent.adk_bridge",
        "magi_agent.tools",
        "magi_agent.transport",
        "socket",
        "subprocess",
        "httpx",
        "requests",
        "aiohttp",
        "selenium",
        "playwright",
    )

    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
    for fragment in ("__import__(", "importlib.import_module", "requests.get", "httpx."):
        assert fragment not in source
