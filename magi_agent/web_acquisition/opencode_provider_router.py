from __future__ import annotations

from collections.abc import Mapping
import hashlib
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import content_digest, redact_public_text
from magi_agent.web_acquisition.provider_boundary import (
    LocalWebAcquisitionRuntime,
    WebAcquisitionConfig,
)
from magi_agent.web_acquisition.research_tools import (
    LocalWebResearchToolBoundary,
)


OpenCodeWebProfileKey = Literal[
    "scout_repo_fixture",
    "scout_external_repo",
    "scout_web_docs",
]
OpenCodeWebRouterStatus = Literal["disabled", "blocked", "ready"]

OPENCODE_WEB_FAKE_PROVIDER_ID = "openmagi.web-acquisition.fake.opencode"
OPENCODE_WEB_FIXTURE_TOOL_NAMES: tuple[str, ...] = (
    "FixtureWebSearch",
    "FixtureWebFetch",
)
_FIXTURE_TO_LOCAL_TOOL_NAMES: Mapping[str, str] = MappingProxyType(
    {
        "FixtureWebSearch": "WebSearch",
        "FixtureWebFetch": "WebFetch",
    }
)
_CLAIM_METADATA_KEY_PARTS = frozenset(
    {
        "action",
        "method",
        "operation",
        "route",
        "tool",
        "toolname",
    }
)
_DIRECT_WEB_TOOL_VALUE_CLAIMS = frozenset({"websearch", "webfetch"})
_PROVIDER_HANDLE_NONCE = object()
_ATTACHMENT_FLAGS: Mapping[str, bool] = MappingProxyType(
    {
        "adkRunnerInvoked": False,
        "liveToolDispatched": False,
        "networkFetched": False,
        "browserExecuted": False,
        "rawContentInjected": False,
        "parentContextInjected": False,
        "productionAuthority": False,
        "routeAttached": False,
    }
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class OpenCodeLocalFixtureWebProvider:
    openmagi_local_fake_provider: Literal[True] = True

    def __init__(
        self,
        *,
        search_payload: Mapping[str, object] | None = None,
        fetch_payload: Mapping[str, object] | None = None,
    ) -> None:
        self._search_payload = dict(search_payload or _default_search_payload())
        self._fetch_payload = dict(fetch_payload or _default_fetch_payload())
        self.calls: list[str] = []

    async def search(self, request: object) -> Mapping[str, object]:
        self.calls.append("search")
        return self._search_payload

    async def fetch(self, request: object) -> Mapping[str, object]:
        self.calls.append("fetch")
        return self._fetch_payload

    def payload_for_fixture_tool(self, tool_name: str) -> Mapping[str, object]:
        if tool_name == "FixtureWebSearch":
            return self._search_payload
        if tool_name == "FixtureWebFetch":
            return self._fetch_payload
        return {}


class OpenCodeFixtureProviderHandle:
    __slots__ = ("_provider", "_nonce")

    def __init__(self, provider: OpenCodeLocalFixtureWebProvider, nonce: object) -> None:
        self._provider = provider
        self._nonce = nonce


class OpenCodeFixtureWebResearchToolBoundary:
    fixture_only: Literal[True] = True
    tool_host_execution_allowed: Literal[False] = False
    live_authority_allowed: Literal[False] = False

    def __init__(
        self,
        *,
        boundary: LocalWebResearchToolBoundary,
        provider: OpenCodeLocalFixtureWebProvider,
    ) -> None:
        self._boundary = boundary
        self._provider = provider

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None = None,
    ) -> ToolResult:
        if tool_name not in _FIXTURE_TO_LOCAL_TOOL_NAMES:
            return _blocked_tool_result(tool_name, "opencode_fixture_tool_required")
        result = await self._boundary.execute_tool(
            _FIXTURE_TO_LOCAL_TOOL_NAMES[tool_name],
            arguments,
            context,
        )
        if result.status != "ok":
            return result
        if _has_url_only_source_evidence(result):
            return _blocked_tool_result(tool_name, "opencode_url_only_source_evidence_blocked")
        if not _payload_has_content_backing(
            self._provider.payload_for_fixture_tool(tool_name),
            tool_name=tool_name,
        ):
            return _blocked_tool_result(tool_name, "opencode_empty_source_evidence_blocked")
        return _rewrite_runtime_refs(
            result,
            fixture_tool_name=tool_name,
            context=context,
        )


class OpenCodeWebProviderRouterDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: OpenCodeWebRouterStatus
    profile_key: OpenCodeWebProfileKey = Field(alias="profileKey")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    tool_names: tuple[str, ...] = Field(default=(), alias="toolNames")
    provider_id: str = Field(default=OPENCODE_WEB_FAKE_PROVIDER_ID, alias="providerId")
    web_acquisition_config: WebAcquisitionConfig = Field(alias="webAcquisitionConfig")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    local_fake_provider_route_allowed: bool = Field(
        default=False,
        alias="localFakeProviderRouteAllowed",
    )
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(
        default=False,
        alias="functionToolAttached",
    )
    toolhost_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostExecutionAllowed",
    )
    live_provider_calls_allowed: Literal[False] = Field(
        default=False,
        alias="liveProviderCallsAllowed",
    )
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    model_calls_allowed: Literal[False] = Field(default=False, alias="modelCallsAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    adk_tools: tuple[object, ...] = Field(default=(), alias="adkTools")
    attachment_flags: Mapping[str, bool] = Field(
        default_factory=lambda: dict(_ATTACHMENT_FLAGS),
        alias="attachmentFlags",
    )

    @model_validator(mode="after")
    def _validate_decision(self) -> "OpenCodeWebProviderRouterDecision":
        ready = self.status == "ready"
        if self.provider_id != OPENCODE_WEB_FAKE_PROVIDER_ID:
            raise ValueError("OpenCode web providerId must be the local fake provider")
        if self.tool_names != _expected_tool_names(self.status, self.profile_key):
            raise ValueError("OpenCode web toolNames do not match profile status")
        if self.reason_codes not in _allowed_reason_codes(
            status=self.status,
            profile_key=self.profile_key,
        ):
            raise ValueError("OpenCode web reasonCodes are not allowed for profile status")
        if self.local_fake_provider_route_allowed is not ready:
            raise ValueError("OpenCode web fake-provider route flag must match ready status")
        if _config_dump(self.web_acquisition_config) != _config_dump(_config_for_status(ready)):
            raise ValueError("OpenCode web webAcquisitionConfig must be deterministic")
        if self.adk_tools:
            raise ValueError("OpenCode web router must not attach ADK tools")
        if dict(self.attachment_flags) != dict(_ATTACHMENT_FLAGS):
            raise ValueError("OpenCode web attachmentFlags must remain false")
        object.__setattr__(
            self,
            "attachment_flags",
            MappingProxyType(dict(_ATTACHMENT_FLAGS)),
        )
        return self

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, value: Mapping[str, bool]) -> dict[str, bool]:
        return dict(value)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return _revalidated_model_copy(self, update=update)


def materialize_opencode_web_provider_router(
    *,
    profile_key: OpenCodeWebProfileKey = "scout_web_docs",
    rollout_enabled: bool = False,
    fake_provider_boundary_enabled: bool = False,
    local_fake_provider_available: bool = False,
) -> OpenCodeWebProviderRouterDecision:
    if not rollout_enabled:
        return _decision(
            status="disabled",
            profile_key=profile_key,
            reason_codes=("rollout_gate_disabled",),
        )
    if profile_key == "scout_repo_fixture":
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("profile_has_no_web_tools",),
        )
    if profile_key == "scout_external_repo":
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("live_network_not_allowed",),
        )
    if not fake_provider_boundary_enabled:
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("fake_provider_boundary_disabled",),
        )
    if not local_fake_provider_available:
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("local_fake_provider_missing",),
        )
    return _decision(
        status="ready",
        profile_key=profile_key,
        reason_codes=("fake_provider_route_ready",),
    )


def build_opencode_web_research_tool_boundary(
    decision: OpenCodeWebProviderRouterDecision,
    *,
    provider_handle: object | None,
) -> OpenCodeFixtureWebResearchToolBoundary | None:
    if decision.status != "ready" or not _valid_provider_handle(provider_handle):
        return None
    runtime = LocalWebAcquisitionRuntime(
        decision.web_acquisition_config,
        provider=provider_handle._provider,
    )
    return OpenCodeFixtureWebResearchToolBoundary(
        boundary=LocalWebResearchToolBoundary(runtime=runtime),
        provider=provider_handle._provider,
    )


def issue_opencode_fixture_provider_handle(
    provider: OpenCodeLocalFixtureWebProvider,
) -> OpenCodeFixtureProviderHandle:
    if type(provider) is not OpenCodeLocalFixtureWebProvider:
        raise ValueError("provider must be a sealed OpenCode fixture provider")
    return OpenCodeFixtureProviderHandle(provider, _PROVIDER_HANDLE_NONCE)


