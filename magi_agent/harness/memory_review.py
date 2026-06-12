"""Gated background memory-review harness (A1, PR5).

Today the model only persists memory when it explicitly calls the ``MemoryWrite``
tool. Hermes additionally runs a periodic BACKGROUND REVIEW: after every N turns
it re-reads the transcript and saves what the model forgot. This module ports
that mechanism into magi's gated model.

Safety posture (mirrors the default-off harness boundaries, e.g.
``harness/cron_runtime.py``)
-------------------------------------------------------
* DEFAULT OFF. ``MemoryReviewConfig.enabled`` defaults to ``False`` and the
  ``review()`` entrypoint additionally requires the ``MAGI_MEMORY_REVIEW_ENABLED``
  env gate. Either being off short-circuits to a ``disabled`` receipt with NO
  reviewer call and NO writes.
* The reviewer is an INJECTED dependency (``Callable[[list[dict]], list[str]]``).
  This is the seam where a live model-backed extractor plugs in LATER; this PR
  never calls a live model. Tests inject a fake.
* Authority pins (``background_review_runner_attached``, ``live_reviewer_attached``,
  ``production_writes_enabled``) are locked to ``Literal[False]`` and coerced to
  False even if a forged truthy value is supplied — they cannot grant authority.
* Every accepted fact flows through the EXISTING safety pipeline: the declarative
  filter (``memory.declarative_filter.is_declarative_result``) drops task-state,
  then the PR2 ``MemoryWriteToolHost`` runs redaction + the gated append-only
  write. This harness reimplements NONE of that.
* The mechanism is intended to run OFF the hot turn loop (see the breadcrumb in
  ``transport/chat.py`` ``_local_adk_chat_sse``). It must NEVER block the user's
  turn.

Forbidden imports: urllib, socket, subprocess, http, requests — none here.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from magi_agent.memory.declarative_filter import is_declarative_result
from magi_agent.tools.context import ToolContext


#: Env gate (default OFF). ``review()`` requires BOTH this AND ``config.enabled``.
MAGI_MEMORY_REVIEW_ENABLED_ENV: str = "MAGI_MEMORY_REVIEW_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)

#: Reviewer seam: returns candidate fact strings extracted from the transcript.
#: A live model-backed extractor plugs in here later; this PR injects a fake.
Reviewer = Callable[[list[dict]], list[str]]

ReviewStatus = Literal["disabled", "reviewed"]
WriteOutcome = Literal["ok", "simulated", "blocked"]


def _review_env_enabled() -> bool:
    """Return True only when ``MAGI_MEMORY_REVIEW_ENABLED`` is explicitly truthy."""
    return os.environ.get(MAGI_MEMORY_REVIEW_ENABLED_ENV, "").lower() in _TRUE_STRINGS


class MemoryReviewConfig(BaseModel):
    """Default-off config for the background memory-review harness.

    ``enabled=False`` (default): ``review()`` short-circuits without calling the
    reviewer or attempting any write.

    The authority pins below are LOCKED to ``Literal[False]`` (mirroring
    ``BackgroundTaskConfig``); a forged truthy value is coerced to False so the
    harness can never claim to have attached a live runner / reviewer or to have
    performed production writes. Real write authority is owned entirely by the
    injected PR2 write-host's gate, never by this config.
    """

    model_config = _MODEL_CONFIG

    enabled: bool = False
    interval_turns: int = Field(default=10, ge=1, alias="intervalTurns")
    background_review_runner_attached: Literal[False] = Field(
        default=False, alias="backgroundReviewRunnerAttached"
    )
    live_reviewer_attached: Literal[False] = Field(
        default=False, alias="liveReviewerAttached"
    )
    production_writes_enabled: Literal[False] = Field(
        default=False, alias="productionWritesEnabled"
    )

    @field_serializer(
        "background_review_runner_attached",
        "live_reviewer_attached",
        "production_writes_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def model_copy(self, *, update: Any = None, deep: bool = False) -> Self:
        data = self.model_dump(mode="python", by_alias=False, warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update(
                {alias_to_name.get(str(key), str(key)): value for key, value in dict(update).items()}
            )
        # Authority pins can never be lifted via copy/update.
        data["background_review_runner_attached"] = False
        data["live_reviewer_attached"] = False
        data["production_writes_enabled"] = False
        _ = deep
        return type(self).model_validate(data)

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        model = super().model_construct(_fields_set=_fields_set, **values)
        object.__setattr__(model, "background_review_runner_attached", False)
        object.__setattr__(model, "live_reviewer_attached", False)
        object.__setattr__(model, "production_writes_enabled", False)
        return model


class MemoryReviewWriteReceipt(BaseModel):
    """Per-fact outcome of routing one accepted fact through the PR2 write-host."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    fact_preview: str = Field(alias="factPreview")
    status: WriteOutcome
    real_write: bool = Field(default=False, alias="realWrite")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


