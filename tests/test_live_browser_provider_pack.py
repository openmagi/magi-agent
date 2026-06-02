from __future__ import annotations

import json
import subprocess
import sys


class FakeBrowserWorkerProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, private_only: bool = False, status: str = "ok") -> None:
        self.private_only = private_only
        self.status = status
        self.calls: list[str] = []
        self.texts: list[str | None] = []

    def open(self, request: object) -> dict[str, object]:
        self.calls.append("browser.open")
        return self._payload(request, title="Opened Page")

    def snapshot(self, request: object) -> dict[str, object]:
        self.calls.append("browser.snapshot")
        return self._payload(request, title="Snapshot Page")

    def scrape(self, request: object) -> dict[str, object]:
        self.calls.append("browser.scrape")
        return self._payload(request, title="Scraped Page")

    def screenshot(self, request: object) -> dict[str, object]:
        self.calls.append("browser.screenshot")
        payload = self._payload(request, title="Screenshot Page")
        payload["imageBase64"] = "raw-private-image"
        return payload

    def click(self, request: object) -> dict[str, object]:
        self.calls.append("browser.click")
        return {"status": self.status, "visibleText": "clicked"}

    def fill(self, request: object) -> dict[str, object]:
        self.calls.append("browser.fill")
        self.texts.append(getattr(request, "text", None))
        return {"status": self.status, "visibleText": "typed value"}

    def scroll(self, request: object) -> dict[str, object]:
        self.calls.append("browser.scroll")
        return {"status": self.status, "visibleText": "scrolled"}

    def _payload(self, request: object, *, title: str) -> dict[str, object]:
        if self.status != "ok":
            return {"status": self.status, "reason": "provider refused"}
        if self.private_only:
            return {
                "url": getattr(request, "url", None) or "https://docs.example.com/app",
                "title": "Cookie: session=unsafe",
                "visibleText": "raw_browser_snapshot Cookie: session=unsafe",
                "metadata": {"rawProfile": "Bearer unsafe-token"},
            }
        return {
            "url": getattr(request, "url", None) or "https://docs.example.com/app",
            "title": title,
            "visibleText": (
                "Rendered public fact.\n"
                "raw_browser_snapshot: Cookie: session=unsafe\n"
                "Authorization: Bearer unsafe-token"
            ),
            "metadata": {
                "quality": 0.91,
                "rawProfile": "profile-cookie",
                "formValue": "secret form text",
            },
        }


def _config(**overrides: object) -> object:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPackConfig

    payload = {
        "enabled": True,
        "localFakeProviderEnabled": True,
        "providerAllowlist": ("fake-browser",),
    }
    payload.update(overrides)
    return BrowserProviderPackConfig(**payload)


def _request(**overrides: object) -> object:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPackRequest

    payload = {
        "action": "browser.open",
        "requestId": "browser-1",
        "providerName": "fake-browser",
        "botIdDigest": "bot:abc",
        "ownerIdDigest": "owner:def",
        "sessionKeyDigest": "session:ghi",
        "turnId": "turn-browser",
        "sessionId": "browser-session-1",
        "url": "https://docs.example.com/app",
    }
    payload.update(overrides)
    return BrowserProviderPackRequest(**payload)


def _approval_receipt(request: object) -> object:
    from openmagi_core_agent.browser.live_provider_pack import (
        BrowserProviderPackApprovalReceipt,
        browser_provider_pack_request_digest,
    )

    return BrowserProviderPackApprovalReceipt(
        receiptRef=f"approval:{getattr(request, 'request_id')}",
        requestDigest=browser_provider_pack_request_digest(request),
        action=getattr(request, "action"),
        approved=True,
    )


def test_browser_provider_pack_default_disabled_blocks_actions_without_provider_calls() -> None:
    from openmagi_core_agent.browser.live_provider_pack import (
        BrowserProviderPack,
        BrowserProviderPackConfig,
    )

    provider = FakeBrowserWorkerProvider()
    pack = BrowserProviderPack(BrowserProviderPackConfig())

    for action in (
        "browser.open",
        "browser.snapshot",
        "browser.scrape",
        "browser.click",
        "browser.fill",
        "browser.scroll",
        "browser.screenshot",
    ):
        result = pack.run(
            _request(
                action=action,
                selector="@e1",
                text="hello",
                direction="down",
                screenshotPath="screens/page.png",
                approvalGranted=True,
            ),
            provider=provider,
        )
        assert result.status == "disabled"
        assert result.source_records == ()

    assert provider.calls == []