def _decision(
    *,
    status: OpenCodeWebRouterStatus,
    profile_key: OpenCodeWebProfileKey,
    reason_codes: tuple[str, ...],
) -> OpenCodeWebProviderRouterDecision:
    ready = status == "ready"
    return OpenCodeWebProviderRouterDecision(
        status=status,
        profileKey=profile_key,
        reasonCodes=reason_codes,
        toolNames=_expected_tool_names(status, profile_key),
        providerId=OPENCODE_WEB_FAKE_PROVIDER_ID,
        webAcquisitionConfig=_config_for_status(ready),
        localFakeProviderRouteAllowed=ready,
        adkTools=(),
        attachmentFlags=dict(_ATTACHMENT_FLAGS),
    )


def _expected_tool_names(
    status: OpenCodeWebRouterStatus,
    profile_key: OpenCodeWebProfileKey,
) -> tuple[str, ...]:
    if status == "ready" and profile_key == "scout_web_docs":
        return OPENCODE_WEB_FIXTURE_TOOL_NAMES
    return ()


def _allowed_reason_codes(
    *,
    status: OpenCodeWebRouterStatus,
    profile_key: OpenCodeWebProfileKey,
) -> tuple[tuple[str, ...], ...]:
    if status == "disabled":
        return (("rollout_gate_disabled",),)
    if status == "ready" and profile_key == "scout_web_docs":
        return (("fake_provider_route_ready",),)
    if profile_key == "scout_repo_fixture":
        return (("profile_has_no_web_tools",),)
    if profile_key == "scout_external_repo":
        return (("live_network_not_allowed",),)
    return (
        ("fake_provider_boundary_disabled",),
        ("local_fake_provider_missing",),
    )


def _config_for_status(ready: bool) -> WebAcquisitionConfig:
    return WebAcquisitionConfig(
        enabled=ready,
        localFakeProviderEnabled=ready,
        providerId=OPENCODE_WEB_FAKE_PROVIDER_ID,
    )


def _config_dump(config: WebAcquisitionConfig) -> dict[str, object]:
    return config.model_dump(by_alias=True, mode="python", warnings=False)


def _valid_provider_handle(value: object | None) -> bool:
    return (
        isinstance(value, OpenCodeFixtureProviderHandle)
        and value._nonce is _PROVIDER_HANDLE_NONCE
        and type(value._provider) is OpenCodeLocalFixtureWebProvider
    )


def _blocked_tool_result(tool_name: str, error_code: str) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode=error_code,
        metadata={
            "toolName": tool_name,
            "boundaryStatus": "blocked",
            "attachmentFlags": dict(_ATTACHMENT_FLAGS),
        },
    )


def _has_url_only_source_evidence(result: ToolResult) -> bool:
    output = result.output
    if not isinstance(output, Mapping):
        return True
    sources = output.get("sources")
    if not isinstance(sources, list | tuple) or not sources:
        return True
    for source in sources:
        if not isinstance(source, Mapping):
            return True
        url_ref = source.get("urlRef")
        digest = source.get("contentDigest")
        if not isinstance(url_ref, str) or not isinstance(digest, str):
            return True
        if digest == content_digest(url_ref):
            return True
    return False


def _payload_has_content_backing(
    payload: Mapping[str, object],
    *,
    tool_name: str,
) -> bool:
    if tool_name == "FixtureWebSearch":
        results = payload.get("results") or payload.get("sources")
        if not isinstance(results, list | tuple):
            return False
        result_items = [item for item in results if isinstance(item, Mapping)]
        if not result_items:
            return False
        return all(
            isinstance(item, Mapping) and _mapping_has_meaningful_content(item)
            for item in result_items
        )
    if tool_name == "FixtureWebFetch":
        return _mapping_has_meaningful_content(payload)
    return False


def _mapping_has_meaningful_content(item: Mapping[str, object]) -> bool:
    return any(
        _is_meaningful_public_content(item.get(key))
        for key in ("content", "body", "text", "snippet", "preview")
    )


