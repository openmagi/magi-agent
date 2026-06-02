from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest
from pydantic import ValidationError


class FakeProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, request: object) -> Mapping[str, object]:
        self.calls.append(request.model_dump(by_alias=True))
        return {
            "ok": True,
            "echo": request.payload,
            "Authorization": "Bearer provider-token",
            "path": "/Users/kevin/private/provider-output.txt",
        }


class ProductionTestDouble:
    openmagi_local_fake_provider = False
    openmagi_provider_boundary_test_double = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, request: object) -> Mapping[str, object]:
        self.calls.append(request.model_dump(by_alias=True))
        return {"ok": True, "provider": request.provider_name}


class StatusForgingProvider:
    openmagi_local_fake_provider = True

    async def execute(self, request: object) -> Mapping[str, object]:
        return {
            "status": "disabled",
            "providerName": "Alice Example private provider",
            "ok": True,
        }


class FailingProvider:
    openmagi_local_fake_provider = True

    async def execute(self, request: object) -> Mapping[str, object]:
        raise RuntimeError(
            "provider failed for safe query with Authorization Bearer live-token "
            "from /Users/kevin/private/source.txt"
        )


def _scope(*, selected: bool = True, environment: str = "test") -> object:
    from magi_agent.runtime.provider_execution import ProviderExecutionScope

    return ProviderExecutionScope(
        environment=environment,
        botIdDigest="bot:abc123",
        ownerIdDigest="owner:def456",
        selectedScope=selected,
    )


def _request(*, provider_name: str = "fake-search", operation: str = "search") -> object:
    from magi_agent.runtime.provider_execution import ProviderExecutionRequest

    return ProviderExecutionRequest(
        providerName=provider_name,
        operation=operation,
        payload={
            "query": "safe query",
            "rawUserText": "secret raw text",
            "Authorization": "Bearer live-token",
            "path": "/workspace/private/source.txt",
        },
        scope=_scope(),
        evidenceRefs=("evidence://provider/request-1",),
    )


def test_disabled_provider_execution_never_calls_provider() -> None:
    from magi_agent.runtime.provider_execution import (
        ProviderExecutionBoundary,
        ProviderExecutionConfig,
    )

    provider = FakeProvider()
    result = asyncio.run(
        ProviderExecutionBoundary(ProviderExecutionConfig()).execute(
            _request(),
            provider=provider,
        )
    )

    assert result.status == "disabled"
    assert result.provider_called is False
    assert provider.calls == []
    assert result.receipt.status == "disabled"
    assert result.authority_flags["providerCalled"] is False
    assert result.authority_flags["productionProviderCall"] is False


def test_local_fake_provider_requires_explicit_fake_enablement() -> None:
    from magi_agent.runtime.provider_execution import (
        ProviderExecutionBoundary,
        ProviderExecutionConfig,
    )

    provider = FakeProvider()
    blocked = asyncio.run(
        ProviderExecutionBoundary(ProviderExecutionConfig(enabled=True)).execute(
            _request(),
            provider=provider,
        )
    )
    allowed = asyncio.run(
        ProviderExecutionBoundary(
            ProviderExecutionConfig(enabled=True, localFakeProviderEnabled=True)
        ).execute(
            _request(),
            provider=provider,
        )
    )

    assert blocked.status == "blocked"
    assert blocked.reason_codes == ("local_fake_provider_disabled",)
    assert allowed.status == "ok"
    assert allowed.provider_called is True
    assert len(provider.calls) == 1
    assert allowed.receipt.request_digest.startswith("sha256:")
    assert allowed.receipt.response_digest.startswith("sha256:")
    rendered = json.dumps(allowed.model_dump(by_alias=True), sort_keys=True)
    assert "safe query" not in rendered
    assert "secret raw text" not in rendered
    assert "live-token" not in rendered
    assert "provider-token" not in rendered
    assert "/workspace/private" not in rendered
    assert "/Users/kevin" not in rendered


