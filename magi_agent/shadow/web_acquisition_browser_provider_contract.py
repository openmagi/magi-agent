from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal, Self, TypeAlias
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.transport.tool_preview import sanitize_tool_preview


WebAcquisitionPhase: TypeAlias = Literal[
    "web_search",
    "fetch",
    "reader/Jina-style extraction",
    "metadata/JSON-LD extraction",
    "browser snapshot/scrape fallback",
    "source identity normalization",
    "content quality scoring",
    "retry/fallback strategy",
    "timeout/budget/domain policy",
    "redaction/public preview",
    "source/evidence ledger record creation",
    "opened/observed proof",
]
BrowserCapability: TypeAlias = Literal[
    "browser.open",
    "browser.snapshot",
    "browser.scrape",
    "browser.click",
    "browser.fill",
    "browser.scroll",
    "browser.screenshot",
]
WebAcquisitionCaseCategory: TypeAlias = Literal[
    "source_record_digest_and_observation",
    "browser_fallback_evidence_metadata",
    "blocked_local_metadata_cluster_urls",
    "no_auth_bypass",
    "no_captcha_solving",
    "no_private_data_scraping",
    "sanitized_parent_refs_only",
    "research_recipe_dependency",
]
WebAcquisitionDecision: TypeAlias = Literal["allow_metadata_only", "block"]
WebAcquisitionMethod: TypeAlias = Literal[
    "web.search",
    "web.fetch",
    "reader.extract",
    "metadata.jsonld",
    "browser.open",
    "browser.snapshot",
    "browser.scrape",
    "browser.click",
    "browser.fill",
    "browser.scroll",
    "browser.screenshot",
]
WebAcquisitionProvider: TypeAlias = Literal[
    "openmagi.web-acquisition.system",
    "openmagi.browser-provider.system",
]
WebAcquisitionProofType: TypeAlias = Literal["opened", "observed"]
BlockedUrlClass: TypeAlias = Literal["local", "metadata", "cluster"]
BrowserApprovalReason: TypeAlias = Literal[
    "forms",
    "downloads",
    "authenticated_flows",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_REQUIRED_PHASES: tuple[WebAcquisitionPhase, ...] = (
    "web_search",
    "fetch",
    "reader/Jina-style extraction",
    "metadata/JSON-LD extraction",
    "browser snapshot/scrape fallback",
    "source identity normalization",
    "content quality scoring",
    "retry/fallback strategy",
    "timeout/budget/domain policy",
    "redaction/public preview",
    "source/evidence ledger record creation",
    "opened/observed proof",
)
_REQUIRED_BROWSER_CAPABILITIES: tuple[BrowserCapability, ...] = (
    "browser.open",
    "browser.snapshot",
    "browser.scrape",
    "browser.click",
    "browser.fill",
    "browser.scroll",
    "browser.screenshot",
)
_REQUIRED_BLOCKED_URL_CLASSES: tuple[BlockedUrlClass, ...] = (
    "local",
    "metadata",
    "cluster",
)
_REQUIRED_BROWSER_APPROVALS: tuple[BrowserApprovalReason, ...] = (
    "forms",
    "downloads",
    "authenticated_flows",
)
_REQUIRED_CATEGORIES = set(WebAcquisitionCaseCategory.__args__)  # type: ignore[attr-defined]
_SOURCE_REF_RE = re.compile(r"^source:(?:web|browser):src_[1-9][0-9]*$")
_EVIDENCE_REF_RE = re.compile(r"^evidence:(?:web|browser):src_[1-9][0-9]*$")
_PARENT_REF_RE = re.compile(r"^(?:source|evidence):(?:web|browser):src_[1-9][0-9]*$")
_ARTIFACT_REF_RE = re.compile(r"^artifact:browser-snapshot:src_[1-9][0-9]*$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])data[\\/]bots(?:[\\/]|$)|"
    r"(?:^|[\\/])var[\\/]lib[\\/]kubelet(?:[\\/]|$)|"
    r"(?:^|[\\/])data[\\/]workspace[\\/][^\s\"'}]*pvc[^\s\"'}]*|"
    r"(?:^|[\s\"'=:`])[/\\]workspace(?:[/\\](?:bot[-_][^\\/\\s\"'}]+|bots?|"
    r"pvc[-_][^\\/\\s\"'}]+|private[^\\/\\s\"'}]*|core-agent)(?:[\\/]|$)|[\\/]?$)|"
    r"(?:^|[\\/])mnt[\\/]pvc(?:[\\/]|$)|"
    r"(?:^|[\\/])pvc(?:[\\/]|$)|"
    r"supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_SECRET_SHAPED_VALUE_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY)[A-Z0-9_]*\s*[:=]\s*[^,\s}{]{4,})",
    re.IGNORECASE,
)
_BLOCKED_URL_TEXT_RE = re.compile(
    r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|169\.254\.169\.254|"
    r"kubernetes\.default\.svc|\.cluster\.local|\.svc(?:/|:|$))",
    re.IGNORECASE,
)
_URL_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "SUPABASE_SERVICE_ROLE_KEY",
    "hidden reasoning",
    "private dashboard data",
    "raw browser snapshot",
    "raw tool log",
    "raw transcript",
)
_FORBIDDEN_TRUE_KEYS = frozenset(
    {
        "adk_runner_invoked",
        "auth_bypass_allowed",
        "auth_bypass_attempted",
        "browser_executed",
        "browser_worker_attached",
        "captcha_solved",
        "captcha_solving_allowed",
        "cdp_session_attached",
        "core_runtime",
        "live_tool_dispatched",
        "network_fetched",
        "parent_context_injected",
        "parent_context_raw_injection",
        "private_data_scraped",
        "private_data_scraping_allowed",
        "production_authority",
        "raw_browser_snapshot_injected",
        "raw_content_included",
        "raw_snapshot_injected",
        "raw_tool_log_injected",
        "raw_tool_logs_injected",
        "raw_transcript_included",
        "route_or_api_attached",
    }
)


class WebAcquisitionAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    network_fetched: Literal[False] = Field(default=False, alias="networkFetched")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    browser_worker_attached: Literal[False] = Field(
        default=False,
        alias="browserWorkerAttached",
    )
    cdp_session_attached: Literal[False] = Field(default=False, alias="cdpSessionAttached")
    raw_snapshot_injected: Literal[False] = Field(
        default=False,
        alias="rawSnapshotInjected",
    )
    raw_tool_log_injected: Literal[False] = Field(
        default=False,
        alias="rawToolLogInjected",
    )
    parent_context_injected: Literal[False] = Field(
        default=False,
        alias="parentContextInjected",
    )
    auth_bypass_attempted: Literal[False] = Field(
        default=False,
        alias="authBypassAttempted",
    )
    captcha_solved: Literal[False] = Field(default=False, alias="captchaSolved")
    private_data_scraped: Literal[False] = Field(
        default=False,
        alias="privateDataScraped",
    )
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "network_fetched",
        "browser_executed",
        "browser_worker_attached",
        "cdp_session_attached",
        "raw_snapshot_injected",
        "raw_tool_log_injected",
        "parent_context_injected",
        "auth_bypass_attempted",
        "captcha_solved",
        "private_data_scraped",
        "route_or_api_attached",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class WebAcquisitionObservedProof(BaseModel):
    model_config = _MODEL_CONFIG

    proof_type: WebAcquisitionProofType = Field(alias="proofType")
    observed_at: str = Field(alias="observedAt")
    evidence_ref: str = Field(alias="evidenceRef")

    @model_validator(mode="after")
    def _validate_proof(self) -> Self:
        _validate_public_string(self.observed_at)
        if _EVIDENCE_REF_RE.fullmatch(self.evidence_ref) is None:
            raise ValueError("proof evidenceRef must be a sanitized evidence ref")
        return self


class WebAcquisitionSourceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    method: WebAcquisitionMethod
    provider: WebAcquisitionProvider
    url: str
    normalized_url: str = Field(alias="normalizedUrl")
    content_digest: str = Field(alias="contentDigest")
    proof: WebAcquisitionObservedProof
    public_preview: str = Field(alias="publicPreview")
    raw_content_included: Literal[False] = Field(
        default=False,
        alias="rawContentIncluded",
    )
    raw_transcript_included: Literal[False] = Field(
        default=False,
        alias="rawTranscriptIncluded",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_record(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_record(self) -> Self:
        if _SOURCE_REF_RE.fullmatch(self.source_ref) is None:
            raise ValueError("sourceRef must be a sanitized source ref")
        _validate_external_public_url(self.url)
        _validate_external_public_url(self.normalized_url)
        if self.normalized_url != _canonical_url(self.url):
            raise ValueError("normalizedUrl must match canonical source URL")
        if _SHA256_RE.fullmatch(self.content_digest) is None:
            raise ValueError("contentDigest must be sha256-prefixed lowercase hex")
        if self.method.startswith("browser."):
            if self.provider != "openmagi.browser-provider.system":
                raise ValueError("browser methods require browser provider metadata")
            if not self.source_ref.startswith("source:browser:"):
                raise ValueError("browser source records require browser source refs")
        else:
            if self.provider != "openmagi.web-acquisition.system":
                raise ValueError("web acquisition methods require web provider metadata")
            if not self.source_ref.startswith("source:web:"):
                raise ValueError("web acquisition source records require web source refs")
        _validate_public_string(self.public_preview)
        return self


class BrowserProviderContract(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: Literal["openmagi.browser-provider.system"] = Field(alias="providerId")
    classification: Literal["first_party_system_plugin_provider"]
    core_runtime: Literal[False] = Field(default=False, alias="coreRuntime")
    capabilities: tuple[BrowserCapability, ...]
    session_isolation: Literal["ephemeral_per_turn"] = Field(alias="sessionIsolation")
    worker_boundary: Literal["CDP/browser-worker boundary"] = Field(alias="workerBoundary")
    blocked_url_classes: tuple[BlockedUrlClass, ...] = Field(alias="blockedUrlClasses")
    screenshot_artifact_policy: Literal["sanitized_artifact_ref_only"] = Field(
        alias="screenshotArtifactPolicy",
    )
    timeout_budget_policy: Literal["per_call_timeout_and_per_turn_budget"] = Field(
        alias="timeoutBudgetPolicy",
    )
    approval_required_for: tuple[BrowserApprovalReason, ...] = Field(
        alias="approvalRequiredFor",
    )
    attachment_flags: WebAcquisitionAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_provider(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_provider(self) -> Self:
        if self.capabilities != _REQUIRED_BROWSER_CAPABILITIES:
            raise ValueError("browser provider must declare the required capability set")
        if self.blocked_url_classes != _REQUIRED_BLOCKED_URL_CLASSES:
            raise ValueError("browser provider must block local, metadata, and cluster URLs")
        if self.approval_required_for != _REQUIRED_BROWSER_APPROVALS:
            raise ValueError("browser provider must require approval for forms/downloads/auth")
        return self


class WebAcquisitionCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: WebAcquisitionCaseCategory
    decision: WebAcquisitionDecision
    public_preview: str = Field(alias="publicPreview")
    source_records: tuple[WebAcquisitionSourceRecord, ...] = Field(alias="sourceRecords")
    evidence_refs: tuple[str, ...] = Field(alias="evidenceRefs")
    parent_output_refs: tuple[str, ...] = Field(alias="parentOutputRefs")
    raw_browser_snapshot_ref: str | None = Field(default=None, alias="rawBrowserSnapshotRef")
    raw_browser_snapshot_injected: Literal[False] = Field(
        default=False,
        alias="rawBrowserSnapshotInjected",
    )
    raw_tool_logs_injected: Literal[False] = Field(
        default=False,
        alias="rawToolLogsInjected",
    )
    parent_context_raw_injection: Literal[False] = Field(
        default=False,
        alias="parentContextRawInjection",
    )
    auth_bypass_allowed: Literal[False] = Field(default=False, alias="authBypassAllowed")
    captcha_solving_allowed: Literal[False] = Field(
        default=False,
        alias="captchaSolvingAllowed",
    )
    private_data_scraping_allowed: Literal[False] = Field(
        default=False,
        alias="privateDataScrapingAllowed",
    )
    blocked_url_classes: tuple[BlockedUrlClass, ...] = Field(alias="blockedUrlClasses")
    approval_required_for: tuple[BrowserApprovalReason, ...] = Field(
        alias="approvalRequiredFor",
    )
    recipe_dependencies: tuple[str, ...] = Field(alias="recipeDependencies")
    citations_added_by_recipe: bool = Field(alias="citationsAddedByRecipe")
    fact_grounding_added_by_recipe: bool = Field(alias="factGroundingAddedByRecipe")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    attachment_flags: WebAcquisitionAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        for value in (
            self.case_id,
            self.public_preview,
            *(self.evidence_refs),
            *(self.parent_output_refs),
            *(self.recipe_dependencies),
            *(self.reason_codes),
        ):
            _validate_public_string(value)
        if not self.reason_codes:
            raise ValueError("web acquisition cases require reasonCodes")
        for evidence_ref in self.evidence_refs:
            if _EVIDENCE_REF_RE.fullmatch(evidence_ref) is None:
                raise ValueError("evidenceRefs must be sanitized evidence refs")
        for output_ref in self.parent_output_refs:
            if _PARENT_REF_RE.fullmatch(output_ref) is None:
                raise ValueError("parentOutputRefs must use sanitized source/evidence refs")
        if self.decision == "allow_metadata_only" and self.blocked_url_classes:
            raise ValueError("allow decisions cannot carry blockedUrlClasses")
        if self.decision == "block" and self.source_records:
            raise ValueError("blocked web acquisition cases cannot include source records")
        if self.raw_browser_snapshot_ref is not None:
            _validate_public_string(self.raw_browser_snapshot_ref)
            if _ARTIFACT_REF_RE.fullmatch(self.raw_browser_snapshot_ref) is None:
                raise ValueError("rawBrowserSnapshotRef must be a sanitized artifact ref")
        record_evidence_refs = {record.proof.evidence_ref for record in self.source_records}
        if record_evidence_refs and not record_evidence_refs.issubset(set(self.evidence_refs)):
            raise ValueError("source record proofs must be exposed as sanitized evidenceRefs")

        if self.category == "source_record_digest_and_observation":
            if self.decision != "allow_metadata_only" or not self.source_records:
                raise ValueError("source record digest case requires allow metadata source records")
        elif self.category == "browser_fallback_evidence_metadata":
            self._validate_browser_fallback()
        elif self.category == "blocked_local_metadata_cluster_urls":
            if self.decision != "block" or self.blocked_url_classes != _REQUIRED_BLOCKED_URL_CLASSES:
                raise ValueError("blocked URL case must block local/metadata/cluster classes")
        elif self.category == "no_auth_bypass":
            if self.decision != "block" or "authenticated_flows" not in self.approval_required_for:
                raise ValueError("auth bypass case must block and require auth-flow approval")
        elif self.category == "no_captcha_solving":
            if self.decision != "block":
                raise ValueError("captcha case must block")
        elif self.category == "no_private_data_scraping":
            if self.decision != "block" or "authenticated_flows" not in self.approval_required_for:
                raise ValueError("private data scraping case must block authenticated flows")
        elif self.category == "sanitized_parent_refs_only":
            if self.decision != "allow_metadata_only" or not self.parent_output_refs:
                raise ValueError("parent ref case requires sanitized parent refs")
        elif self.category == "research_recipe_dependency":
            if self.recipe_dependencies != ("web-acquisition",):
                raise ValueError("research recipe case must depend on web-acquisition")
            if not self.citations_added_by_recipe or not self.fact_grounding_added_by_recipe:
                raise ValueError("research recipe must add citations and fact grounding separately")
        if self.category != "research_recipe_dependency":
            if self.recipe_dependencies or self.citations_added_by_recipe or self.fact_grounding_added_by_recipe:
                raise ValueError("only research recipe dependency case can declare recipe add-ons")
        return self

    def _validate_browser_fallback(self) -> None:
        if self.decision != "allow_metadata_only":
            raise ValueError("browser fallback is metadata-only")
        if self.raw_browser_snapshot_ref is None:
            raise ValueError("browser fallback requires raw snapshot artifact ref metadata")
        methods = tuple(record.method for record in self.source_records)
        if methods != ("browser.open", "browser.snapshot", "browser.scrape"):
            raise ValueError("browser fallback must record open, snapshot, and scrape metadata")
        if self.approval_required_for != _REQUIRED_BROWSER_APPROVALS:
            raise ValueError("browser fallback must carry approval policy metadata")


class WebAcquisitionBrowserProviderFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["webAcquisitionBrowserProviderFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    future_live_surface: Literal[
        "ADK FunctionTool attachment through OpenMagi ToolHost policy"
    ] = Field(alias="futureLiveSurface")
    long_running_tool_scope: Literal[
        "individual crawl/render/export jobs only"
    ] = Field(alias="longRunningToolScope")
    acquisition_phases: tuple[WebAcquisitionPhase, ...] = Field(alias="acquisitionPhases")
    attachment_flags: WebAcquisitionAttachmentFlags = Field(alias="attachmentFlags")
    browser_provider: BrowserProviderContract = Field(alias="browserProvider")
    cases: tuple[WebAcquisitionCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        _validate_public_string(self.fixture_id)
        if self.acquisition_phases != _REQUIRED_PHASES:
            raise ValueError("web acquisition fixture must enumerate required WA-2 phases")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("web acquisition caseId values must be unique")
        categories = {case.category for case in self.cases}
        if categories != _REQUIRED_CATEGORIES:
            missing = sorted(_REQUIRED_CATEGORIES - categories)
            extra = sorted(categories - _REQUIRED_CATEGORIES)
            raise ValueError(
                "web acquisition fixture must cover every category: "
                f"missing={missing}, extra={extra}"
            )
        return self


class WebAcquisitionBrowserProviderProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    no_live_execution: Literal[True] = Field(default=True, alias="noLiveExecution")
    acquisition_phases: tuple[WebAcquisitionPhase, ...] = Field(alias="acquisitionPhases")
    browser_provider: dict[str, object] = Field(alias="browserProvider")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_decision: dict[WebAcquisitionDecision, int] = Field(alias="byDecision")
    by_category: dict[WebAcquisitionCaseCategory, int] = Field(alias="byCategory")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")
    attachment_flags: WebAcquisitionAttachmentFlags = Field(alias="attachmentFlags")


def load_web_acquisition_browser_provider_fixture(
    fixture_name: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> WebAcquisitionBrowserProviderFixture:
    resolved_path = _resolve_fixture_path(fixture_name, fixture_root=fixture_root)
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    return WebAcquisitionBrowserProviderFixture.model_validate(payload)


def project_web_acquisition_browser_provider_fixture(
    fixture: WebAcquisitionBrowserProviderFixture | Mapping[str, Any],
) -> WebAcquisitionBrowserProviderProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    public_previews = {
        case.case_id: sanitize_tool_preview(case.public_preview)
        for case in safe_fixture.cases
    }
    browser_provider = _browser_provider_snapshot(safe_fixture.browser_provider)
    case_snapshots = {
        case.case_id: _case_snapshot(case)
        for case in safe_fixture.cases
    }
    _reject_unsafe_public_snapshot(
        {
            "browserProvider": browser_provider,
            "publicPreviews": public_previews,
            "caseSnapshots": case_snapshots,
        }
    )
    return WebAcquisitionBrowserProviderProjection(
        fixtureId=safe_fixture.fixture_id,
        version=safe_fixture.version,
        localDiagnostic=True,
        metadataOnly=True,
        defaultOff=True,
        noLiveExecution=True,
        acquisitionPhases=safe_fixture.acquisition_phases,
        browserProvider=browser_provider,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byDecision=dict(Counter(case.decision for case in safe_fixture.cases)),
        byCategory=dict(Counter(case.category for case in safe_fixture.cases)),
        publicPreviews=public_previews,
        caseSnapshots=case_snapshots,
        attachmentFlags=safe_fixture.attachment_flags,
    )


def _validated_fixture_snapshot(
    fixture: WebAcquisitionBrowserProviderFixture | Mapping[str, Any],
) -> WebAcquisitionBrowserProviderFixture:
    if isinstance(fixture, WebAcquisitionBrowserProviderFixture):
        return WebAcquisitionBrowserProviderFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return WebAcquisitionBrowserProviderFixture.model_validate(fixture)


def _browser_provider_snapshot(provider: BrowserProviderContract) -> dict[str, object]:
    return {
        "providerId": provider.provider_id,
        "classification": provider.classification,
        "coreRuntime": provider.core_runtime,
        "capabilities": provider.capabilities,
        "sessionIsolation": provider.session_isolation,
        "workerBoundary": provider.worker_boundary,
        "blockedUrlClasses": provider.blocked_url_classes,
        "screenshotArtifactPolicy": provider.screenshot_artifact_policy,
        "timeoutBudgetPolicy": provider.timeout_budget_policy,
        "approvalRequiredFor": provider.approval_required_for,
    }


def _case_snapshot(case: WebAcquisitionCase) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "category": case.category,
        "decision": case.decision,
        "publicPreview": sanitize_tool_preview(case.public_preview),
        "sourceRecords": tuple(_source_record_snapshot(record) for record in case.source_records),
        "sourceRecordMethods": tuple(record.method for record in case.source_records),
        "evidenceRefs": case.evidence_refs,
        "parentOutputRefs": case.parent_output_refs,
        "blockedUrlClasses": case.blocked_url_classes,
        "approvalRequiredFor": case.approval_required_for,
        "rawBrowserSnapshotInjected": case.raw_browser_snapshot_injected,
        "rawToolLogsInjected": case.raw_tool_logs_injected,
        "parentContextRawInjection": case.parent_context_raw_injection,
        "authBypassAllowed": case.auth_bypass_allowed,
        "captchaSolvingAllowed": case.captcha_solving_allowed,
        "privateDataScrapingAllowed": case.private_data_scraping_allowed,
        "recipeDependencies": case.recipe_dependencies,
        "citationsAddedByRecipe": case.citations_added_by_recipe,
        "factGroundingAddedByRecipe": case.fact_grounding_added_by_recipe,
        "reasonCodes": case.reason_codes,
        "attachmentFlags": case.attachment_flags.model_dump(by_alias=True),
    }
    _reject_unsafe_public_snapshot(snapshot)
    return snapshot


def _source_record_snapshot(record: WebAcquisitionSourceRecord) -> dict[str, str]:
    return {
        "sourceRef": record.source_ref,
        "method": record.method,
        "provider": record.provider,
        "url": record.normalized_url,
        "normalizedUrl": record.normalized_url,
        "contentDigest": record.content_digest,
        "proofType": record.proof.proof_type,
        "evidenceRef": record.proof.evidence_ref,
    }


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_path_text(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_path_text(str(resolved))
        return resolved
    _reject_unsafe_path_text(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("web acquisition fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("web acquisition fixtures must stay local and non-production")


def _validate_public_string(value: str) -> None:
    if not value.strip():
        raise ValueError("web acquisition metadata strings must be non-empty")
    if _PRODUCTION_PATH_RE.search(value):
        raise ValueError("web acquisition metadata cannot expose production paths")
    if _SECRET_SHAPED_VALUE_RE.search(value):
        raise ValueError("web acquisition metadata cannot expose secret-shaped values")
    if _BLOCKED_URL_TEXT_RE.search(value):
        raise ValueError("web acquisition metadata cannot expose blocked URL targets")
    _reject_blocked_urls_in_public_text(value)
    sanitized = sanitize_tool_preview(value)
    if any(token in sanitized for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("web acquisition metadata contains unsafe public token")


def _reject_blocked_urls_in_public_text(value: str) -> None:
    for raw_url in _URL_TEXT_RE.findall(value):
        parts = urlsplit(raw_url.rstrip(".,);]}"))
        host = (parts.hostname or "").lower().rstrip(".")
        if host and _is_blocked_host(host):
            raise ValueError("web acquisition metadata cannot expose blocked URL targets")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = _camel_to_snake(str(key))
            if normalized_key in _FORBIDDEN_TRUE_KEYS and item is True:
                raise ValueError(f"{key} cannot be true in web acquisition fixtures")
            _reject_unsafe_raw_value(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)
        return
    if isinstance(value, str):
        _validate_public_string(value)


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("web acquisition fixture values must be JSON-compatible")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("web acquisition fixture keys must be strings")
            _validate_json_like(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    raise ValueError("web acquisition fixture values must be JSON-compatible")


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _PRODUCTION_PATH_RE.search(rendered):
        raise ValueError("web acquisition public snapshot contains production paths")
    if _BLOCKED_URL_TEXT_RE.search(rendered):
        raise ValueError("web acquisition public snapshot exposes blocked URL targets")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("web acquisition public snapshot contains unsafe data")


def _validate_external_public_url(value: str) -> None:
    _validate_public_string(value)
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError("web acquisition source URLs must use https")
    if not parts.hostname:
        raise ValueError("web acquisition source URLs require a host")
    host = parts.hostname.lower().rstrip(".")
    if _is_blocked_host(host):
        raise ValueError("web acquisition source URL host is blocked")


def _canonical_url(value: str) -> str:
    parts = urlsplit(value)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    path = parts.path or ""
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{host}{path}{query}"


def _is_blocked_host(host: str) -> bool:
    if host in {"localhost", "kubernetes.default.svc"}:
        return True
    if host.endswith(".localhost") or host.endswith(".svc") or host.endswith(".cluster.local"):
        return True
    try:
        parsed = ip_address(host)
    except ValueError:
        return False
    return (
        parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_private
        or parsed.is_reserved
        or parsed.is_unspecified
        or parsed.is_multicast
    )


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
