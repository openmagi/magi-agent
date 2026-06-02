from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path


class FakeBrowserProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, request: object) -> dict[str, object]:
        action = getattr(request, "action")
        self.calls.append(action)
        return {
            "url": getattr(request, "url", None) or "https://docs.example.com/app",
            "title": "Rendered App",
            "visibleText": (
                "Rendered public fact.\n"
                "raw_browser_snapshot: Cookie: session=unsafe\n"
                "chain_of_thought: hidden"
            ),
            "metadata": {
                "quality": 0.9,
                "rawToolLog": "Authorization: Bearer unsafe",
            },
        }


def test_browser_provider_defaults_off_and_never_calls_provider() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        BrowserRequest,
        LocalBrowserProviderRuntime,
    )

    provider = FakeBrowserProvider()
    runtime = LocalBrowserProviderRuntime(BrowserProviderConfig(), provider=provider)

    result = asyncio.run(runtime.run(BrowserRequest(action="browser.open", url="https://example.com")))

    assert result.status == "disabled"
    assert result.error_code == "browser_provider_disabled"
    assert provider.calls == []
    assert result.diagnostic_metadata["productionBrowserEnabled"] is False
    assert result.attachment_flags.browser_executed is False


def test_browser_open_snapshot_scrape_and_screenshot_emit_only_safe_refs() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        BrowserRequest,
        LocalBrowserProviderRuntime,
    )

    provider = FakeBrowserProvider()
    runtime = LocalBrowserProviderRuntime(
        BrowserProviderConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    open_result = asyncio.run(
        runtime.run(BrowserRequest(action="browser.open", url="https://example.com/app"))
    )
    snapshot = asyncio.run(runtime.run(BrowserRequest(action="browser.snapshot")))
    scrape = asyncio.run(runtime.run(BrowserRequest(action="browser.scrape")))
    screenshot = asyncio.run(
        runtime.run(
            BrowserRequest(
                action="browser.screenshot",
                screenshot_path="screens/page.png",
                approval_granted=True,
            )
        )
    )
    encoded = json.dumps(
        [
            open_result.public_projection(),
            snapshot.public_projection(),
            scrape.public_projection(),
            screenshot.public_projection(),
        ],
        sort_keys=True,
    )

    assert provider.calls == [
        "browser.open",
        "browser.snapshot",
        "browser.scrape",
        "browser.screenshot",
    ]
    assert open_result.records[0].proof_type == "opened"
    assert snapshot.records[0].proof_type == "observed"
    assert screenshot.records[0].artifact_ref is not None
    assert screenshot.public_projection()["parentOutputRefs"] == [
        "source:browser:src_1",
        "evidence:browser:src_1",
        screenshot.records[0].artifact_ref,
    ]
    assert "Rendered public fact" in encoded
    assert "raw_browser_snapshot" not in encoded
    assert "Cookie:" not in encoded
    assert "session=unsafe" not in encoded
    assert "Authorization" not in encoded
    assert "chain_of_thought" not in encoded
    assert "imageBase64" in encoded
    assert '"imageBase64": null' in encoded
    assert '"browserExecuted": false' in encoded
    assert '"browserWorkerAttached": false' in encoded


def test_browser_provider_blocks_private_auth_captcha_and_cluster_urls_before_calls() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        BrowserRequest,
        LocalBrowserProviderRuntime,
    )

    provider = FakeBrowserProvider()
    runtime = LocalBrowserProviderRuntime(
        BrowserProviderConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    blocked_urls = (
        "data:text/html,<h1>x</h1>",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://169.254.169.254/latest/meta-data",
        "https://kubernetes.default.svc/api",
        "https://user:pass@example.com/private",
        "https://example.com/captcha",
    )

    for url in blocked_urls:
        result = asyncio.run(runtime.run(BrowserRequest(action="browser.open", url=url)))
        assert result.status == "blocked"

    assert provider.calls == []


def test_browser_provider_rejects_unmarked_local_fake_provider() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        BrowserRequest,
        LocalBrowserProviderRuntime,
    )

    class UnmarkedProvider(FakeBrowserProvider):
        openmagi_local_fake_provider = False

    provider = UnmarkedProvider()
    runtime = LocalBrowserProviderRuntime(
        BrowserProviderConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    result = asyncio.run(runtime.run(BrowserRequest(action="browser.snapshot")))

    assert result.status == "blocked"
    assert result.error_code == "local_fake_browser_provider_untrusted"
    assert provider.calls == []


def test_browser_mutating_actions_are_approval_gated_and_sanitized() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        BrowserRequest,
        LocalBrowserProviderRuntime,
    )

    provider = FakeBrowserProvider()
    runtime = LocalBrowserProviderRuntime(
        BrowserProviderConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    click_blocked = asyncio.run(
        runtime.run(BrowserRequest(action="browser.click", selector="[ref=e29]"))
    )
    fill_blocked = asyncio.run(
        runtime.run(BrowserRequest(action="browser.fill", selector="@e24", text="hello"))
    )
    fill_allowed = asyncio.run(
        runtime.run(
            BrowserRequest(
                action="browser.fill",
                selector='textbox "Investment scope"[ref=e24]',
                text="hello",
                approval_granted=True,
            )
        )
    )
    private_fill = asyncio.run(
        runtime.run(
            BrowserRequest(
                action="browser.fill",
                selector="@e24",
                text="Cookie: session=unsafe",
                approval_granted=True,
            )
        )
    )

    assert click_blocked.status == "approval_required"
    assert fill_blocked.status == "approval_required"
    assert fill_allowed.status == "ok"
    assert fill_allowed.records == ()
    assert private_fill.status == "blocked"
    assert private_fill.error_code == "private_or_captcha_payload_blocked"
    assert provider.calls == ["browser.fill"]


def test_browser_screenshot_path_and_scroll_validation_happen_before_calls() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        BrowserRequest,
        LocalBrowserProviderRuntime,
    )

    provider = FakeBrowserProvider()
    runtime = LocalBrowserProviderRuntime(
        BrowserProviderConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    bad_path = asyncio.run(
        runtime.run(
            BrowserRequest(
                action="browser.screenshot",
                screenshot_path="../escape.png",
                approval_granted=True,
            )
        )
    )
    missing_direction = asyncio.run(runtime.run(BrowserRequest(action="browser.scroll")))

    assert bad_path.status == "blocked"
    assert bad_path.error_code == "invalid_screenshot_path"
    assert missing_direction.status == "blocked"
    assert missing_direction.error_code == "direction_required"
    assert provider.calls == []


def test_browser_session_lease_blocks_out_of_order_actions_and_budgets() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserRequest,
        BrowserSessionLease,
        evaluate_browser_session_action,
    )

    empty_lease = BrowserSessionLease(
        sessionId="browser-session-1",
        turnId="turn-browser",
        maxActions=2,
        maxScreenshots=1,
    )
    observed_lease = empty_lease.model_copy(
        update={"observedFrameRefs": ("source:browser:src_1",)}
    )
    exhausted_lease = observed_lease.model_copy(update={"actionCount": 2})
    screenshot_exhausted = observed_lease.model_copy(update={"screenshotCount": 1})
    expired_lease = observed_lease.model_copy(update={"expired": True})

    base_request = {
        "turnId": "turn-browser",
        "sessionId": "browser-session-1",
    }
    out_of_order = evaluate_browser_session_action(
        BrowserRequest(
            action="browser.click",
            selector="@e1",
            approvalGranted=True,
            **base_request,
        ),
        empty_lease,
    )
    allowed_click = evaluate_browser_session_action(
        BrowserRequest(
            action="browser.click",
            selector="@e1",
            approvalGranted=True,
            **base_request,
        ),
        observed_lease,
    )
    action_budget = evaluate_browser_session_action(
        BrowserRequest(action="browser.scroll", direction="down", **base_request),
        exhausted_lease,
    )
    screenshot_budget = evaluate_browser_session_action(
        BrowserRequest(
            action="browser.screenshot",
            screenshotPath="screens/page.png",
            approvalGranted=True,
            **base_request,
        ),
        screenshot_exhausted,
    )
    expired = evaluate_browser_session_action(
        BrowserRequest(action="browser.snapshot", **base_request),
        expired_lease,
    )
    missing_approval = evaluate_browser_session_action(
        BrowserRequest(
            action="browser.screenshot",
            screenshotPath="screens/page.png",
            **base_request,
        ),
        observed_lease,
    )
    mismatch = evaluate_browser_session_action(
        BrowserRequest(
            action="browser.snapshot",
            turnId="turn-other",
            sessionId="browser-session-other",
        ),
        observed_lease,
    )
    private_lease = evaluate_browser_session_action(
        BrowserRequest(
            action="browser.snapshot",
            turnId="browser-session:43c3a8b151bce41a",
            sessionId="browser-session:3c427d0fed519a78",
        ),
        BrowserSessionLease(
            sessionId="/Users/kevin/private-session",
            turnId="/workspace/private-turn",
            observedFrameRefs=("/data/bots/raw-frame",),
        ),
    )

    assert out_of_order.status == "blocked"
    assert out_of_order.reason_code == "observed_frame_required"
    assert out_of_order.execution_allowed is False
    assert allowed_click.status == "allowed"
    assert allowed_click.next_lease is not None
    assert allowed_click.next_lease.action_count == 1
    assert action_budget.reason_code == "action_budget_exceeded"
    assert screenshot_budget.reason_code == "screenshot_budget_exceeded"
    assert expired.reason_code == "session_expired"
    assert missing_approval.reason_code == "browser_action_requires_approval"
    assert mismatch.status == "blocked"
    assert mismatch.reason_code == "session_lease_mismatch"
    assert "/Users/kevin" not in str(private_lease.model_dump(by_alias=True))
    assert "/workspace/private-turn" not in str(private_lease.model_dump(by_alias=True))
    assert "/data/bots" not in str(private_lease.model_dump(by_alias=True))
    assert all(
        decision.attachment_flags.browser_executed is False
        for decision in (
            out_of_order,
            allowed_click,
            action_budget,
            screenshot_budget,
            expired,
            missing_approval,
            mismatch,
            private_lease,
        )
    )


