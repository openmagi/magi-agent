from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


CanaryGateRequiredStage = Literal[
    "mocked_runtime",
    "local_live_enabled_test_only",
    "selected_bot_shadow_canary",
    "selected_bot_user_visible_canary",
    "readiness_report_only",
]
CanaryRunStatus = Literal["skipped", "passed", "failed"]
CanaryRouteDecision = Literal["typescript_fallback", "python_selected"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    arbitrary_types_allowed=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")


class _CanaryLadderModel(BaseModel):
    model_config = _MODEL_CONFIG

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
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class CanaryReadinessPackage(_CanaryLadderModel):
    implementation_blockers: tuple[str, ...] = Field(alias="implementationBlockers")
    required_test_sinks: tuple[str, ...] = Field(alias="requiredTestSinks")
    activation_env: tuple[str, ...] = Field(alias="activationEnv")
    counter_requirements: tuple[str, ...] = Field(alias="counterRequirements")
    rollback: tuple[str, ...]
    stop_conditions: tuple[str, ...] = Field(alias="stopConditions")

    @field_validator(
        "implementation_blockers",
        "required_test_sinks",
        "activation_env",
        "counter_requirements",
        "rollback",
        "stop_conditions",
        mode="before",
    )
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(str(item) for item in value if str(item).strip())
        return ()


class CanaryGateSpec(_CanaryLadderModel):
    gate_id: int = Field(alias="gateId", ge=0, le=9)
    slug: str
    title: str
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    required_stage: CanaryGateRequiredStage = Field(alias="requiredStage")
    readiness_package: CanaryReadinessPackage | None = Field(
        default=None,
        alias="readinessPackage",
    )


class CanaryGateRegistry(_CanaryLadderModel):
    gates: tuple[CanaryGateSpec, ...]

    def by_id(self, gate_id: int) -> CanaryGateSpec:
        for gate in self.gates:
            if gate.gate_id == gate_id:
                return gate
        raise KeyError(gate_id)


class CanaryHarnessConfig(_CanaryLadderModel):
    enabled: bool = False
    local_api_loop_enabled: bool = Field(default=False, alias="localApiLoopEnabled")
    scoped_canary_token_ref: str = Field(default="", alias="scopedCanaryTokenRef")
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_digest: str = Field(default="", alias="selectedOwnerDigest")
    environment: str = "local"
    report_directory: Path | None = Field(default=None, alias="reportDirectory")

    @field_validator("scoped_canary_token_ref")
    @classmethod
    def _validate_token_ref(cls, value: str) -> str:
        if value and not _SAFE_REF_RE.fullmatch(value):
            raise ValueError("scoped canary token ref must be a public-safe reference")
        return value

    @field_validator("selected_bot_digest", "selected_owner_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if value and not _DIGEST_RE.fullmatch(value):
            raise ValueError("selected canary scope must use sha256 digests")
        return value


class CanaryHarnessRequest(_CanaryLadderModel):
    gate_id: int = Field(alias="gateId")
    gate_slug: str = Field(alias="gateSlug")
    selected_bot_digest: str = Field(alias="selectedBotDigest")
    selected_owner_digest: str = Field(alias="selectedOwnerDigest")
    environment: str
    scoped_token_digest: str = Field(alias="scopedTokenDigest")
    synthetic: Literal[True] = True
    default_off_required: Literal[True] = Field(default=True, alias="defaultOffRequired")


class CanaryHarnessReport(_CanaryLadderModel):
    gate_id: int = Field(alias="gateId")
    gate_slug: str = Field(alias="gateSlug")
    status: CanaryRunStatus
    reason: str
    route_decision: CanaryRouteDecision = Field(alias="routeDecision")
    validated_sse_frames: bool = Field(default=False, alias="validatedSseFrames")
    validated_receipts: bool = Field(default=False, alias="validatedReceipts")
    validated_counters: bool = Field(default=False, alias="validatedCounters")
    validated_egress_evidence: bool = Field(default=False, alias="validatedEgressEvidence")
    default_off_restored: bool = Field(default=True, alias="defaultOffRestored")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )


class ScopedCanaryTestRequest(_CanaryLadderModel):
    method: Literal["POST"] = "POST"
    path: str
    headers: dict[str, str]
    body: str
    body_digest: str = Field(alias="bodyDigest")
    token_digest: str = Field(alias="tokenDigest")


ApiCanaryClient = Callable[[CanaryHarnessRequest], Mapping[str, object]]


class SyntheticCanaryHarness:
    def __init__(
        self,
        *,
        registry: CanaryGateRegistry,
        config: CanaryHarnessConfig | Mapping[str, object],
        api_client: ApiCanaryClient,
    ) -> None:
        self.registry = registry
        self.config = CanaryHarnessConfig.model_validate(config)
        self.api_client = api_client

    def run_gate(self, gate_id: int) -> CanaryHarnessReport:
        gate = self.registry.by_id(gate_id)
        if not self.config.enabled or not self.config.local_api_loop_enabled:
            return CanaryHarnessReport(
                gateId=gate.gate_id,
                gateSlug=gate.slug,
                status="skipped",
                reason="gate_disabled",
                routeDecision="typescript_fallback",
            )
        request = CanaryHarnessRequest(
            gateId=gate.gate_id,
            gateSlug=gate.slug,
            selectedBotDigest=self.config.selected_bot_digest,
            selectedOwnerDigest=self.config.selected_owner_digest,
            environment=self.config.environment,
            scopedTokenDigest=_digest(self.config.scoped_canary_token_ref),
        )
        try:
            response = dict(self.api_client(request))
        except Exception:
            report = CanaryHarnessReport(
                gateId=gate.gate_id,
                gateSlug=gate.slug,
                status="failed",
                reason="api_client_error",
                routeDecision="typescript_fallback",
            )
            self._write_report(report)
            return report

        report = CanaryHarnessReport(
            gateId=gate.gate_id,
            gateSlug=gate.slug,
            status="passed" if _response_passed(response) else "failed",
            reason="synthetic_canary_validated"
            if _response_passed(response)
            else "synthetic_canary_validation_failed",
            routeDecision=_route_decision(response),
            validatedSseFrames=_validate_sse_frames(response.get("eventFrames")),
            validatedReceipts=_validate_receipts(response.get("receipts")),
            validatedCounters=_validate_counter(response.get("counterStatus")),
            validatedEgressEvidence=_validate_egress(response.get("egressEvidence")),
            defaultOffRestored=True,
        )
        self._write_report(report)
        return report

    def _write_report(self, report: CanaryHarnessReport) -> None:
        if self.config.report_directory is None:
            return
        self.config.report_directory.mkdir(parents=True, exist_ok=True)
        path = self.config.report_directory / f"{report.gate_slug}.json"
        path.write_text(
            json.dumps(
                report.model_dump(by_alias=True, mode="json", warnings=False),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )


def build_canary_gate_registry() -> CanaryGateRegistry:
    return CanaryGateRegistry(
        gates=(
            CanaryGateSpec(
                gateId=0,
                slug="gate0_text",
                title="Selected-bot Python text canary",
                requiredStage="local_live_enabled_test_only",
            ),
            CanaryGateSpec(
                gateId=1,
                slug="gate1_readonly_tools",
                title="Selected-bot read-only tools",
                requiredStage="local_live_enabled_test_only",
            ),
            CanaryGateSpec(
                gateId=2,
                slug="gate2_coding_workspace",
                title="Coding and isolated workspace writes",
                requiredStage="mocked_runtime",
                readinessPackage=_package(
                    blockers=(
                        "isolated sandbox workspace with no user workspace mutation",
                        "file create/edit/patch/test/git staging handlers behind fake sinks",
                    ),
                    sinks=("temporary sandbox workspace", "fake test runner", "staged artifact sink"),
                    env=("OPENMAGI_CANARY_GATE=2", "OPENMAGI_WORKSPACE_SANDBOX=local-only"),
                    counters=("workspace mutation attempts", "patch bytes", "test command count"),
                    rollback=("discard sandbox", "clear staged artifacts", "restore TypeScript fallback"),
                    stops=("real workspace mutation", "unbounded shell command", "missing sandbox cleanup"),
                ),
            ),
            CanaryGateSpec(
                gateId=3,
                slug="gate3_web_research_browser",
                title="Controlled web research and browser read-only inspection",
                requiredStage="mocked_runtime",
                readinessPackage=_package(
                    blockers=(
                        "controlled web fixture server",
                        "allowlisted URL policy",
                        "read-only browser observation provider",
                    ),
                    sinks=("local web fixtures", "fake acquisition provider", "browser snapshot fixture"),
                    env=("OPENMAGI_CANARY_GATE=3", "OPENMAGI_WEB_FIXTURE_ONLY=1"),
                    counters=("allowed URL observations", "fetch budget", "browser snapshot bytes"),
                    rollback=("disable provider pack", "clear source ledger", "restore text-only fallback"),
                    stops=("public network fetch", "auth or captcha flow", "raw page transcript leakage"),
                ),
            ),
            CanaryGateSpec(
                gateId=4,
                slug="gate4_delivery_channel",
                title="Delivery and channel receipts",
                requiredStage="mocked_runtime",
                readinessPackage=_package(
                    blockers=(
                        "fake Telegram and Discord sinks",
                        "FileDeliver and FileSend receipts",
                        "delivery acknowledgement validation",
                    ),
                    sinks=("fake Telegram sink", "fake Discord sink", "local file delivery sink"),
                    env=("OPENMAGI_CANARY_GATE=4", "OPENMAGI_CHANNEL_FAKE_SINK=1"),
                    counters=("delivery attempts", "ack receipts", "channel retry attempts"),
                    rollback=("disable channel dispatcher", "drop fake sink state", "restore no-delivery mode"),
                    stops=("real user delivery", "token exposure", "channel side effect outside fake sink"),
                ),
            ),
            CanaryGateSpec(
                gateId=5,
                slug="gate5_scheduler_cron_mission",
                title="Scheduler, cron, and mission runtime",
                requiredStage="mocked_runtime",
                readinessPackage=_package(
                    blockers=(
                        "short-lived test cron namespace",
                        "mission lifecycle cleanup receipt",
                        "operator stop-condition verifier",
                    ),
                    sinks=("fake scheduler", "test cron namespace", "mission audit sink"),
                    env=("OPENMAGI_CANARY_GATE=5", "OPENMAGI_SCHEDULER_FAKE_TICK=1"),
                    counters=("scheduled ticks", "mission budget units", "cleanup receipts"),
                    rollback=("cancel test mission", "clear fake scheduler queue", "restore scheduler disabled"),
                    stops=("production cron mutation", "background tick outside harness", "missing cleanup receipt"),
                ),
            ),
            CanaryGateSpec(
                gateId=6,
                slug="gate6_memory",
                title="Memory recall, projection, write, and compaction",
                requiredStage="mocked_runtime",
                readinessPackage=_package(
                    blockers=(
                        "test memory namespace",
                        "recall/projection/write receipts",
                        "private memory leakage verifier",
                    ),
                    sinks=("fake memory provider", "test memory namespace", "compaction receipt sink"),
                    env=("OPENMAGI_CANARY_GATE=6", "OPENMAGI_MEMORY_NAMESPACE=test-only"),
                    counters=("recall count", "write receipt count", "compaction budget"),
                    rollback=("delete test namespace", "disable memory projection", "restore no-memory mode"),
                    stops=("production memory write", "private memory in public output", "redaction failure"),
                ),
            ),
            CanaryGateSpec(
                gateId=7,
                slug="gate7_child_workspace_adoption",
                title="Child agents and workspace adoption",
                requiredStage="mocked_runtime",
                readinessPackage=_package(
                    blockers=(
                        "isolated child runner",
                        "sanitized child envelope",
                        "worktree adoption preflight",
                    ),
                    sinks=("fake child runner", "temporary worktree", "sanitized envelope ledger"),
                    env=("OPENMAGI_CANARY_GATE=7", "OPENMAGI_CHILD_RUNNER_FAKE=1"),
                    counters=("child task count", "envelope byte budget", "adoption preflight count"),
                    rollback=("close child runner", "remove temporary worktree", "drop adoption proposal"),
                    stops=("real child runner", "raw child context injection", "workspace adoption without preflight"),
                ),
            ),
            CanaryGateSpec(
                gateId=8,
                slug="gate8_full_selected_python_authority",
                title="Full selected-bot Python authority",
                requiredStage="selected_bot_user_visible_canary",
                readinessPackage=_package(
                    blockers=(
                        "selected bot only routing",
                        "SessionContinuityBoundary canary imports committed "
                        "sanitized multi-turn history through ADK SessionService",
                        "continuity canary proves follow-up references such as "
                        "아까 말한 그거 resolve only when continuity is enabled",
                        "continuity projection rejects hidden reasoning, raw tool logs, "
                        "child transcripts, credentials, private paths, and unapproved memory",
                        "budget and observability rollback dashboard",
                    ),
                    sinks=(
                        "selected bot synthetic chat",
                        "pre-Gate8 multi-turn session continuity canary",
                        "budget meter",
                        "rollback monitor",
                    ),
                    env=(
                        "OPENMAGI_CANARY_GATE=8",
                        "OPENMAGI_SELECTED_BOT_ONLY=1",
                        "OPENMAGI_SESSION_CONTINUITY_CANARY=1",
                    ),
                    counters=(
                        "turn count",
                        "fallback count",
                        "budget spend",
                        "error rate",
                        "continuityCanaryStatus",
                        "importedEventCount",
                        "rejectedEntryCount",
                        "compactionApplied",
                        "projectionDigest",
                        "modelVisibleDigest",
                        "sourceTranscriptHeadDigest",
                        "reasonCodes",
                    ),
                    rollback=(
                        "force TypeScript fallback",
                        "disable Python route",
                        "preserve transcripts",
                    ),
                    stops=(
                        "non-selected bot routed",
                        "budget exceeded",
                        "TypeScript fallback unavailable",
                        "raw full transcript passed to model",
                        "hidden reasoning or raw tool logs imported",
                        "child transcripts, credentials, private paths, or unapproved memory imported",
                        "pre-Gate8 continuity canary missing or continuityCanaryStatus=pass not recorded",
                        "missing deterministic fallback or close behavior for continuity rejection",
                    ),
                ),
            ),
            CanaryGateSpec(
                gateId=9,
                slug="gate9_broader_canary_replacement",
                title="Broader canary and replacement readiness",
                requiredStage="readiness_report_only",
                readinessPackage=_package(
                    blockers=(
                        "cross-bot readiness aggregation",
                        "support and rollback owner assignment",
                        "replacement SLO comparison",
                    ),
                    sinks=("fleet readiness report", "synthetic comparison suite", "rollback owner ledger"),
                    env=("OPENMAGI_CANARY_GATE=9", "OPENMAGI_BROADER_CANARY_REPORT_ONLY=1"),
                    counters=("eligible bot count", "fallback health", "support incident budget"),
                    rollback=("halt expansion", "return all bots to TypeScript", "archive comparison report"),
                    stops=("separate rollout approval missing", "SLO regression", "rollback owner missing"),
                ),
            ),
        )
    )


def build_scoped_canary_test_request(
    *,
    botId: str,
    ownerUserId: str,
    environment: str,
    gate: str,
    body: str,
    issuer: str,
    audience: str,
    secret: str,
    nonce: str,
    nowMs: int | None = None,
    ttlSeconds: int = 300,
) -> ScopedCanaryTestRequest:
    issued_at = int((nowMs / 1000) if nowMs is not None else time.time())
    ttl = max(1, min(600, int(ttlSeconds)))
    route = f"/v1/chat/{botId}/completions"
    body_digest = _sha256_text(body)
    claims = {
        "iss": issuer,
        "aud": audience,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "purpose": "gate-canary",
        "botId": botId,
        "ownerUserId": ownerUserId,
        "env": environment,
        "gate": gate,
        "route": route,
        "maxRequests": 1,
        "ttlSeconds": ttl,
        "nonce": nonce,
        "bodyDigest": body_digest,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _base64url_json(header)
    encoded_payload = _base64url_json(claims)
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = _base64url_bytes(
        hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    )
    token = f"{encoded_header}.{encoded_payload}.{signature}"
    return ScopedCanaryTestRequest(
        path=route,
        headers={
            "content-type": "application/json",
            "x-gate-canary-test-token": token,
        },
        body=body,
        bodyDigest=body_digest,
        tokenDigest=_sha256_text(token),
    )


def _package(
    *,
    blockers: tuple[str, ...],
    sinks: tuple[str, ...],
    env: tuple[str, ...],
    counters: tuple[str, ...],
    rollback: tuple[str, ...],
    stops: tuple[str, ...],
) -> CanaryReadinessPackage:
    return CanaryReadinessPackage(
        implementationBlockers=blockers,
        requiredTestSinks=sinks,
        activationEnv=env,
        counterRequirements=counters,
        rollback=rollback,
        stopConditions=stops,
    )


def _response_passed(response: Mapping[str, object]) -> bool:
    return (
        response.get("status") == "python_ready"
        and response.get("fallbackStatus") == "none"
        and response.get("responseAuthority") == "python"
        and _validate_sse_frames(response.get("eventFrames"))
        and _validate_receipts(response.get("receipts"))
        and _validate_counter(response.get("counterStatus"))
        and _validate_egress(response.get("egressEvidence"))
    )


def _route_decision(response: Mapping[str, object]) -> CanaryRouteDecision:
    return "python_selected" if response.get("routeDecision") == "python_selected" else "typescript_fallback"


def _validate_sse_frames(value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, bytes | bytearray | str):
        return False
    frame_types = [
        frame.get("type")
        for frame in value
        if isinstance(frame, Mapping)
    ]
    return "response_clear" in frame_types and "done" in frame_types


def _validate_receipts(value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, bytes | bytearray | str):
        return False
    for item in value:
        if not isinstance(item, Mapping):
            return False
        if not _DIGEST_RE.fullmatch(str(item.get("requestDigest", ""))):
            return False
        if not _DIGEST_RE.fullmatch(str(item.get("boundedOutputDigest", ""))):
            return False
        if str(item.get("status", "")) not in {"ok", "blocked", "disabled", "error"}:
            return False
    return bool(value)


def _validate_counter(value: object) -> bool:
    return value in {
        "served_to_client",
        "runner_completed",
        "completed_after_client_timeout",
        "fallback_served",
        "ok",
    }


def _validate_egress(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("networkFetched") is False


def _digest(value: object) -> str:
    if isinstance(value, str) and _DIGEST_RE.fullmatch(value):
        return value
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _base64url_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_json(value: Mapping[str, object]) -> str:
    return _base64url_bytes(
        json.dumps(
            value,
            sort_keys=False,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


__all__ = [
    "ApiCanaryClient",
    "CanaryGateRegistry",
    "CanaryGateRequiredStage",
    "CanaryGateSpec",
    "CanaryHarnessConfig",
    "CanaryHarnessReport",
    "CanaryHarnessRequest",
    "CanaryReadinessPackage",
    "ScopedCanaryTestRequest",
    "SyntheticCanaryHarness",
    "build_canary_gate_registry",
    "build_scoped_canary_test_request",
]