class MemoryReviewReceipt(BaseModel):
    """Summary receipt for one background review pass.

    ``status`` is ``disabled`` when the harness short-circuited (config OR env
    gate off) — in that case all counts are zero and the reviewer was NOT called.
    Otherwise ``reviewed``.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    status: ReviewStatus
    candidates: int = 0
    dropped_declarative: int = Field(default=0, alias="droppedDeclarative")
    attempted_writes: int = Field(default=0, alias="attemptedWrites")
    written: int = 0
    simulated: int = 0
    blocked: int = 0
    write_receipts: tuple[MemoryReviewWriteReceipt, ...] = Field(
        default=(), alias="writeReceipts"
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


#: The bundled first-party ``memory_strategy`` ref for the N-turn review
#: trigger (magi_agent/firstparty/packs/memory_strategies_default). A user
#: pack re-declaring this ref overrides the trigger.
DEFAULT_REVIEW_TRIGGER_STRATEGY_REF = "memory_strategy:review-trigger@1"


def should_run_review(turn_count: int, *, interval_turns: int, enabled: bool) -> bool:
    """Pure N-turn trigger (Task 5.2).

    Return ``True`` only when the harness is enabled AND at least one turn has
    completed AND ``turn_count`` lands on an ``interval_turns`` boundary. This is
    deliberately side-effect-free so the live-wiring caller can decide whether to
    kick off a review without importing any I/O.
    """
    if not enabled:
        return False
    if interval_turns <= 0:
        return False
    if turn_count <= 0:
        return False
    return turn_count % interval_turns == 0


class MemoryReviewHarness:
    """Run a gated background review pass over a transcript.

    The harness owns NO write authority of its own: it pre-filters candidates
    with the declarative filter and delegates persistence to the injected PR2
    ``MemoryWriteToolHost``. Whether a real write lands depends entirely on that
    host's own gate (disabled / shadow / live).
    """

    def __init__(
        self,
        config: MemoryReviewConfig,
        *,
        trigger: object | None = None,
    ) -> None:
        self.config = config
        if trigger is None:
            # C4 dual-load seam: explicit injection wins; otherwise resolve the
            # bundled first-party trigger from packs (fail-open to the exact
            # legacy in-module trigger — same function either way). Lazy import:
            # the shared resolver lives in memory_compaction and must not enter
            # this module's top-level import graph.
            from magi_agent.harness.memory_compaction import resolve_memory_strategy

            trigger = resolve_memory_strategy(
                DEFAULT_REVIEW_TRIGGER_STRATEGY_REF, default=should_run_review
            )
        self._trigger = trigger

    def should_run(self, *, turn_count: int) -> bool:
        """N-turn trigger via the injected strategy (default: should_run_review)."""
        return bool(
            self._trigger(
                turn_count,
                interval_turns=self.config.interval_turns,
                enabled=self.config.enabled,
            )
        )

    async def review(
        self,
        transcript: list[dict],
        *,
        reviewer: Reviewer,
        write_host: Any,
    ) -> MemoryReviewReceipt:
        """Re-read *transcript* via *reviewer* and route surfaced facts to *write_host*.

        Short-circuits (no reviewer call, no writes) unless BOTH
        ``config.enabled`` and the ``MAGI_MEMORY_REVIEW_ENABLED`` env gate are on.

        This is ``async`` on purpose: the documented caller
        (``transport/chat.py::_local_adk_chat_sse``) runs inside a live event
        loop and schedules this OFF the hot path (e.g. ``asyncio.create_task``).
        It therefore ``await``\\s the PR2 host's async ``_handle`` directly — no
        sync→async bridge, no worker thread that would block the loop.
        """
        if not self.config.enabled:
            return MemoryReviewReceipt(
                status="disabled", reasonCodes=("review_config_disabled",)
            )
        if not _review_env_enabled():
            return MemoryReviewReceipt(
                status="disabled", reasonCodes=("review_env_gate_disabled",)
            )

        # Reviewer isolation: a raising reviewer must never propagate into the
        # caller's turn. Return a reviewed receipt with zero candidates instead.
        try:
            candidate_facts = list(reviewer(transcript))
        except Exception:
            return MemoryReviewReceipt(
                status="reviewed",
                candidates=0,
                reasonCodes=("reviewer_exception",),
            )

        write_receipts: list[MemoryReviewWriteReceipt] = []
        dropped = 0
        written = 0
        simulated = 0
        blocked = 0

        for fact in candidate_facts:
            text = "" if fact is None else str(fact)
            decision = is_declarative_result(text)
            if not decision.accepted:
                dropped += 1
                continue
            receipt = await self._route_fact(text, write_host)
            write_receipts.append(receipt)
            if receipt.status == "ok":
                written += 1
            elif receipt.status == "simulated":
                simulated += 1
            else:
                blocked += 1

        return MemoryReviewReceipt(
            status="reviewed",
            candidates=len(candidate_facts),
            droppedDeclarative=dropped,
            attemptedWrites=len(write_receipts),
            written=written,
            simulated=simulated,
            blocked=blocked,
            writeReceipts=tuple(write_receipts),
            reasonCodes=("review_completed",),
        )

    async def _route_fact(self, fact: str, write_host: Any) -> MemoryReviewWriteReceipt:
        """Route one accepted fact through the PR2 host's ``_handle`` boundary.

        Builds a minimal ``ToolContext`` and awaits the async handler. The host
        runs its own declarative filter + redaction + gated append-only write,
        so this harness adds no privilege beyond surfacing the candidate.

        Per-fact isolation: if ``_handle`` (or receipt mapping) raises, this
        returns a ``blocked`` receipt with a generic reason code rather than
        crashing the whole review or leaking the exception. The raw exception
        text is intentionally NOT included (it could contain secrets).
        """
        arguments = {"fact": fact, "target_file": "MEMORY.md"}
        context = ToolContext(botId="memory-review", turnId="memory-review")
        try:
            result = await write_host._handle(arguments, context)
            return _to_write_receipt(fact, result)
        except Exception:
            return MemoryReviewWriteReceipt(
                factPreview=_fact_preview(fact),
                status="blocked",
                realWrite=False,
                reasonCodes=("review_write_exception",),
            )


def _to_write_receipt(fact: str, result: Any) -> MemoryReviewWriteReceipt:
    status = getattr(result, "status", "blocked")
    metadata = getattr(result, "metadata", {}) or {}
    output = getattr(result, "output", {}) or {}
    reason_codes = tuple(str(code) for code in (metadata.get("reasonCodes") or ()))

    if status == "ok":
        real = bool(output.get("realWrite", False)) if isinstance(output, dict) else False
        outcome: WriteOutcome = "ok" if real else "simulated"
        return MemoryReviewWriteReceipt(
            factPreview=_fact_preview(fact),
            status=outcome,
            realWrite=real,
            reasonCodes=reason_codes,
        )

    error_code = getattr(result, "error_code", None)
    codes = reason_codes or ((str(error_code),) if error_code else ())
    return MemoryReviewWriteReceipt(
        factPreview=_fact_preview(fact),
        status="blocked",
        realWrite=False,
        reasonCodes=codes,
    )


def _fact_preview(fact: str) -> str:
    return "sha256:" + hashlib.sha256(fact.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_REVIEW_TRIGGER_STRATEGY_REF",
    "MAGI_MEMORY_REVIEW_ENABLED_ENV",
    "MemoryReviewConfig",
    "MemoryReviewHarness",
    "MemoryReviewReceipt",
    "MemoryReviewWriteReceipt",
    "Reviewer",
    "should_run_review",
]