def test_fake_browser_provider_runs_all_actions_only_when_local_fake_enabled() -> None:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPack

    provider = FakeBrowserWorkerProvider()
    pack = BrowserProviderPack(_config())

    open_result = pack.run(_request(action="browser.open"), provider=provider)
    snapshot = pack.run(_request(action="browser.snapshot"), provider=provider)
    scrape = pack.run(_request(action="browser.scrape"), provider=provider)
    screenshot_request = _request(
        action="browser.screenshot",
        screenshotPath="screens/page.png",
    )
    click_request = _request(action="browser.click", selector="@e1")
    fill_request = _request(
        action="browser.fill",
        selector="@e1",
        text="typed public text",
    )
    screenshot = pack.run(
        screenshot_request,
        provider=provider,
        approval_receipt=_approval_receipt(screenshot_request),
    )
    click = pack.run(
        click_request,
        provider=provider,
        approval_receipt=_approval_receipt(click_request),
    )
    fill = pack.run(
        fill_request,
        provider=provider,
        approval_receipt=_approval_receipt(fill_request),
    )
    disabled_local_fake = BrowserProviderPack(_config(localFakeProviderEnabled=False)).run(
        _request(action="browser.snapshot"),
        provider=provider,
    )
    encoded = json.dumps(
        [
            open_result.public_projection(),
            snapshot.public_projection(),
            scrape.public_projection(),
            screenshot.public_projection(),
            click.public_projection(),
            fill.public_projection(),
        ],
        sort_keys=True,
    )

    assert provider.calls == [
        "browser.open",
        "browser.snapshot",
        "browser.scrape",
        "browser.screenshot",
        "browser.click",
        "browser.fill",
    ]
    assert open_result.source_records[0].proof_type == "opened"
    assert snapshot.source_records[0].proof_type == "observed"
    assert scrape.source_records[0].proof_type == "observed"
    assert screenshot.source_records[0].artifact_ref is not None
    assert click.source_records == ()
    assert fill.source_records == ()
    assert disabled_local_fake.status == "disabled"
    assert "Rendered public fact" in encoded
    assert "raw_browser_snapshot" not in encoded
    assert "Cookie" not in encoded
    assert "Authorization" not in encoded
    assert "unsafe-token" not in encoded
    assert "raw-private-image" not in encoded
    assert '"browserExecuted": false' in encoded
    assert '"parentContextInjected": false' in encoded


def test_browser_provider_pack_approval_and_private_payload_gates_happen_before_provider_calls() -> None:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPack

    provider = FakeBrowserWorkerProvider()
    pack = BrowserProviderPack(_config())

    click = pack.run(_request(action="browser.click", selector="@e1"), provider=provider)
    fill = pack.run(
        _request(
            action="browser.fill",
            selector="@e1",
            text="password=unsafe",
            approvalGranted=True,
        ),
        provider=provider,
    )
    screenshot = pack.run(
        _request(action="browser.screenshot", screenshotPath="screens/page.png"),
        provider=provider,
    )

    assert click.status == "approval_required"
    assert fill.status == "blocked"
    assert fill.reason_codes == ("private_or_captcha_payload_blocked",)
    assert screenshot.status == "approval_required"
    assert provider.calls == []


def test_browser_provider_pack_request_approval_granted_is_not_trusted() -> None:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPack

    provider = FakeBrowserWorkerProvider()
    pack = BrowserProviderPack(_config())

    forged_click = pack.run(
        _request(action="browser.click", selector="@e1", approvalGranted=True),
        provider=provider,
    )
    forged_auth_open = pack.run(
        _request(
            action="browser.open",
            url="https://docs.example.com/login",
            approvalGranted=True,
        ),
        provider=provider,
    )
    approved_auth_request = _request(
        action="browser.open",
        url="https://docs.example.com/login",
    )
    approved_auth_open = pack.run(
        approved_auth_request,
        provider=provider,
        approval_receipt=_approval_receipt(approved_auth_request),
    )

    assert forged_click.status == "approval_required"
    assert forged_auth_open.status == "approval_required"
    assert approved_auth_open.status == "ok"
    assert provider.calls == ["browser.open"]


def test_browser_fill_request_digest_includes_public_text() -> None:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPack

    provider = FakeBrowserWorkerProvider()
    pack = BrowserProviderPack(_config())

    first_request = _request(
        action="browser.fill",
        requestId="browser-fill-same",
        selector="@e1",
        text="first public value",
    )
    second_request = _request(
        action="browser.fill",
        requestId="browser-fill-same",
        selector="@e1",
        text="second public value",
    )
    first = pack.run(
        first_request,
        provider=provider,
        approval_receipt=_approval_receipt(first_request),
    )
    second = pack.run(
        second_request,
        provider=provider,
        approval_receipt=_approval_receipt(second_request),
    )

    assert first.status == "ok"
    assert second.status == "ok"
    assert first.request_digest != second.request_digest
    assert first.provider_receipt is not None
    assert second.provider_receipt is not None
    assert first.provider_receipt.request_digest != second.provider_receipt.request_digest


