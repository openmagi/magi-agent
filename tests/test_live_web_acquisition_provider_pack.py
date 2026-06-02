from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


class FakeWebSearchProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, status: str = "ok") -> None:
        self.status = status
        self.calls: list[object] = []

    def search(self, request: object) -> dict[str, object]:
        self.calls.append(request)
        if self.status == "denied":
            return {"status": "denied", "reason": "rate limit"}
        if self.status == "timeout":
            return {"status": "timeout", "reason": "timeout"}
        return {
            "results": [
                {
                    "url": "https://docs.example.com/current?utm=1",
                    "title": "Current docs",
                    "snippet": "Search result summary.",
                }
            ]
        }


class FakeWebContentProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[object] = []

    def fetch(self, request: object) -> dict[str, object]:
        self.calls.append(request)
        return {
            "url": "https://docs.example.com/current",
            "title": "Docs",
            "content": "Opened source content.\nraw_tool_log Cookie: unsafe",
        }

    def reader(self, request: object) -> dict[str, object]:
        self.calls.append(request)
        return {
            "url": "https://docs.example.com/current",
            "content": "Reader content.",
            "metadata": {"reader": "jina-style", "rawResponse": "secret"},
        }

    def browser_fallback(self, request: object) -> dict[str, object]:
        self.calls.append(request)
        return {
            "url": "https://docs.example.com/current",
            "content": "Browser observed content.",
            "metadata": {"snapshotRef": "snapshot:abc", "rawSnapshot": "private"},
        }


def _config(**overrides: object) -> object:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderPackConfig,
    )

    payload = {
        "enabled": True,
        "localFakeProviderEnabled": True,
        "providerAllowlist": ("fake-web",),
    }
    payload.update(overrides)
    return WebAcquisitionProviderPackConfig(**payload)


def _request(**overrides: object) -> object:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderRequest,
    )

    payload = {
        "operation": "search",
        "requestId": "web-1",
        "providerName": "fake-web",
        "botIdDigest": "bot:abc",
        "ownerIdDigest": "owner:def",
        "sessionKeyDigest": "session:ghi",
        "query": "current docs",
    }
    payload.update(overrides)
    return WebAcquisitionProviderRequest(**payload)


def test_web_provider_pack_default_disabled_blocks_all_operations_without_provider_calls() -> None:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderPack,
        WebAcquisitionProviderPackConfig,
    )

    provider = FakeWebSearchProvider()
    pack = WebAcquisitionProviderPack(WebAcquisitionProviderPackConfig())

    for operation in ("search", "fetch", "reader", "browser_fallback"):
        result = pack.run(
            _request(operation=operation, url="https://docs.example.com/current"),
            provider=provider,
        )
        assert result.status == "disabled"
        assert result.source_records == ()

    assert provider.calls == []


def test_blank_search_query_returns_deterministic_disabled_or_blocked_result() -> None:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderPack,
        WebAcquisitionProviderPackConfig,
    )

    provider = FakeWebSearchProvider()
    disabled = WebAcquisitionProviderPack(WebAcquisitionProviderPackConfig()).run(
        _request(operation="search", query="   "),
        provider=provider,
    )
    blocked = WebAcquisitionProviderPack(_config()).run(
        _request(operation="search", query="   "),
        provider=provider,
    )

    assert disabled.status == "disabled"
    assert disabled.reason_codes == ("web_acquisition_provider_pack_disabled",)
    assert disabled.request_digest.startswith("sha256:")
    assert blocked.status == "blocked"
    assert blocked.reason_codes == ("query_required",)
    assert blocked.request_digest.startswith("sha256:")
    assert provider.calls == []


def test_fake_search_provider_returns_stable_source_records_and_receipts() -> None:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderPack,
    )

    provider = FakeWebSearchProvider()
    pack = WebAcquisitionProviderPack(_config())

    first = pack.run(_request(operation="search", query="current docs"), provider=provider)
    second = pack.run(_request(operation="search", query=" current   docs "), provider=provider)
    projection = first.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert first.status == "ok"
    assert first.source_records[0].source_ref == second.source_records[0].source_ref
    assert first.source_records[0].proof_type == "observed"
    assert first.source_records[0].provider_fetched_ref.startswith("provider-fetched:")
    assert first.source_records[0].model_seen_ref.startswith("model-saw:")
    assert projection["sourceRecords"][0]["url"] == "[redacted]"
    assert "utm=1" not in rendered
    assert "providerReceipt" in projection
    assert projection["authorityFlags"]["networkFetched"] is False


