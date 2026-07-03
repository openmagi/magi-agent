from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from magi_agent.context.auto_compact import AutoCompactionEngine
from magi_agent.context.content_replacement import ContentReplacer
from magi_agent.context.microcompact import MicrocompactEngine
from magi_agent.context.token_tracker import TokenBudgetTracker
from magi_agent.context.types import ContextManagementConfig, WarningLevel
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.hooks.scope import HookScope
from magi_agent.runtime.error_recovery.strategies.collapse_drain import CollapseDrainStrategy
from magi_agent.runtime.error_recovery.strategies.reactive_compact import (
    LLMCompactCaller,
    ReactiveCompactStrategy,
)
from magi_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    RecoverableError,
    RecoveryContext,
)
from magi_agent.tools.manifest import ToolSource

HOOK_NAME = "context_management"
HOOK_PRIORITY = 10

ClassifierCallable = Callable[[str], Awaitable[str]]
CompactionCallback = Callable[[], Awaitable[None]] | None


def make_context_management_manifest() -> HookManifest:
    """Factory function that returns the hook manifest for context management."""
    return HookManifest(
        name=HOOK_NAME,
        point=HookPoint.BEFORE_LLM_CALL,
        description="Multi-tier context window management pipeline",
        source=ToolSource(kind="builtin", package="context_management"),
        priority=HOOK_PRIORITY,
        blocking=False,
        failOpen=True,
        timeoutMs=30_000,
        enabled=True,
        scope=HookScope(),
    )


@dataclass(frozen=True)
class PipelineResult:
    """Immutable result from running the full context management pipeline."""

    warning_level: WarningLevel
    content_replacement_applied: bool
    snip_tokens_freed: int
    microcompact_applied: bool
    microcompact_cache_hits: int
    microcompact_tokens_freed: int
    auto_compact_applied: bool
    auto_compact_turns_summarized: int
    messages_before: int
    messages_after: int
    # Tier 6-7: proactive recovery (reuses error_recovery strategies)
    proactive_collapse_applied: bool = False
    proactive_collapse_tokens_freed: int = 0
    proactive_compact_applied: bool = False
    proactive_compact_tokens_freed: int = 0


def _safe_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _make_proactive_recovery_context(
    messages: list[dict],
    session_key: str = "proactive",
    turn_id: str = "proactive",
) -> RecoveryContext:
    """Wrap pipeline state into a RecoveryContext for reusing error recovery strategies proactively."""
    return RecoveryContext(
        error=RecoverableError(
            kind=ErrorKind.PROMPT_TOO_LONG,
            original_error="proactive_context_management",
        ),
        messages=messages,
        session_key=session_key,
        turn_id=turn_id,
    )


def load_config_from_env() -> ContextManagementConfig:
    """Load ContextManagementConfig from environment variables.

    Reads:
        MAGI_CONTEXT_MGMT_ENABLED: "1"/"true"/"True"/"yes" to enable
        MAGI_CONTEXT_MODERATE_THRESHOLD: float, default 0.60
        MAGI_CONTEXT_HIGH_THRESHOLD: float, default 0.75
        MAGI_CONTEXT_CRITICAL_THRESHOLD: float, default 0.90
        MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED: "1"/"true"/"True"/"yes" to enable
    """
    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_profile_bool, flag_str  #  # noqa: PLC0415

    enabled = flag_profile_bool("MAGI_CONTEXT_MGMT_ENABLED")
    moderate = _safe_float(flag_str("MAGI_CONTEXT_MODERATE_THRESHOLD") or None, 0.60)
    high = _safe_float(flag_str("MAGI_CONTEXT_HIGH_THRESHOLD") or None, 0.75)
    critical = _safe_float(flag_str("MAGI_CONTEXT_CRITICAL_THRESHOLD") or None, 0.90)
    proactive = flag_profile_bool("MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED")

    return ContextManagementConfig(
        enabled=enabled,
        moderate_threshold=moderate,
        high_threshold=high,
        critical_threshold=critical,
        proactive_recovery_enabled=proactive,
    )


