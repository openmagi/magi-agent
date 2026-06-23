from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import inspect
import time
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.runtime.provider_receipts import (
    ProviderReceipt,
    ProviderReceiptStatus,
    build_provider_receipt,
    sanitize_provider_payload,
)


ProviderExecutionStatus = Literal["disabled", "blocked", "ok", "error"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class ProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def execute(self, request: ProviderExecutionRequest) -> Mapping[str, object]: ...


class ProviderExecutionConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    production_provider_calls_enabled: bool = Field(
        default=False,
        alias="productionProviderCallsEnabled",
    )
    selected_scope_required: Literal[True] = Field(default=True, alias="selectedScopeRequired")
    provider_allowlist: tuple[str, ...] = Field(default=(), alias="providerAllowlist")

    @field_validator("provider_allowlist", mode="before")
    @classmethod
    def _coerce_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(str(item) for item in value)
        return ()


class ProviderExecutionScope(BaseModel):
    model_config = _MODEL_CONFIG

    environment: str = "test"
    bot_id_digest: str = Field(alias="botIdDigest")
    owner_id_digest: str = Field(alias="ownerIdDigest")
    selected_scope: bool = Field(default=False, alias="selectedScope")
    session_id_digest: str | None = Field(default=None, alias="sessionIdDigest")

    @field_validator("environment", "bot_id_digest", "owner_id_digest", "session_id_digest")
    @classmethod
    def _non_empty_safe_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("scope field must be non-empty")
        return clean[:180]


class ProviderExecutionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    provider_name: str = Field(alias="providerName")
    operation: str
    payload: Mapping[str, object] = Field(default_factory=dict)
    scope: ProviderExecutionScope
    request_id: str | None = Field(default=None, alias="requestId")
    retry_count: int = Field(default=0, alias="retryCount", ge=0, le=10)
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("provider_name", "operation")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("provider execution field must be non-empty")
        return clean

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _coerce_refs(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(str(item) for item in value)
        return ()


class ProviderExecutionAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    production_provider_call: Literal[False] = Field(default=False, alias="productionProviderCall")
    network_fetched: Literal[False] = Field(default=False, alias="networkFetched")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    user_visible_output: Literal[False] = Field(default=False, alias="userVisibleOutput")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    def __getitem__(self, key: str) -> bool:
        return self.model_dump(by_alias=True)[key]

    @field_serializer(
        "provider_called",
        "production_provider_call",
        "network_fetched",
        "route_attached",
        "user_visible_output",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ProviderExecutionResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: ProviderExecutionStatus
    provider_called: bool = Field(default=False, alias="providerCalled")
    receipt: ProviderReceipt
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: ProviderExecutionAuthorityFlags = Field(
        default_factory=ProviderExecutionAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ProviderExecutionAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(str(key), str(key)): value for key, value in update.items()})
        data["authority_flags"] = ProviderExecutionAuthorityFlags()
        _ = deep
        return type(self).model_validate(data)


class ProviderExecutionBoundary:
    """Default-off provider execution boundary for injected test providers."""

    def __init__(self, config: ProviderExecutionConfig) -> None:
        self.config = config

    async def execute(
        self,
        request: ProviderExecutionRequest,
        *,
        provider: ProviderPort | None = None,
    ) -> ProviderExecutionResult:
        diagnostics = _diagnostics(self.config, request)
        if not self.config.enabled:
            return _result(
                request,
                "disabled",
                ("provider_execution_disabled",),
                diagnostics,
                receipt_status="disabled",
            )

        gate_error = _provider_gate_error(self.config, request, provider)
        if gate_error is not None:
            return _result(
                request,
                "blocked",
                (gate_error,),
                diagnostics,
                receipt_status="blocked",
            )

        started = time.perf_counter()
        try:
            assert provider is not None
            raw_response = provider.execute(request)
            if inspect.isawaitable(raw_response):
                raw_response = await raw_response
        except Exception as exc:
            duration_ms = _duration_ms(started)
            return _result(
                request,
                "error",
                ("provider_execution_error",),
                {**diagnostics, "providerError": _safe_error(exc)},
                receipt_status="error",
                response_payload={"error": _safe_error(exc)},
                duration_ms=duration_ms,
                provider_called=True,
            )

        duration_ms = _duration_ms(started)
        return _result(
            request,
            "ok",
            (),
            diagnostics,
            receipt_status="ok",
            response_payload=raw_response,
            duration_ms=duration_ms,
            provider_called=True,
        )

    def execute_sync(
        self,
        request: ProviderExecutionRequest,
        *,
        provider: ProviderPort | None = None,
    ) -> ProviderExecutionResult:
        """J-9: synchronous entry point mirroring :meth:`execute`.

        The :class:`channels.dispatcher.ChannelDispatcher` path is
        effectively sync — its provider port is synchronous and never
        returns awaitables. Pre-J-9 the dispatcher invoked the async
        :meth:`execute` and drove the coroutine manually via
        ``coro.send(None)`` + ``StopIteration.value``, papered over by
        ``# type: ignore[attr-defined]``. That only worked because the
        await branch never fired; a real ``async def`` provider would
        have silently misbehaved.

        ``execute_sync`` is the same logic without the await branch:
        the gate / disabled / error paths are identical to :meth:`execute`,
        and the success path requires the provider to return a non-
        awaitable value (an awaitable returned here is classified as a
        ``provider_execution_error`` rather than silently discarded).
        """

        diagnostics = _diagnostics(self.config, request)
        if not self.config.enabled:
            return _result(
                request,
                "disabled",
                ("provider_execution_disabled",),
                diagnostics,
                receipt_status="disabled",
            )

        gate_error = _provider_gate_error(self.config, request, provider)
        if gate_error is not None:
            return _result(
                request,
                "blocked",
                (gate_error,),
                diagnostics,
                receipt_status="blocked",
            )

        started = time.perf_counter()
        try:
            assert provider is not None
            raw_response = provider.execute(request)
            if inspect.isawaitable(raw_response):
                # A real async provider must use ``execute`` (not
                # ``execute_sync``). Surface this as an error rather
                # than silently swallowing the awaitable.
                raise TypeError(
                    "execute_sync called with an async provider; "
                    "use ``await execute(...)`` instead."
                )
        except Exception as exc:
            duration_ms = _duration_ms(started)
            return _result(
                request,
                "error",
                ("provider_execution_error",),
                {**diagnostics, "providerError": _safe_error(exc)},
                receipt_status="error",
                response_payload={"error": _safe_error(exc)},
                duration_ms=duration_ms,
                provider_called=True,
            )

        duration_ms = _duration_ms(started)
        return _result(
            request,
            "ok",
            (),
            diagnostics,
            receipt_status="ok",
            response_payload=raw_response,
            duration_ms=duration_ms,
            provider_called=True,
        )


def _provider_gate_error(
    config: ProviderExecutionConfig,
    request: ProviderExecutionRequest,
    provider: ProviderPort | None,
) -> str | None:
    if provider is None:
        return "provider_missing"
    if getattr(provider, "openmagi_local_fake_provider", False) is True:
        if not config.local_fake_provider_enabled:
            return "local_fake_provider_disabled"
        return None
    if not config.production_provider_calls_enabled:
        return "production_provider_calls_disabled"
    if not request.scope.selected_scope:
        return "selected_scope_required"
    if not config.provider_allowlist or request.provider_name not in config.provider_allowlist:
        return "provider_not_allowlisted"
    return None


def _result(
    request: ProviderExecutionRequest,
    status: ProviderExecutionStatus,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    receipt_status: ProviderReceiptStatus,
    response_payload: object | None = None,
    duration_ms: int = 0,
    provider_called: bool = False,
) -> ProviderExecutionResult:
    receipt = build_provider_receipt(
        provider_name=request.provider_name,
        operation=request.operation,
        status=receipt_status,
        request_payload=request.payload,
        response_payload=response_payload,
        duration_ms=duration_ms,
        retry_count=request.retry_count,
        evidence_refs=request.evidence_refs,
    )
    return ProviderExecutionResult(
        status=status,
        providerCalled=provider_called,
        receipt=receipt,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_diagnostics(diagnostics),
        authorityFlags=ProviderExecutionAuthorityFlags(),
    )


def _diagnostics(
    config: ProviderExecutionConfig,
    request: ProviderExecutionRequest,
) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionProviderCallsEnabled": config.production_provider_calls_enabled,
        "selectedScopeRequired": config.selected_scope_required,
        "selectedScope": request.scope.selected_scope,
        "environmentRef": _diagnostic_ref(request.scope.environment, "environment"),
        "providerRef": _diagnostic_ref(request.provider_name, "provider"),
        "operationRef": _diagnostic_ref(request.operation, "operation"),
    }


def _safe_diagnostics(diagnostics: Mapping[str, object]) -> dict[str, object]:
    sanitized = sanitize_provider_payload(diagnostics)
    if isinstance(sanitized, Mapping):
        return dict(sanitized)
    return {}


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _diagnostic_ref(value: str, prefix: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _safe_error(exc: BaseException) -> str:
    _ = exc
    return "[redacted-provider-error]"


__all__ = [
    "ProviderExecutionAuthorityFlags",
    "ProviderExecutionBoundary",
    "ProviderExecutionConfig",
    "ProviderExecutionRequest",
    "ProviderExecutionResult",
    "ProviderExecutionScope",
    "ProviderExecutionStatus",
    "ProviderPort",
]
