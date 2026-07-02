from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.evidence.gate1a_egress_correlation import (
    GATE1A_EGRESS_CORRELATION_MODE,
    GATE1A_EGRESS_TELEMETRY_SOURCE,
    SENSITIVE_EGRESS_MARKER_RE as _SENSITIVE_RE,
    safe_proxy_url_from_env,
)


ObservedEgressEvidenceStatus = Literal[
    "observed_egress_evidence_present",
    "missing_observed_egress_evidence",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,95}$")
_SAFE_TIMESTAMP_RE = re.compile(r"^[0-9TZ:.+-]{10,40}$")
# _SENSITIVE_RE now comes from gate1a_egress_correlation (superset detector);
# see the import above. observed_egress previously kept a strict-subset copy.
_DEFAULT_LIVE_EGRESS_TELEMETRY_SOURCE = GATE1A_EGRESS_TELEMETRY_SOURCE
_LIVE_EGRESS_TELEMETRY_SCHEMA = "gate1a.egressProxyTelemetry.v1"
_LIVE_EGRESS_CORRELATION_MODE = GATE1A_EGRESS_CORRELATION_MODE
_MAX_TELEMETRY_LINE_BYTES = 4096
_MAX_TELEMETRY_SCAN_LINES = 8192


class ObservedEgressEvidence(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["openmagi.observedEgressEvidence.v1"] = Field(
        default="openmagi.observedEgressEvidence.v1",
        alias="schemaVersion",
    )
    request_digest: str | None = Field(default=None, alias="requestDigest")
    correlation_digest: str | None = Field(default=None, alias="correlationDigest")
    model_attempt_digest: str | None = Field(default=None, alias="modelAttemptDigest")
    provider_request_count: int = Field(ge=0, alias="providerRequestCount")
    egress_tunnel_count: int = Field(ge=0, alias="egressTunnelCount")
    egress_host_classes: tuple[str, ...] = Field(alias="egressHostClasses")
    observed_window_start: str = Field(alias="observedWindowStart")
    observed_window_end: str = Field(alias="observedWindowEnd")
    evidence_source: str = Field(alias="evidenceSource")
    redaction_status: Literal["public_safe"] = Field(alias="redactionStatus")
    decision_reason: str = Field(alias="decisionReason")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
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

    @field_validator("egress_host_classes", mode="before")
    @classmethod
    def _coerce_host_classes(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list | tuple):
            return tuple(str(item) for item in value)
        return ()

    @model_validator(mode="after")
    def _validate_public_safe_fields(self) -> Self:
        if self.request_digest is None and self.correlation_digest is None:
            raise ValueError("observed egress evidence requires request or correlation digest")
        for digest in (
            self.request_digest,
            self.correlation_digest,
            self.model_attempt_digest,
        ):
            if digest is not None and not _DIGEST_RE.fullmatch(digest):
                raise ValueError("observed egress evidence digest fields must be sha256 digests")
        if not self.egress_host_classes:
            raise ValueError("observed egress evidence requires at least one host class")
        for host_class in self.egress_host_classes:
            _validate_safe_label(host_class, "egress host class")
        _validate_safe_label(self.evidence_source, "evidence source")
        _validate_safe_reason(self.decision_reason, "decision reason")
        for timestamp in (self.observed_window_start, self.observed_window_end):
            if not _SAFE_TIMESTAMP_RE.fullmatch(timestamp):
                raise ValueError("observed egress window timestamp must be public-safe")
        serialized = str(
            self.model_dump(by_alias=True, mode="json", warnings=False)
        )
        if _SENSITIVE_RE.search(serialized):
            raise ValueError("observed egress evidence must not contain raw or secret values")
        return self


class ObservedEgressTelemetryEvent(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate1a.egressProxyTelemetry.v1"] = Field(
        default="gate1a.egressProxyTelemetry.v1",
        alias="schemaVersion",
    )
    observed_at: str = Field(alias="observedAt")
    request_digest: str | None = Field(default=None, alias="requestDigest")
    correlation_digest: str | None = Field(default=None, alias="correlationDigest")
    model_attempt_digest: str | None = Field(default=None, alias="modelAttemptDigest")
    egress_host_class: str = Field(alias="egressHostClass")
    evidence_source: str = Field(
        default=_DEFAULT_LIVE_EGRESS_TELEMETRY_SOURCE,
        alias="evidenceSource",
    )
    redaction_status: Literal["public_safe"] = Field(alias="redactionStatus")
    decision_reason: Literal["connect_tunnel_established"] = Field(
        default="connect_tunnel_established",
        alias="decisionReason",
    )

    @model_validator(mode="after")
    def _validate_public_safe_event(self) -> Self:
        if self.request_digest is None and self.correlation_digest is None:
            raise ValueError("egress telemetry requires request or correlation digest")
        for digest in (
            self.request_digest,
            self.correlation_digest,
            self.model_attempt_digest,
        ):
            if digest is not None and not _DIGEST_RE.fullmatch(digest):
                raise ValueError("egress telemetry digest fields must be sha256 digests")
        _validate_safe_label(self.egress_host_class, "egress host class")
        _validate_safe_label(self.evidence_source, "evidence source")
        if not _SAFE_TIMESTAMP_RE.fullmatch(self.observed_at):
            raise ValueError("egress telemetry timestamp must be public-safe")
        serialized = str(self.model_dump(by_alias=True, mode="json", warnings=False))
        if _SENSITIVE_RE.search(serialized):
            raise ValueError("egress telemetry must not contain raw or secret values")
        return self


class ObservedEgressEvidenceProvider(Protocol):
    evidence_source: str
    observed_egress_evidence_available: bool
    gate1a_egress_evidence_ready: bool
    readiness_reason: str
    correlation_mode: str
    gate1a_proxy_url: str | None

    def collect(
        self,
        *,
        request_digest: str,
        model_attempt_digest: str | None = None,
        observed_window_start: str | None = None,
        observed_window_end: str | None = None,
    ) -> ObservedEgressEvidence | None:
        ...


class NoObservedEgressEvidenceProvider:
    evidence_source = "none"
    observed_egress_evidence_available = False
    gate1a_egress_evidence_ready = False
    readiness_reason = "no_live_correlation_source_configured"
    correlation_mode = "none"
    gate1a_proxy_url = None

    def collect(
        self,
        *,
        request_digest: str,
        model_attempt_digest: str | None = None,
        observed_window_start: str | None = None,
        observed_window_end: str | None = None,
    ) -> ObservedEgressEvidence | None:
        del request_digest, model_attempt_digest, observed_window_start, observed_window_end
        return None


class LocalObservedEgressEvidenceProvider:
    evidence_source = "local_fixture"
    observed_egress_evidence_available = True
    gate1a_egress_evidence_ready = False
    readiness_reason = "local_fixture_not_activation_ready"
    correlation_mode = "local_fixture"
    gate1a_proxy_url = None

    def __init__(self, evidence: ObservedEgressEvidence) -> None:
        self._evidence = ObservedEgressEvidence.model_validate(
            evidence.model_dump(by_alias=True, mode="python", warnings=False)
        )

    def collect(
        self,
        *,
        request_digest: str,
        model_attempt_digest: str | None = None,
        observed_window_start: str | None = None,
        observed_window_end: str | None = None,
    ) -> ObservedEgressEvidence | None:
        del observed_window_start, observed_window_end
        evidence_digests = tuple(
            digest
            for digest in (
                self._evidence.request_digest,
                self._evidence.correlation_digest,
            )
            if digest is not None
        )
        if request_digest not in evidence_digests:
            return None
        if (
            model_attempt_digest is not None
            and self._evidence.model_attempt_digest is not None
            and self._evidence.model_attempt_digest != model_attempt_digest
        ):
            return None
        return self._evidence


class LiveEgressTelemetryEvidenceProvider:
    evidence_source = _DEFAULT_LIVE_EGRESS_TELEMETRY_SOURCE

    def __init__(
        self,
        telemetry_path: str | Path,
        *,
        correlation_source_configured: bool = True,
        proxy_url: str | None = None,
        max_scan_lines: int = _MAX_TELEMETRY_SCAN_LINES,
    ) -> None:
        self._telemetry_path = Path(telemetry_path)
        self._correlation_source_configured = bool(correlation_source_configured)
        self._proxy_url = str(proxy_url or "").strip() or None
        self._max_scan_lines = max(1, max_scan_lines)

    @property
    def observed_egress_evidence_available(self) -> bool:
        return self._correlation_source_configured and self._telemetry_source_ready()

    @property
    def gate1a_egress_evidence_ready(self) -> bool:
        return self.observed_egress_evidence_available and self._proxy_url is not None

    @property
    def correlation_mode(self) -> str:
        if self._correlation_source_configured:
            return _LIVE_EGRESS_CORRELATION_MODE
        return "none"

    @property
    def gate1a_proxy_url(self) -> str | None:
        return self._proxy_url

    @property
    def readiness_reason(self) -> str:
        if self.gate1a_egress_evidence_ready:
            return "live_correlation_source_ready"
        if not self._correlation_source_configured:
            return "correlation_source_not_configured"
        if not self._telemetry_source_ready():
            return "telemetry_source_unavailable"
        if self._proxy_url is None:
            return "proxy_connect_header_source_unavailable"
        return "telemetry_source_unavailable"

    def collect(
        self,
        *,
        request_digest: str,
        model_attempt_digest: str | None = None,
        observed_window_start: str | None = None,
        observed_window_end: str | None = None,
    ) -> ObservedEgressEvidence | None:
        if (
            not self.observed_egress_evidence_available
            or not _DIGEST_RE.fullmatch(request_digest)
            or observed_window_start is None
            or observed_window_end is None
        ):
            return None
        window_start = _parse_public_timestamp(observed_window_start)
        window_end = _parse_public_timestamp(observed_window_end)
        if window_start is None or window_end is None or window_end < window_start:
            return None
        if model_attempt_digest is not None and not _DIGEST_RE.fullmatch(model_attempt_digest):
            return None

        matched: list[ObservedEgressTelemetryEvent] = []
        mismatched_model_attempt = False
        for event in self._read_events():
            if not _event_matches_request(event, request_digest):
                continue
            observed_at = _parse_public_timestamp(event.observed_at)
            if observed_at is None or observed_at < window_start or observed_at > window_end:
                continue
            if (
                model_attempt_digest is not None
                and event.model_attempt_digest != model_attempt_digest
            ):
                mismatched_model_attempt = True
                continue
            matched.append(event)
        if mismatched_model_attempt or not matched:
            return None

        host_classes = tuple(sorted({event.egress_host_class for event in matched}))
        observed_times = sorted(matched, key=lambda event: event.observed_at)
        decision_reason = (
            "observed_gemini_proxy_tunnel"
            if host_classes == ("gemini_proxy",)
            else "observed_non_gemini_tunnel"
        )
        try:
            return ObservedEgressEvidence.model_validate(
                {
                    "requestDigest": request_digest,
                    "correlationDigest": request_digest,
                    "modelAttemptDigest": model_attempt_digest,
                    "providerRequestCount": 1,
                    "egressTunnelCount": len(matched),
                    "egressHostClasses": host_classes,
                    "observedWindowStart": observed_times[0].observed_at,
                    "observedWindowEnd": observed_times[-1].observed_at,
                    "evidenceSource": self.evidence_source,
                    "redactionStatus": "public_safe",
                    "decisionReason": decision_reason,
                }
            )
        except ValueError:
            return None

    def _telemetry_source_ready(self) -> bool:
        try:
            if not self._telemetry_path.is_file() or not os.access(
                self._telemetry_path,
                os.R_OK,
            ):
                return False
            with self._telemetry_path.open("r", encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= self._max_scan_lines:
                        break
                    text = line.strip()
                    if not text:
                        continue
                    if len(line.encode("utf-8")) > _MAX_TELEMETRY_LINE_BYTES:
                        return False
                    if _parse_telemetry_line(line) is None:
                        return False
            return True
        except OSError:
            return False

    def _read_events(self) -> Iterable[ObservedEgressTelemetryEvent]:
        try:
            with self._telemetry_path.open("r", encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= self._max_scan_lines:
                        break
                    if len(line.encode("utf-8")) > _MAX_TELEMETRY_LINE_BYTES:
                        continue
                    event = _parse_telemetry_line(line)
                    if event is not None:
                        yield event
        except OSError:
            return


def build_gate1a_observed_egress_evidence_provider_from_env(
    env: Mapping[str, str],
) -> ObservedEgressEvidenceProvider:
    # I-1: route the three gate1a egress-evidence knobs through the
    # typed flag registry. ``flag_str`` returns "" for unset, byte-
    # identical to the prior ``str(env.get(...) or "").strip()`` chain
    # because ``"" or ""`` short-circuits to ``""`` either way.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    source = str(
        flag_str("CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE", env=env) or ""
    ).strip()
    if source != "egress_proxy_telemetry":
        return NoObservedEgressEvidenceProvider()
    telemetry_path = str(
        flag_str("CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH", env=env) or ""
    ).strip()
    if not telemetry_path:
        return NoObservedEgressEvidenceProvider()
    correlation_mode = str(
        flag_str("CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE", env=env) or ""
    ).strip()
    return LiveEgressTelemetryEvidenceProvider(
        telemetry_path,
        correlation_source_configured=correlation_mode == _LIVE_EGRESS_CORRELATION_MODE,
        proxy_url=safe_proxy_url_from_env(env),
    )


def get_observed_egress_evidence_provider(runtime: object) -> ObservedEgressEvidenceProvider:
    provider = getattr(runtime, "gate1a_observed_egress_evidence_provider", None)
    if provider is None or not callable(getattr(provider, "collect", None)):
        return NoObservedEgressEvidenceProvider()
    return provider


def observed_egress_diagnostics(
    provider: ObservedEgressEvidenceProvider,
) -> dict[str, object]:
    return {
        "observedEgressEvidenceAvailable": bool(
            getattr(provider, "observed_egress_evidence_available", False)
        ),
        "gate1aEgressEvidenceReady": bool(
            getattr(provider, "gate1a_egress_evidence_ready", False)
        ),
        "egressEvidenceSource": _safe_or_none(
            getattr(provider, "evidence_source", "none")
        ),
        "egressEvidenceReadinessReason": _safe_or_none(
            getattr(provider, "readiness_reason", "no_live_correlation_source_configured")
        ),
    }


def _safe_or_none(value: object) -> str:
    text = str(value or "none").strip()
    if _SAFE_REASON_RE.fullmatch(text) and not _SENSITIVE_RE.search(text):
        return text
    return "redacted"


def _validate_safe_label(value: str, field_name: str) -> None:
    if not _SAFE_LABEL_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a sanitized class label")


def _validate_safe_reason(value: str, field_name: str) -> None:
    if not _SAFE_REASON_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be public-safe")


def _event_matches_request(
    event: ObservedEgressTelemetryEvent,
    request_digest: str,
) -> bool:
    return request_digest in {
        digest for digest in (event.request_digest, event.correlation_digest) if digest
    }


def _parse_telemetry_line(line: str) -> ObservedEgressTelemetryEvent | None:
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    if payload.get("schemaVersion") != _LIVE_EGRESS_TELEMETRY_SCHEMA:
        return None
    try:
        return ObservedEgressTelemetryEvent.model_validate(payload)
    except ValueError:
        return None


def _parse_public_timestamp(value: str) -> datetime | None:
    if not _SAFE_TIMESTAMP_RE.fullmatch(value):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