def _is_meaningful_public_content(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    public_text = redact_public_text(value).strip()
    if not public_text:
        return False
    placeholder_stripped = (
        public_text.replace("[redacted-path]", "")
        .replace("[redacted-url]", "")
        .replace("[redacted]", "")
        .strip()
    )
    return bool(placeholder_stripped)


def _rewrite_runtime_refs(
    result: ToolResult,
    *,
    fixture_tool_name: str,
    context: object | None,
) -> ToolResult:
    output = result.output
    if not isinstance(output, Mapping):
        return result
    rewritten_output = dict(output)
    rewritten_output["toolName"] = fixture_tool_name
    sources = output.get("sources")
    if not isinstance(sources, list | tuple):
        return result
    provider_id = str(output.get("providerId") or OPENCODE_WEB_FAKE_PROVIDER_ID)
    turn_id = _context_text(context, "turn_id", "turnId") or "turn-local"
    rewritten_sources: list[dict[str, object]] = []
    source_refs: list[str] = []
    evidence_refs: list[str] = []
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, Mapping):
            continue
        rewritten_source = dict(source)
        source_digest = str(source.get("contentDigest") or "")
        stable_id = hashlib.sha256(
            f"{turn_id}|{provider_id}|{source_digest}|{index}".encode("utf-8")
        ).hexdigest()[:16]
        source_ref = f"source:web:{stable_id}"
        evidence_ref = f"evidence:web:{stable_id}"
        rewritten_source["sourceRef"] = source_ref
        rewritten_source["evidenceRef"] = evidence_ref
        rewritten_source["metadata"] = _sanitize_nested_source_metadata(
            rewritten_source.get("metadata")
        )
        rewritten_sources.append(rewritten_source)
        source_refs.append(source_ref)
        evidence_refs.append(evidence_ref)
    rewritten_output["sources"] = rewritten_sources
    if "resultRefs" in rewritten_output:
        rewritten_output["resultRefs"] = source_refs
    if "inspectedSourceRefs" in rewritten_output:
        rewritten_output["inspectedSourceRefs"] = source_refs

    transcript_output = result.transcript_output
    if isinstance(transcript_output, Mapping):
        rewritten_transcript: Mapping[str, object] | None = {
            **dict(transcript_output),
            "toolName": fixture_tool_name,
            "resultRefs": source_refs,
        }
    else:
        rewritten_transcript = None
    metadata = dict(result.metadata or {})
    metadata["toolName"] = fixture_tool_name
    metadata["parentOutputRefs"] = [ref for pair in zip(source_refs, evidence_refs, strict=False) for ref in pair]
    return ToolResult(
        status=result.status,
        output=rewritten_output,
        llmOutput=rewritten_output,
        transcriptOutput=rewritten_transcript,
        metadata=metadata,
    )


def _context_text(context: object | None, *names: str) -> str | None:
    if context is None:
        return None
    for name in names:
        value = getattr(context, name, None)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(context, Mapping):
        for name in names:
            value = context.get(name)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _sanitize_nested_source_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    sanitized: dict[str, object] = {}
    for key, item in value.items():
        normalized_key = "".join(ch for ch in str(key).casefold() if ch.isalnum())
        if any(part in normalized_key for part in _CLAIM_METADATA_KEY_PARTS):
            continue
        if isinstance(item, str):
            normalized_value = "".join(ch for ch in item.casefold() if ch.isalnum())
            if any(claim in normalized_value for claim in _DIRECT_WEB_TOOL_VALUE_CLAIMS):
                continue
            sanitized[str(key)] = item
        elif isinstance(item, bool | int | float) or item is None:
            sanitized[str(key)] = item
    return sanitized


def _default_search_payload() -> Mapping[str, object]:
    return {
        "results": [
            {
                "title": "OpenCode Fixture Docs",
                "url": "https://docs.example.com/opencode",
                "snippet": "Fixture-backed OpenCode web search result.",
            }
        ],
        "preview": "Fixture-backed OpenCode web search result.",
    }


def _default_fetch_payload() -> Mapping[str, object]:
    return {
        "url": "https://docs.example.com/opencode",
        "title": "OpenCode Fixture Docs",
        "content": "Fixture-backed OpenCode web fetch result.",
        "metadata": {"status": 200, "contentType": "text/html"},
    }


def _alias_updates(model_class: type[BaseModel], update: Mapping[str, Any]) -> dict[str, Any]:
    alias_to_name = {
        field.alias: name
        for name, field in model_class.model_fields.items()
        if field.alias is not None
    }
    return {alias_to_name.get(key, key): value for key, value in update.items()}


def _revalidated_model_copy(
    model: BaseModel,
    *,
    update: Mapping[str, Any] | None,
) -> Any:
    data = model.model_dump(by_alias=False, mode="python", warnings=False)
    if update:
        data.update(_alias_updates(model.__class__, update))
    return model.__class__.model_validate(data)


__all__ = [
    "OPENCODE_WEB_FAKE_PROVIDER_ID",
    "OPENCODE_WEB_FIXTURE_TOOL_NAMES",
    "OpenCodeFixtureProviderHandle",
    "OpenCodeFixtureWebResearchToolBoundary",
    "OpenCodeLocalFixtureWebProvider",
    "OpenCodeWebProviderRouterDecision",
    "OpenCodeWebProfileKey",
    "OpenCodeWebRouterStatus",
    "build_opencode_web_research_tool_boundary",
    "issue_opencode_fixture_provider_handle",
    "materialize_opencode_web_provider_router",
]