def test_browser_public_projection_redacts_forged_frame_preview_and_refs() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserProviderResult,
        BrowserSourceRecord,
    )

    record = BrowserSourceRecord.model_construct(
        source_ref="source:/Users/kevin/private",
        evidence_ref="evidence:Authorization: Bearer unsafe-token",
        artifact_ref="artifact:/workspace/private/screen.png",
        method="browser.snapshot",
        provider="provider sk-browser-secret",
        url="https://docs.example.com/private?token=unsafe",
        normalized_url="https://docs.example.com/private?token=unsafe",
        content_digest="/Users/kevin/raw",
        proof_type="observed",
        title="raw_browser_snapshot /Users/kevin/private",
        metadata={"routeAttached": True, "note": "safe"},
    )
    result = BrowserProviderResult(
        status="ok",
        action="browser.snapshot",
        records=(record,),
        browserFrame={
            "type": "browser_frame",
            "action": "browser.snapshot",
            "sourceRef": "/Users/kevin/raw-source",
            "evidenceRef": "Authorization: Bearer unsafe-token",
            "artifactRef": "/workspace/private/screen.png",
            "imageBase64": "raw-image-data",
            "rawSnapshotInjected": True,
            "rawToolLogInjected": True,
            "cookie": "session=unsafe",
        },
        publicPreview="raw_browser_snapshot Cookie: session=unsafe\n/Users/kevin/private",
        diagnosticMetadata={
            "productionBrowserEnabled": True,
            "trusted": True,
            "authoritative": True,
            "safeBudget": 1,
        },
    )

    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["browserFrame"]["imageBase64"] is None
    assert projection["browserFrame"]["rawSnapshotInjected"] is False
    assert projection["browserFrame"]["rawToolLogInjected"] is False
    assert "/Users/kevin" not in encoded
    assert "/workspace/private" not in encoded
    assert "Authorization" not in encoded
    assert "unsafe-token" not in encoded
    assert "sk-browser-secret" not in encoded
    assert "raw-image-data" not in encoded
    assert "productionBrowserEnabled" not in encoded
    assert "trusted" not in encoded
    assert "authoritative" not in encoded
    assert "routeAttached" not in encoded
    assert projection["diagnosticMetadata"]["safeBudget"] == 1


