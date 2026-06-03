from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
import hashlib
import logging
import os
import re
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.channels.contract import ChannelRef
from magi_agent.harness.learning_executor import (
    LearningReflectionConfig,
    LearningReflectionResult,
    run_reflection,
)
from magi_agent.runtime.provider_receipts import provider_digest


logger = logging.getLogger(__name__)


#: Env gate shared with the reflection executor; OFF → reflection job not
#: scheduled and ``trigger_now`` returns a disabled no-op (zero work).
_REFLECTION_ENV_VAR: str = "MAGI_LEARNING_REFLECTION_ENABLED"
_REFLECTION_INTERVAL_ENV_VAR: str = "MAGI_LEARNING_REFLECTION_INTERVAL"
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: Default reflection interval — 24 hours (in seconds).
DEFAULT_REFLECTION_INTERVAL_SECONDS: int = 86_400


def _reflection_enabled() -> bool:
    return os.environ.get(_REFLECTION_ENV_VAR, "").lower() in _TRUE_STRINGS


def _reflection_interval_seconds() -> int:
    raw = os.environ.get(_REFLECTION_INTERVAL_ENV_VAR)
    if raw is None:
        return DEFAULT_REFLECTION_INTERVAL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REFLECTION_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_REFLECTION_INTERVAL_SECONDS


CronRuntimeStatus = Literal["disabled", "blocked", "hydrate_intent", "hydrated_local_fake", "mutated_local_fake"]
CronMutationOperation = Literal["pause", "resume", "cancel"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b)",
    re.IGNORECASE,
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|/workspace(?:/[^,\s\"']*)?|"
    r"/data/bots(?:/[^,\s\"']*)?|/var/lib/kubelet(?:/[^,\s\"']*)?|"
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args)|hidden[_ -]?reasoning)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "token",
    "secret",
    "credential",
    "password",
    "cookie",
    "path",
    "raw",
    "production",
    "route",
    "enabled",
    "attached",
    "authority",
    "authoritative",
    "performed",
)


class CronRuntimeConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_cron_enabled: bool = Field(default=False, alias="localFakeCronEnabled")
    background_scheduler_attached: Literal[False] = Field(default=False, alias="backgroundSchedulerAttached")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(mode="python", by_alias=False, warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(str(key), str(key)): value for key, value in update.items()})
        data["background_scheduler_attached"] = False
        data["production_writes_enabled"] = False
        data["route_attached"] = False
        _ = deep
        return type(self).model_validate(data)


class CronAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    background_scheduler_attached: Literal[False] = Field(default=False, alias="backgroundSchedulerAttached")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer("background_scheduler_attached", "production_writes_enabled", "route_attached")
    def _serialize_false(self, _value: object) -> bool:
        return False


class CronLease(BaseModel):
    model_config = _MODEL_CONFIG

    lease_id: str = Field(alias="leaseId")
    owner_digest: str = Field(alias="ownerDigest")
    acquired_at: int = Field(alias="acquiredAt", ge=0)
    expires_at: int = Field(alias="expiresAt", ge=0)

    @field_validator("lease_id", "owner_digest")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)


class CronDefinition(BaseModel):
    model_config = _MODEL_CONFIG

    cron_id: str = Field(alias="cronId")
    owner_digest: str = Field(alias="ownerDigest")
    expression: str
    timezone: str = "UTC"
    prompt_preview: str = Field(alias="promptPreview")
    delivery_channel: ChannelRef = Field(alias="deliveryChannel")
    enabled: bool = True
    paused: bool = False
    cancelled: bool = False
    next_fire_at: int = Field(alias="nextFireAt", ge=0)
    last_fired_at: int | None = Field(default=None, alias="lastFiredAt", ge=0)
    consecutive_failures: int = Field(default=0, alias="consecutiveFailures", ge=0)

    @field_validator("cron_id", "owner_digest")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("prompt_preview")
    @classmethod
    def _sanitize_prompt_preview(cls, value: str) -> str:
        return _safe_text(value)[:500]

    def public_projection(self) -> dict[str, object]:
        return {
            "cronId": _public_ref(self.cron_id, "cron"),
            "ownerDigest": _public_ref(self.owner_digest, "owner"),
            "expression": _safe_text(self.expression),
            "timezone": _safe_text(self.timezone),
            "promptPreview": _safe_text(self.prompt_preview)[:500],
            "deliveryChannel": self.delivery_channel.model_dump(by_alias=True),
            "enabled": self.enabled,
            "paused": self.paused,
            "cancelled": self.cancelled,
            "nextFireAt": self.next_fire_at,
            "lastFiredAt": self.last_fired_at,
            "consecutiveFailures": self.consecutive_failures,
        }