def test_production_provider_call_requires_all_server_side_gates() -> None:
    from magi_agent.runtime.provider_execution import (
        ProviderExecutionBoundary,
        ProviderExecutionConfig,
        ProviderExecutionRequest,
    )

    provider = ProductionTestDouble()
    request = ProviderExecutionRequest(
        providerName="prod-search",
        operation="search",
        payload={"query": "safe"},
        scope=_scope(selected=True, environment="test"),
    )

    missing_gates = asyncio.run(
        ProviderExecutionBoundary(ProviderExecutionConfig(enabled=True)).execute(
            request,
            provider=provider,
        )
    )
    wrong_provider = asyncio.run(
        ProviderExecutionBoundary(
            ProviderExecutionConfig(
                enabled=True,
                productionProviderCallsEnabled=True,
                selectedScopeRequired=True,
                providerAllowlist=("other-provider",),
            )
        ).execute(request, provider=provider)
    )
    wrong_scope = asyncio.run(
        ProviderExecutionBoundary(
            ProviderExecutionConfig(
                enabled=True,
                productionProviderCallsEnabled=True,
                selectedScopeRequired=True,
                providerAllowlist=("prod-search",),
            )
        ).execute(request.model_copy(update={"scope": _scope(selected=False)}), provider=provider)
    )
    allowed = asyncio.run(
        ProviderExecutionBoundary(
            ProviderExecutionConfig(
                enabled=True,
                productionProviderCallsEnabled=True,
                selectedScopeRequired=True,
                providerAllowlist=("prod-search",),
            )
        ).execute(request, provider=provider)
    )

    assert missing_gates.status == "blocked"
    assert wrong_provider.status == "blocked"
    assert wrong_scope.status == "blocked"
    assert allowed.status == "ok"
    assert provider.calls and len(provider.calls) == 1
    assert allowed.authority_flags["providerCalled"] is False
    assert allowed.authority_flags["productionProviderCall"] is False


def test_selected_scope_gate_cannot_be_configured_away() -> None:
    from magi_agent.runtime.provider_execution import ProviderExecutionConfig

    with pytest.raises(ValidationError):
        ProviderExecutionConfig(
            enabled=True,
            productionProviderCallsEnabled=True,
            selectedScopeRequired=False,
            providerAllowlist=("prod-search",),
        )


def test_provider_exception_diagnostics_are_redacted_without_raw_exception_text() -> None:
    from magi_agent.runtime.provider_execution import (
        ProviderExecutionBoundary,
        ProviderExecutionConfig,
    )

    result = asyncio.run(
        ProviderExecutionBoundary(
            ProviderExecutionConfig(enabled=True, localFakeProviderEnabled=True)
        ).execute(
            _request(),
            provider=FailingProvider(),
        )
    )

    rendered = json.dumps(result.model_dump(by_alias=True), sort_keys=True)
    assert result.status == "error"
    assert result.provider_called is True
    assert "provider failed" not in rendered
    assert "safe query" not in rendered
    assert "live-token" not in rendered
    assert "/Users/kevin" not in rendered
    assert result.diagnostic_metadata["providerError"] == "[redacted-provider-error]"


def test_request_controlled_identifiers_are_not_raw_diagnostics_or_receipt_status() -> None:
    from magi_agent.runtime.provider_execution import (
        ProviderExecutionBoundary,
        ProviderExecutionConfig,
        ProviderExecutionRequest,
        ProviderExecutionScope,
    )

    request = ProviderExecutionRequest(
        providerName="Alice Example private provider",
        operation="search for Alice Example SSN",
        payload={"query": "safe"},
        scope=ProviderExecutionScope(
            environment="Alice Example laptop",
            botIdDigest="bot:abc123",
            ownerIdDigest="owner:def456",
            selectedScope=True,
        ),
    )

    result = asyncio.run(
        ProviderExecutionBoundary(
            ProviderExecutionConfig(enabled=True, localFakeProviderEnabled=True)
        ).execute(request, provider=StatusForgingProvider())
    )

    rendered = json.dumps(result.model_dump(by_alias=True), sort_keys=True)
    assert result.status == "ok"
    assert result.receipt.status == "ok"
    assert "Alice Example" not in rendered
    assert "search for" not in rendered
    assert "private provider" not in rendered
    assert result.diagnostic_metadata["providerRef"].startswith("provider:")
    assert result.diagnostic_metadata["operationRef"].startswith("operation:")
    assert result.diagnostic_metadata["environmentRef"].startswith("environment:")


def test_provider_execution_result_copy_cannot_forge_authority_flags() -> None:
    from magi_agent.runtime.provider_execution import (
        ProviderExecutionBoundary,
        ProviderExecutionConfig,
    )

    result = asyncio.run(
        ProviderExecutionBoundary(ProviderExecutionConfig()).execute(_request())
    )

    forged = result.model_copy(
        update={
            "authority_flags": {
                "providerCalled": True,
                "productionProviderCall": True,
                "networkFetched": True,
                "routeAttached": True,
                "userVisibleOutput": True,
            }
        }
    )

    assert forged.authority_flags["providerCalled"] is False
    assert forged.authority_flags["productionProviderCall"] is False
    assert forged.authority_flags["networkFetched"] is False
    assert forged.authority_flags["routeAttached"] is False
    assert forged.authority_flags["userVisibleOutput"] is False