def test_browser_local_runtime_rejects_records_when_sanitized_output_has_no_public_evidence() -> None:
    from openmagi_core_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        BrowserRequest,
        LocalBrowserProviderRuntime,
    )

    class PrivateOnlyProvider:
        openmagi_local_fake_provider = True

        async def run(self, _request: object) -> dict[str, object]:
            return {
                "url": "https://docs.example.com/private",
                "title": "Cookie: session=unsafe",
                "visibleText": "raw_browser_snapshot Cookie: session=unsafe",
                "metadata": {"rawProfile": "Authorization: Bearer unsafe"},
            }

    result = asyncio.run(
        LocalBrowserProviderRuntime(
            BrowserProviderConfig(enabled=True, localFakeProviderEnabled=True),
            provider=PrivateOnlyProvider(),
        ).run(BrowserRequest(action="browser.snapshot"))
    )

    assert result.status == "blocked"
    assert result.error_code == "browser_output_sanitizer_rejected"
    assert result.records == ()
    assert result.public_projection()["parentOutputRefs"] == []


def test_browser_import_boundary_has_no_live_browser_or_runtime_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "openmagi_core_agent"
        / "browser"
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
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.tools",
        "openmagi_core_agent.transport",
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
    for fragment in ("__import__(", "importlib.import_module", "subprocess.run", "cdpEndpoint"):
        assert fragment not in source


def test_browser_local_runtime_uses_shared_provider_execution_boundary(monkeypatch) -> None:
    from openmagi_core_agent.browser import provider_boundary

    calls: list[tuple[str, str]] = []
    original_execute = provider_boundary.ProviderExecutionBoundary.execute

    async def spy_execute(self: object, request: object, *, provider: object | None = None) -> object:
        calls.append((request.provider_name, request.operation))
        return await original_execute(self, request, provider=provider)

    monkeypatch.setattr(provider_boundary.ProviderExecutionBoundary, "execute", spy_execute)

    provider = FakeBrowserProvider()
    runtime = provider_boundary.LocalBrowserProviderRuntime(
        provider_boundary.BrowserProviderConfig(enabled=True, localFakeProviderEnabled=True),
        provider=provider,
    )

    result = asyncio.run(runtime.run(provider_boundary.BrowserRequest(action="browser.snapshot")))

    assert result.status == "ok"
    assert provider.calls == ["browser.snapshot"]
    assert calls == [("openmagi.browser-provider.system", "browser.snapshot")]