def test_browser_approval_receipt_binds_raw_text_digest_not_redacted_text() -> None:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPack

    provider = FakeBrowserWorkerProvider()
    pack = BrowserProviderPack(_config())
    first_request = _request(
        action="browser.fill",
        requestId="browser-fill-secret",
        selector="@e1",
        text="sk-" + "test-" + "aaaaaaaa",
    )
    second_request = _request(
        action="browser.fill",
        requestId="browser-fill-secret",
        selector="@e1",
        text="sk-" + "test-" + "bbbbbbbb",
    )

    forged = pack.run(
        second_request,
        provider=provider,
        approval_receipt=_approval_receipt(first_request),
    )

    assert forged.status == "approval_required"
    assert forged.reason_codes == ("browser_action_requires_approval",)
    assert provider.calls == []
    assert provider.texts == []


def test_browser_provider_output_records_only_after_sanitizer_passes() -> None:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPack

    provider = FakeBrowserWorkerProvider(private_only=True)
    pack = BrowserProviderPack(_config())

    result = pack.run(_request(action="browser.snapshot"), provider=provider)
    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert result.source_records == ()
    assert result.reason_codes == ("browser_output_sanitizer_rejected",)
    assert projection["parentOutputRefs"] == []
    assert "session=unsafe" not in encoded
    assert "Bearer" not in encoded
    assert "rawProfile" not in encoded


def test_browser_provider_pack_blocks_research_use_without_web_acquisition_policy() -> None:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPack

    provider = FakeBrowserWorkerProvider()
    blocked_unselected = BrowserProviderPack(_config(browserFallbackEnabled=True)).run(
        _request(action="browser.snapshot", context="research"),
        provider=provider,
    )
    blocked_policy = BrowserProviderPack(_config(browserFallbackEnabled=True)).run(
        _request(
            action="browser.snapshot",
            context="research",
            webAcquisitionBrowserFallbackSelected=True,
        ),
        provider=provider,
    )
    allowed = BrowserProviderPack(_config(browserFallbackEnabled=True)).run(
        _request(
            action="browser.snapshot",
            context="research",
            webAcquisitionBrowserFallbackSelected=True,
            webAcquisitionPolicyAllowsBrowser=True,
        ),
        provider=provider,
    )

    assert blocked_unselected.status == "blocked"
    assert blocked_unselected.reason_codes == ("web_acquisition_browser_fallback_not_selected",)
    assert blocked_policy.status == "blocked"
    assert blocked_policy.reason_codes == ("web_acquisition_browser_policy_blocked",)
    assert allowed.status == "ok"
    assert provider.calls == ["browser.snapshot"]


def test_browser_provider_pack_blocks_web_acquisition_context_without_selected_fallback_policy() -> None:
    from openmagi_core_agent.browser.live_provider_pack import BrowserProviderPack

    provider = FakeBrowserWorkerProvider()
    blocked_unselected = BrowserProviderPack(_config(browserFallbackEnabled=True)).run(
        _request(action="browser.snapshot", context="web_acquisition"),
        provider=provider,
    )
    blocked_policy = BrowserProviderPack(_config(browserFallbackEnabled=True)).run(
        _request(
            action="browser.snapshot",
            context="web_acquisition",
            webAcquisitionBrowserFallbackSelected=True,
        ),
        provider=provider,
    )
    allowed = BrowserProviderPack(_config(browserFallbackEnabled=True)).run(
        _request(
            action="browser.snapshot",
            context="web_acquisition",
            webAcquisitionBrowserFallbackSelected=True,
            webAcquisitionPolicyAllowsBrowser=True,
        ),
        provider=provider,
    )

    assert blocked_unselected.status == "blocked"
    assert blocked_unselected.reason_codes == ("web_acquisition_browser_fallback_not_selected",)
    assert blocked_policy.status == "blocked"
    assert blocked_policy.reason_codes == ("web_acquisition_browser_policy_blocked",)
    assert allowed.status == "ok"
    assert provider.calls == ["browser.snapshot"]


def test_live_browser_provider_pack_import_boundary() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.browser.live_provider_pack")
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "openmagi_core_agent.runtime_selector",
    "openmagi_core_agent.chat_proxy",
    "openmagi_core_agent.k8s",
    "kubernetes",
    "subprocess",
    "socket",
    "requests",
    "httpx",
    "aiohttp",
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