class ContextManagementHook:
    """beforeLLMCall hook that runs the full context management pipeline.

    Pipeline order (each tier only activates at its threshold):
    1. Token tracking — compute current utilization + warning level
    2. Content replacement (Tier 2) — cap oversized tool results (>= MODERATE)
    3. Snip (Tier 3) — same as Tier 2, part of ContentReplacer
    4. Microcompact (Tier 4) — LLM-based per-result compression (>= HIGH)
    5. Auto compact (Tier 5) — full session summary (== CRITICAL)
    """

    def __init__(
        self,
        *,
        classifier: ClassifierCallable | None = None,
        config: ContextManagementConfig | None = None,
        model: str = "",
        on_before_compaction: CompactionCallback = None,
        on_after_compaction: CompactionCallback = None,
        llm_compact_caller: LLMCompactCaller | None = None,
    ) -> None:
        self._config = config or load_config_from_env()
        self._model = model
        self._tracker = TokenBudgetTracker(model=model, config=self._config)
        self._replacer = ContentReplacer()
        self._microcompact: MicrocompactEngine | None = (
            MicrocompactEngine(classifier=classifier) if classifier else None
        )
        self._auto_compact: AutoCompactionEngine | None = (
            AutoCompactionEngine(classifier=classifier) if classifier else None
        )
        self._on_before_compaction = on_before_compaction
        self._on_after_compaction = on_after_compaction

        # Tier 6-7: proactive recovery strategies (reuse error_recovery)
        if self._config.proactive_recovery_enabled:
            error_config = ErrorRecoveryConfig(recovery_enabled=True)
            self._collapse_drain: CollapseDrainStrategy | None = CollapseDrainStrategy(error_config)
            self._reactive_compact: ReactiveCompactStrategy | None = ReactiveCompactStrategy(
                error_config, llm_caller=llm_compact_caller
            )
        else:
            self._collapse_drain = None
            self._reactive_compact = None

    @property
    def manifest(self) -> HookManifest:
        return make_context_management_manifest()

    async def __call__(self, context: HookContext) -> HookResult:
        """Hook handler invoked at BEFORE_LLM_CALL.

        Returns ``continue`` — the actual message modification is done via
        :meth:`run_pipeline`, called by the ADK callback adapter.
        """
        return HookResult(action="continue")

    async def run_pipeline(
        self,
        messages: list[dict],
    ) -> tuple[list[dict], PipelineResult]:
        """Run the full context management pipeline on messages.

        Called by the ADK callback adapter's ``before_model_callback``
        to modify the ``llm_request`` messages before they reach the LLM.
        """
        messages_before = len(messages)

        # --- Early exit if disabled ----------------------------------------
        if not self._config.enabled:
            return messages, PipelineResult(
                warning_level=WarningLevel.NORMAL,
                content_replacement_applied=False,
                snip_tokens_freed=0,
                microcompact_applied=False,
                microcompact_cache_hits=0,
                microcompact_tokens_freed=0,
                auto_compact_applied=False,
                auto_compact_turns_summarized=0,
                messages_before=messages_before,
                messages_after=messages_before,
            )

        # --- Step 1: Track tokens and compute warning level ---------------
        self._tracker.reset()
        for msg in messages:
            role = msg.get("role", "")
            kind = ""
            tool_use_id = None
            if msg.get("role") == "tool" or msg.get("type") == "tool_result":
                kind = "tool_result"
                tool_use_id = msg.get("tool_use_id") or msg.get("tool_call_id")
            elif msg.get("role") == "user":
                kind = "user_message"
            elif msg.get("role") == "assistant":
                kind = "assistant_text"
            self._tracker.add_message(msg, role=role, kind=kind, tool_use_id=tool_use_id)

        snapshot = self._tracker.snapshot()
        warning_level = snapshot.warning_level

        # Initialize result tracking
        snip_tokens_freed = 0
        content_replacement_applied = False
        microcompact_applied = False
        microcompact_cache_hits = 0
        microcompact_tokens_freed = 0
        auto_compact_applied = False
        auto_compact_turns_summarized = 0

        # --- Step 2-3: Content replacement + snip (>= MODERATE) -----------
        if warning_level in (WarningLevel.MODERATE, WarningLevel.HIGH, WarningLevel.CRITICAL):
            try:
                messages, snip_result = self._replacer.apply(messages, warning_level)
                if snip_result.messages_snipped > 0:
                    content_replacement_applied = True
                    snip_tokens_freed = snip_result.tokens_freed
            except Exception:
                pass  # Fail open

        # --- Step 4: Microcompact (>= HIGH) -------------------------------
        if warning_level in (WarningLevel.HIGH, WarningLevel.CRITICAL) and self._microcompact:
            try:
                messages, mc_result = await self._microcompact.apply(messages, warning_level)
                if mc_result.messages_compacted > 0:
                    microcompact_applied = True
                    microcompact_cache_hits = mc_result.cache_hits
                    microcompact_tokens_freed = mc_result.tokens_freed
            except Exception:
                pass  # Fail open

        # --- Step 5: Auto compact (== CRITICAL) ---------------------------
        if warning_level == WarningLevel.CRITICAL and self._auto_compact:
            try:
                if self._on_before_compaction:
                    await self._on_before_compaction()
                messages, ac_result = await self._auto_compact.apply(messages, warning_level)
                if ac_result.activated:
                    auto_compact_applied = True
                    auto_compact_turns_summarized = ac_result.turns_summarized
                if self._on_after_compaction:
                    await self._on_after_compaction()
            except Exception:
                pass  # Fail open

        # --- Step 6: Proactive collapse drain (still CRITICAL after Tier 5) ---
        proactive_collapse_applied = False
        proactive_collapse_tokens_freed = 0
        proactive_compact_applied = False
        proactive_compact_tokens_freed = 0

        if self._config.proactive_recovery_enabled and warning_level == WarningLevel.CRITICAL:
            # Re-check utilization after Tier 5
            self._tracker.reset()
            for msg in messages:
                self._tracker.add_message(
                    msg, role=msg.get("role", ""), kind="", tool_use_id=None
                )
            post_t5_snapshot = self._tracker.snapshot()

            if post_t5_snapshot.warning_level == WarningLevel.CRITICAL and self._collapse_drain:
                try:
                    recovery_ctx = _make_proactive_recovery_context(messages)
                    result = await self._collapse_drain.recover(recovery_ctx)
                    if result.success and result.modified_messages is not None:
                        messages = result.modified_messages
                        proactive_collapse_applied = True
                        proactive_collapse_tokens_freed = result.tokens_freed
                except Exception:
                    pass  # Fail open

            # --- Step 7: Proactive reactive compact (still CRITICAL after Tier 6) ---
            # Re-check regardless of Tier 6 outcome
            self._tracker.reset()
            for msg in messages:
                self._tracker.add_message(
                    msg, role=msg.get("role", ""), kind="", tool_use_id=None
                )
            post_t6_snapshot = self._tracker.snapshot()

            if post_t6_snapshot.warning_level == WarningLevel.CRITICAL and self._reactive_compact:
                try:
                    recovery_ctx = _make_proactive_recovery_context(messages)
                    result = await self._reactive_compact.recover(recovery_ctx)
                    if result.success and result.modified_messages is not None:
                        messages = result.modified_messages
                        proactive_compact_applied = True
                        proactive_compact_tokens_freed = result.tokens_freed
                except Exception:
                    pass  # Fail open

        return messages, PipelineResult(
            warning_level=warning_level,
            content_replacement_applied=content_replacement_applied,
            snip_tokens_freed=snip_tokens_freed,
            microcompact_applied=microcompact_applied,
            microcompact_cache_hits=microcompact_cache_hits,
            microcompact_tokens_freed=microcompact_tokens_freed,
            auto_compact_applied=auto_compact_applied,
            auto_compact_turns_summarized=auto_compact_turns_summarized,
            messages_before=messages_before,
            messages_after=len(messages),
            proactive_collapse_applied=proactive_collapse_applied,
            proactive_collapse_tokens_freed=proactive_collapse_tokens_freed,
            proactive_compact_applied=proactive_compact_applied,
            proactive_compact_tokens_freed=proactive_compact_tokens_freed,
        )