class CronDueTurn(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    turn_ref: str = Field(alias="turnRef")
    delivery_channel: ChannelRef = Field(alias="deliveryChannel")
    execution_allowed: Literal[False] = Field(default=False, alias="executionAllowed")

    @field_validator("source_ref", "turn_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRef": _public_ref(self.source_ref, "cron"),
            "turnRef": _public_ref(self.turn_ref, "turn"),
            "deliveryChannel": self.delivery_channel.model_dump(by_alias=True),
            "executionAllowed": False,
        }


class CronHydrationRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    now: int = Field(ge=0)
    crons: tuple[CronDefinition, ...] = ()
    fired_refs: tuple[str, ...] = Field(default=(), alias="firedRefs")
    lease: CronLease | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("fired_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)


class CronMutationRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: CronMutationOperation
    cron: CronDefinition
    metadata: Mapping[str, object] = Field(default_factory=dict)


class CronHydrationDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: CronRuntimeStatus
    request_digest: str = Field(alias="requestDigest")
    due_turns: tuple[CronDueTurn, ...] = Field(default=(), alias="dueTurns")
    updated_crons: tuple[CronDefinition, ...] = Field(default=(), alias="updatedCrons")
    suppressed_refs: tuple[str, ...] = Field(default=(), alias="suppressedRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: CronAuthorityFlags = Field(default_factory=CronAuthorityFlags, alias="authorityFlags")

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "requestDigest": self.request_digest,
            "dueTurns": [turn.public_projection() for turn in self.due_turns],
            "updatedCrons": [cron.public_projection() for cron in self.updated_crons],
            "suppressedRefs": [_public_ref(ref, "cron") for ref in self.suppressed_refs],
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class CronMutationDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: CronRuntimeStatus
    cron: CronDefinition | None = None
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    authority_flags: CronAuthorityFlags = Field(default_factory=CronAuthorityFlags, alias="authorityFlags")

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "cron": None if self.cron is None else self.cron.public_projection(),
            "reasonCodes": list(self.reason_codes),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class CronRuntimeBoundary:
    """Pure cron hydration/mutation boundary. No interval loop is started."""

    def __init__(self, config: CronRuntimeConfig) -> None:
        self.config = config

    def hydrate(self, request: CronHydrationRequest) -> CronHydrationDecision:
        diagnostics = _diagnostics(self.config, request.metadata)
        digest = provider_digest({"requestId": request.request_id, "now": request.now, "cronCount": len(request.crons)})
        if not self.config.enabled:
            return _hydrate_decision("disabled", digest, ("cron_runtime_disabled",), diagnostics)
        if not self.config.local_fake_cron_enabled:
            return _hydrate_decision("hydrate_intent", digest, ("local_fake_cron_disabled",), diagnostics)
        if request.lease is not None and request.now >= request.lease.expires_at:
            return _hydrate_decision("blocked", digest, ("cron_lease_stale",), diagnostics)

        seen: set[str] = set()
        fired = set(request.fired_refs)
        due_turns: list[CronDueTurn] = []
        updated: list[CronDefinition] = []
        suppressed: list[str] = []
        for cron in request.crons:
            if cron.cron_id in seen:
                continue
            seen.add(cron.cron_id)
            if cron.cron_id in fired or not cron.enabled or cron.paused or cron.cancelled or cron.next_fire_at > request.now:
                if cron.cron_id not in suppressed:
                    suppressed.append(cron.cron_id)
                continue
            next_fire = _next_fire_after(cron, request.now)
            updated_cron = cron.model_copy(update={"last_fired_at": request.now, "next_fire_at": next_fire})
            updated.append(updated_cron)
            due_turns.append(
                CronDueTurn(
                    sourceRef=cron.cron_id,
                    turnRef=f"cron-turn:{_short_digest(f'{cron.cron_id}:{request.now}')}",
                    deliveryChannel=cron.delivery_channel,
                )
            )
        return _hydrate_decision(
            "hydrated_local_fake",
            digest,
            ("local_fake_cron_hydration_receipt_only",),
            diagnostics,
            tuple(due_turns),
            tuple(updated),
            tuple(suppressed),
        )

    def mutate(self, request: CronMutationRequest) -> CronMutationDecision:
        if not self.config.enabled:
            return CronMutationDecision(status="disabled", cron=None, reasonCodes=("cron_runtime_disabled",))
        if not self.config.local_fake_cron_enabled:
            return CronMutationDecision(status="hydrate_intent", cron=request.cron, reasonCodes=("local_fake_cron_disabled",))
        if request.operation == "pause":
            cron = request.cron.model_copy(update={"paused": True, "enabled": False})
        elif request.operation == "resume":
            cron = request.cron.model_copy(update={"paused": False, "enabled": True, "cancelled": False})
        else:
            cron = request.cron.model_copy(update={"cancelled": True, "enabled": False, "paused": False})
        return CronMutationDecision(status="mutated_local_fake", cron=cron, reasonCodes=(f"cron_{request.operation}_receipt_only",))


def _hydrate_decision(
    status: CronRuntimeStatus,
    digest: str,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    due_turns: tuple[CronDueTurn, ...] = (),
    updated_crons: tuple[CronDefinition, ...] = (),
    suppressed_refs: tuple[str, ...] = (),
) -> CronHydrationDecision:
    return CronHydrationDecision(
        status=status,
        requestDigest=digest,
        dueTurns=due_turns,
        updatedCrons=updated_crons,
        suppressedRefs=suppressed_refs,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=CronAuthorityFlags(),
    )


def _next_fire_after(cron: CronDefinition, now: int) -> int:
    try:
        minute_values = _parse_cron_field(cron.expression.split()[0], 0, 59)
        hour_values = _parse_cron_field(cron.expression.split()[1], 0, 23)
        day_values = _parse_cron_field(cron.expression.split()[2], 1, 31)
        month_values = _parse_cron_field(cron.expression.split()[3], 1, 12)
        weekday_values = _parse_cron_field(cron.expression.split()[4], 0, 7)
        tz = ZoneInfo(cron.timezone)
    except (IndexError, ValueError, ZoneInfoNotFoundError):
        return now + 300_000

    if 7 in weekday_values:
        weekday_values = frozenset(0 if value == 7 else value for value in weekday_values)

    current_utc = datetime.fromtimestamp((now + 1) / 1000, tz=UTC)
    candidate = current_utc.astimezone(tz).replace(second=0, microsecond=0)
    if candidate <= current_utc.astimezone(tz):
        candidate = candidate + timedelta(minutes=1)

    for _ in range(60 * 24 * 366):
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and candidate.day in day_values
            and candidate.month in month_values
            and cron_weekday in weekday_values
        ):
            return int(candidate.astimezone(UTC).timestamp() * 1000)
        candidate = candidate + timedelta(minutes=1)
    return now + 300_000


def _parse_cron_field(field: str, minimum: int, maximum: int) -> frozenset[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            range_part, step_part = part.split("/", 1)
            step = int(step_part)
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            range_part = part
        if range_part == "*":
            values.update(range(minimum, maximum + 1, step))
        elif "-" in range_part:
            start_text, end_text = range_part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start < minimum or end > maximum or start > end:
                raise ValueError("cron range out of bounds")
            values.update(range(start, end + 1, step))
        else:
            value = int(range_part)
            if value < minimum or value > maximum:
                raise ValueError("cron value out of range")
            values.add(value)
    if not values:
        raise ValueError("cron field cannot be empty")
    return frozenset(values)


def _diagnostics(config: CronRuntimeConfig, metadata: Mapping[str, object]) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeCronEnabled": config.local_fake_cron_enabled,
        "backgroundSchedulerAttached": False,
        "productionWritesEnabled": False,
        "routeAttached": False,
        **dict(metadata),
    }


def _safe_ref(value: str) -> str:
    clean = _safe_text(value.strip())
    if not clean or not _REF_RE.fullmatch(clean):
        raise ValueError("cron refs must be public identifiers")
    return clean


def _safe_text(value: str) -> str:
    if _SECRET_TEXT_RE.search(value) or _PRIVATE_TEXT_RE.search(value):
        return "[redacted]"
    return value[:4096]


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        raw_key = str(key)
        normalized = re.sub(r"[^a-z0-9]", "", raw_key.casefold())
        if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean != "[redacted]":
                safe[raw_key[:80]] = clean
        elif isinstance(value, bool | int | float) or value is None:
            safe[raw_key[:80]] = value
    return safe


def _public_ref(value: str, prefix: str) -> str:
    return f"{prefix}:{_short_digest(value)}"


def _short_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


class LearningReflectionCronJob:
    """Reflection cron job — runs ``run_reflection`` on an interval.

    Architecture (no background loop is started — there is no real scheduler
    attachment here; scheduling is *intent* only, computed via
    ``next_fire_at``):

        ``MAGI_LEARNING_REFLECTION_ENABLED`` (env gate)
                ▼ scheduled?  ── OFF ──▶ not scheduled, ``next_fire_at`` None
                ▼ ON
        interval = ``MAGI_LEARNING_REFLECTION_INTERVAL`` or 24h default
                ▼
        ``trigger_now()`` ──▶ ``run_reflection(since=watermark, ...)``
                ▼ watermark-incremental (advances ``self.watermark``)

    The job NEVER attaches an OS/background scheduler, never starts a loop, and
    performs no work when the env gate is OFF (``trigger_now`` returns the
    executor's ``status="disabled"`` no-op).  ``run_reflection`` itself is
    double-gated (env AND ``config.enabled``), so the OFF path stays
    byte-identical to PR4.

    Watermark-incremental: ``trigger_now`` seeds ``run_reflection`` with the
    last persisted watermark and advances it on each ``ok`` pass, so a second
    pass over the same source reads zero new traces.
    """

    def __init__(
        self,
        *,
        source: object | None = None,
        store: object | None = None,
        config: LearningReflectionConfig | None = None,
        checkset: object | None = None,
        eval_gate_config: object | None = None,
        watermark: str | None = None,
    ) -> None:
        self._source = source
        self._store = store
        self._config = config
        self._checkset = checkset
        self._eval_gate_config = eval_gate_config
        self.watermark = watermark

    @property
    def scheduled(self) -> bool:
        """True only when the env gate is ON (the job is registered to run)."""
        return _reflection_enabled()

    @property
    def interval_seconds(self) -> int:
        """Interval between fires in SECONDS — from env, default 24h, positive only.

        Convenience accessor only.  ``next_fire_at`` MUST add ``interval_ms`` (not
        this value) so the result stays in the ms-since-epoch unit the rest of
        the cron module uses.
        """
        return _reflection_interval_seconds()

    @property
    def interval_ms(self) -> int:
        """Interval between fires in MILLISECONDS.

        This is the unit ``next_fire_at`` adds: the surrounding cron module's
        ``now`` is ms-since-epoch (see ``CronDefinition.next_fire_at`` /
        ``datetime.fromtimestamp((now+1)/1000, ...)`` / ``timestamp()*1000``), so
        the reflection interval must be expressed in ms to compose with it.
        """
        return _reflection_interval_seconds() * 1000

    def next_fire_at(self, *, now: int) -> int | None:
        """Next scheduled fire time, or ``None`` when not scheduled (OFF).

        ``now`` is milliseconds since epoch — matching the ms contract used by
        the rest of this module (``CronDefinition`` / ``CronHydrationRequest``).
        The interval is therefore added as ``interval_ms``, NOT seconds, so a
        24h interval advances ~86_400_000 ms rather than ~86 seconds.
        """
        if not self.scheduled:
            return None
        return now + self.interval_ms  # now: milliseconds since epoch

    async def trigger_now(self, *, tenant_id: str = "local") -> LearningReflectionResult:
        """Run one incremental reflection pass on demand.

        Calls ``run_reflection`` seeded with the current watermark.  Advances
        ``self.watermark`` when the pass returns ``status="ok"`` with a
        non-``None`` watermark.  When the env gate is OFF, ``run_reflection``
        returns the disabled no-op and the watermark is left unchanged.  On
        ``status="error"`` the watermark is left unchanged and the error is
        logged at WARNING (forward-compat for PR7's error path).

        Args:
            tenant_id: Tenant the reflection pass writes under.  Threaded into
                ``run_reflection`` so a non-``"local"`` tenant's run stays inside
                its own tenant.  Defaults to ``"local"`` (single-tenant path
                byte-identical).

        NOT re-entrant: it mutates ``self.watermark`` without a lock, so a real
        scheduler (PR7/PR8) MUST serialize concurrent ``trigger_now`` calls.
        """
        result = await run_reflection(
            source=self._source,
            since=self.watermark,
            config=self._config,
            store=self._store,
            checkset=self._checkset,
            eval_gate_config=self._eval_gate_config,
            tenant_id=tenant_id,
        )
        if result.status == "ok" and result.watermark is not None:
            self.watermark = result.watermark
        elif result.status == "error":
            logger.warning(
                "learning reflection pass returned status=error; "
                "watermark unchanged (still %r)",
                self.watermark,
            )
        return result


__all__ = [
    "DEFAULT_REFLECTION_INTERVAL_SECONDS",
    "CronAuthorityFlags",
    "CronDefinition",
    "CronDueTurn",
    "CronHydrationDecision",
    "CronHydrationRequest",
    "CronLease",
    "CronMutationDecision",
    "CronMutationRequest",
    "CronRuntimeBoundary",
    "CronRuntimeConfig",
    "LearningReflectionCronJob",
]
