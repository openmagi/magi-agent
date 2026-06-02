from __future__ import annotations

import asyncio

import pytest

from magi_agent.browser.provider_boundary import (
    BrowserProviderConfig,
    BrowserRequest,
    LocalBrowserProviderRuntime,
)
from magi_agent.web_acquisition.policy import url_policy_error


class FakeBrowserProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    def run(self, request: BrowserRequest) -> dict[str, object]:
        self.calls += 1
        return {
            "content": "Rendered public content",
            "evidenceAvailable": True,
            "method": request.action,
            "url": request.url or "https://docs.example.com/",
        }


@pytest.mark.parametrize(
    ("url", "reason"),
    (
        ("http://100.64.0.1/latest/meta-data", "private_url_blocked"),
        ("http://198.18.0.1/benchmark", "private_url_blocked"),
        ("http://[fc00::1]/internal", "private_url_blocked"),
        ("http://0x7f000001/", "local_url_blocked"),
        ("http://127.1/", "local_url_blocked"),
        ("http://127.0.1/", "local_url_blocked"),
        ("http://017700000001/", "local_url_blocked"),
        ("http://0xA9FEA9FE/latest/meta-data", "metadata_url_blocked"),
        ("http://0x64400001/", "private_url_blocked"),
        ("http://0.0.0.0/admin", "local_url_blocked"),
        ("http://[::1]/admin", "local_url_blocked"),
        ("http://169.254.169.254/latest/meta-data", "metadata_url_blocked"),
        ("http://kubernetes.default.svc/api", "cluster_url_blocked"),
        ("https://example.com/path?credential=redacted-example", "credential_url_blocked"),
    ),
)
def test_hermes_style_egress_blocks_private_and_credential_urls(
    url: str,
    reason: str,
) -> None:
    assert url_policy_error(url) == reason


def test_path_name_login_is_not_hard_blocked_without_auth_context() -> None:
    assert url_policy_error("https://example.com/login") is None


def test_oauth_documentation_path_is_not_hard_blocked_without_auth_context() -> None:
    assert url_policy_error("https://example.com/docs/oauth") is None


@pytest.mark.parametrize(
    "url",
    (
        "https://docs.example.com/lo%67in",
        "https://docs.example.com/o%61uth/authorize",
        "https://docs.example.com/%61uth/callback",
        "https://docs.example.com/%2561uth/callback",
    ),
)
def test_encoded_browser_auth_paths_require_host_approval(url: str) -> None:
    provider = FakeBrowserProvider()
    runtime = LocalBrowserProviderRuntime(
        BrowserProviderConfig(enabled=True, localFakeProviderEnabled=True),
        provider=provider,
    )

    blocked = asyncio.run(
        runtime.run(
            BrowserRequest(
                action="browser.open",
                url=url,
                approvalGranted=False,
            ),
        ),
    )
    approved = asyncio.run(
        runtime.run(
            BrowserRequest(
                action="browser.open",
                url=url,
                approvalGranted=True,
            ),
        ),
    )

    assert blocked.status == "approval_required"
    assert blocked.error_code == "browser_action_requires_approval"
    assert approved.status == "ok"
    assert provider.calls == 1