def test_fetch_reader_and_browser_fallback_record_opened_proof_without_raw_injection() -> None:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderPack,
    )

    provider = FakeWebContentProvider()
    pack = WebAcquisitionProviderPack(_config(browserFallbackEnabled=True))

    fetch = pack.run(
        _request(operation="fetch", url="https://docs.example.com/current"),
        provider=provider,
    )
    reader = pack.run(
        _request(operation="reader", url="https://docs.example.com/current"),
        provider=provider,
    )
    browser = pack.run(
        _request(
            operation="browser_fallback",
            url="https://docs.example.com/current",
            approvalGranted=True,
        ),
        provider=provider,
    )
    rendered = json.dumps(
        [fetch.public_projection(), reader.public_projection(), browser.public_projection()],
        sort_keys=True,
    )

    assert fetch.source_records[0].proof_type == "opened"
    assert reader.source_records[0].proof_type == "opened"
    assert browser.source_records[0].proof_type == "observed"
    assert "Opened source content" in rendered
    assert "Reader content" in rendered
    assert "Browser observed content" in rendered
    assert "raw_tool_log" not in rendered
    assert "rawResponse" not in rendered
    assert "rawSnapshot" not in rendered
    assert "Cookie" not in rendered


def test_blocked_url_classes_do_not_call_provider() -> None:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderPack,
    )

    provider = FakeWebContentProvider()
    pack = WebAcquisitionProviderPack(_config(browserFallbackEnabled=True))
    blocked = (
        "http://localhost:3000",
        "http://10.0.0.1/private",
        "http://169.254.169.254/latest/meta-data",
        "https://user:pass@example.com/private",
        "file:///etc/passwd",
        "https://docs.example.com/page?token=unsafe",
        "https://docs.example.com/captcha",
        "https://browser-worker:3003/snapshot",
    )

    for url in blocked:
        result = pack.run(_request(operation="fetch", url=url), provider=provider)
        assert result.status == "blocked"

    assert provider.calls == []


def test_citation_verifier_requires_opened_source_proof() -> None:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderPack,
        require_opened_source_proof,
    )

    provider = FakeWebContentProvider()
    pack = WebAcquisitionProviderPack(_config())
    fetch = pack.run(
        _request(operation="fetch", url="https://docs.example.com/current"),
        provider=provider,
    )
    search_only = WebAcquisitionProviderPack(_config()).run(
        _request(operation="search", query="current docs"),
        provider=FakeWebSearchProvider(),
    )

    assert require_opened_source_proof(fetch.source_records, (fetch.source_records[0].source_ref,)).status == "ok"
    missing = require_opened_source_proof(search_only.source_records, (search_only.source_records[0].source_ref,))
    assert missing.status == "repair_required"
    assert missing.missing_source_refs == (search_only.source_records[0].source_ref,)


def test_provider_denial_and_timeout_fail_open_without_source_claims() -> None:
    from openmagi_core_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderPack,
    )

    denied = WebAcquisitionProviderPack(_config()).run(
        _request(operation="search", query="current docs"),
        provider=FakeWebSearchProvider(status="denied"),
    )
    timeout = WebAcquisitionProviderPack(_config()).run(
        _request(operation="search", query="current docs"),
        provider=FakeWebSearchProvider(status="timeout"),
    )

    assert denied.status == "no_answer"
    assert timeout.status == "repair_required"
    assert denied.source_records == ()
    assert timeout.source_records == ()
    assert denied.public_projection()["parentOutputRefs"] == []
    assert timeout.public_projection()["parentOutputRefs"] == []


def test_live_web_acquisition_provider_pack_fixture_is_packaged() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "web_acquisition_browser_provider"
        / "live_provider_matrix.json"
    )

    matrix = json.loads(fixture.read_text())

    assert {row["operation"] for row in matrix["rows"]} >= {
        "search",
        "fetch",
        "reader",
        "browser_fallback",
    }


def test_live_web_acquisition_provider_pack_import_boundary() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.web_acquisition.live_provider_pack")
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.browser.live_provider_pack",
    "subprocess",
    "kubernetes",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "playwright",
    "selenium",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
