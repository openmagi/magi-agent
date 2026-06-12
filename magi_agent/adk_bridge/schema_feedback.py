"""Schema-invalid argument feedback for the live ADK Runner (R3).

Hermes-agent parity (mechanism 1, returned-result path): when the dispatcher
rejects a tool call with ``errorCode == "tool_input_schema_invalid"``
(``magi_agent.tools.dispatcher``), the model today only sees the redacted
``schemaValidation`` projection — hashed/dropped field paths that say "a
required field is missing" but never WHICH — and no retry instruction. That
is self-correction-hostile: the model has no signal to fix the call.

This control appends hermes-style corrective feedback: plain-text missing /
unknown argument NAMES plus "retry with corrected arguments" guidance, under
a per-invocation attempt budget.

Redaction-layer relationship (security rationale)
-------------------------------------------------
``magi_agent.tools.schema_validation._safe_path_ref`` hashes field paths
deliberately, and this module does NOT touch that layer. Instead the control
recomputes diagnostics locally from data the model already legitimately
holds:

* the tool's enriched declaration (``tool_adapter._enrich_arguments_schema``
  surfaces the manifest ``input_schema`` on the ``arguments`` property — the
  model is sent these property names with every request), and
* the arguments the model itself produced for this very call.

Only schema VOCABULARY (argument names) is surfaced — never argument VALUES,
and nothing the model did not already see or produce.

Live integration point
-----------------------
Pure LoopControl on the existing control-plane ``on_after_tool`` seam (the
dispatcher RETURNS the error dict — no raise — so ADK's
``after_tool_callback`` fires and ``ControlPlane._after_tool`` fans out with
first-non-None-wins). The replacement dict is a superset-merge of the
original result (same convention as ``resilience_plugin._soft_nudge_response``
and the ``response_type`` markers of edit-retry/tool-exception reflection),
so the original ``errorCode``/``metadata`` survive for telemetry. Zero
loop-internal edits; zero dispatcher/schema_validation edits.

Behavior contract
-----------------
* Only fires on Mapping results with ``errorCode == "tool_input_schema_invalid"``
  that do not already carry a ``response_type`` marker (anti-recursion).
* Under budget: returns ``{**result, response_type, schemaFeedback,
  retryGuidance, retry_attempt, max_attempts}``.
* At/over budget (attempt > ``max_attempts``): returns ``None`` so the
  original redacted result flows through unchanged (today's behavior).
* Only top-level missing/unknown names are computed (YAGNI); deep mismatches
  fall back to the generic retry sentence.
* Fail-open: the ENTIRE body is wrapped in ``try/except Exception -> None``
  — ``tool._get_declaration`` is private ADK 1.33 API (already relied on by
  ``_enrich_arguments_schema`` and ``apply_provider_repair``); if an ADK bump
  changes it, behavior degrades to today's, never breaks the turn.

Fan-out ordering note: for FileEdit/PatchApply schema failures with
``MAGI_EDIT_RETRY_REFLECTION_ENABLED`` also on, ``_EditRetryLoopControl`` is
registered earlier and its ``_error_reason_from_result`` matches
``status == "blocked"`` first, so edit-retry wins — intended; this control is
registered after it in ``control_plane.build_default_plane``.

Flag ownership: ``MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED`` (default OFF, strict
truthy, profile-independent) and ``MAGI_TOOL_SCHEMA_FEEDBACK_MAX_ATTEMPTS``
are parsed in ``magi_agent.config.env``; callers pass resolved values to
:func:`build_schema_feedback_control`.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from magi_agent.adk_bridge.control_plane import BaseLoopControl

# Shared S-C write-through mapping view (one owner of the mutable counters:
# the runtime-owned PerInvocationState) — same view the edit-retry plugin uses.
from magi_agent.adk_bridge.edit_retry_reflection import _ScopedScalarView
from magi_agent.packs.context import PerInvocationState

SCHEMA_FEEDBACK_CONTROL_NAME = "magi_schema_feedback_control"

# Marker placed on the replacement tool response so downstream
# evidence/telemetry never mistakes the injected feedback for a real tool
# success, and so this control never reprocesses its own output (same
# convention as EDIT_RETRY_REFLECTION_RESPONSE_TYPE).
SCHEMA_FEEDBACK_RESPONSE_TYPE = "MAGI_SCHEMA_FEEDBACK"

_SCHEMA_INVALID_ERROR_CODE = "tool_input_schema_invalid"

_GLOBAL_SCOPE_KEY = "__magi_schema_feedback_global__"


class MagiSchemaFeedbackControl(BaseLoopControl):
    """LoopControl that names missing/unknown arguments on schema rejection.

    Attempt counters are keyed by ``(scope_key, tool_name)`` — the scope key
    is the ADK invocation id (same pattern as the edit-retry plugin) — so the
    budget is enforced per turn. ``_plugin`` is set to ``self`` so the generic
    ``_ExtendedControlPlanePlugin.after_run_callback`` sweep discovers
    :meth:`after_run_callback` and counters never grow unbounded across turns.

    Phase 5 / S-C: the per-invocation attempt counters live in a runtime-owned
    :class:`PerInvocationState` (``self._default_state``) rather than a private
    dict, so a user-authored equivalent control gets the same state struct off
    the typed context. The legacy ``self._attempts`` mapping is preserved as a
    live write-through view over that state; :meth:`apply_after_tool` is the
    typed-context entry point reading ``ctx.per_invocation``.
    """

    name = SCHEMA_FEEDBACK_CONTROL_NAME

    def __init__(self, *, max_attempts: int) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        # Runtime-owned per-invocation state (the ONE owner of the mutable
        # attempt counters). Replaces the old control-private ``self._attempts``
        # dict; the legacy attribute is now a live write-through view.
        self._default_state = PerInvocationState()
        # Expose self to the _ExtendedControlPlanePlugin generic plugin
        # fan-out (after_run_callback sweep), mirroring how the adapter
        # classes expose their wrapped plugins.
        self._plugin = self

    @property
    def _attempts(self) -> MutableMapping[tuple[str, str], Any]:
        """Live ``(scope_key, tool_name) -> attempt`` view over the runtime state.

        Backward-compatible surface: reads, writes, and sweeps behave exactly
        like the old dict while the storage is the runtime-owned struct (same
        LRU/sweep semantics as the other S-C migrations).
        """
        return _ScopedScalarView(self._default_state)

    # -- LoopControl hook ---------------------------------------------------

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        """Return a feedback-enriched superset of ``result``, or ``None``.

        ADK-callback path: feed the control's runtime-owned default state. The
        typed-context path (:meth:`apply_after_tool`) supplies a context-owned
        state instead.
        """
        return self.feedback_with_state(
            state=self._default_state,
            tool=tool,
            args=args,
            tool_context=tool_context,
            result=result,
        )

    async def apply_after_tool(
        self,
        ctx: Any,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        """Typed-context entry point (S-C): drive the attempt budget against the
        runtime-owned ``PerInvocationState`` from the context (falls back to the
        control's default state when the context carries none). Behavior is
        byte-identical to ``on_after_tool``."""
        state = getattr(ctx, "per_invocation", None) or self._default_state
        return self.feedback_with_state(
            state=state, tool=tool, args=args, tool_context=tool_context, result=result
        )

    # -- core (S-C typed-context decision) -----------------------------------

    def feedback_with_state(
        self,
        *,
        state: PerInvocationState,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        """Pure decision over a runtime-owned :class:`PerInvocationState` (S-C).

        Replaces the instance-private ``self._attempts`` mutation: the caller
        (the ADK hook above, or the typed-context entry) supplies the shared
        state. The attempt counter is read/incremented on ``state``; everything
        else (anti-recursion, diagnostics recompute, superset-merge) is
        byte-identical to the pre-migration body.

        Fail-open: any internal failure returns ``None`` so the original
        (redacted) result flows through exactly as today.
        """
        try:
            if not isinstance(result, Mapping):
                return None
            # Anti-recursion: never reprocess an injected marker response
            # (ours or any other reflection control's).
            if result.get("response_type") is not None:
                return None
            if result.get("errorCode") != _SCHEMA_INVALID_ERROR_CODE:
                return None

            tool_name = getattr(tool, "name", None)
            if not isinstance(tool_name, str) or not tool_name:
                tool_name = "unknown_tool"

            scope_key = _scope_key(tool_context)
            attempt = state.get_scoped(scope_key, tool_name, default=0) + 1
            state.set_scoped(scope_key, tool_name, attempt)
            if attempt > self.max_attempts:
                # Budget exhausted -> original redacted result flows through.
                return None

            # Recompute readable diagnostics from the enriched declaration
            # (_enrich_arguments_schema puts the manifest input_schema on the
            # 'arguments' property) + the args the model itself sent. Private
            # ADK API — protected by the enclosing fail-open try/except.
            declaration = tool._get_declaration()
            arguments_schema = declaration.parameters.properties["arguments"]
            required = [str(item) for item in (arguments_schema.required or [])]
            properties = dict(arguments_schema.properties or {})

            inner = args.get("arguments")
            if not isinstance(inner, Mapping):
                inner = {}

            missing = [field for field in required if field not in inner]
            unknown = [key for key in inner if key not in properties]
            valid_arguments = sorted(properties)

            guidance = (
                f"Your {tool_name} call did not match the tool's input schema."
            )
            if missing:
                guidance += f" Missing required argument(s): {', '.join(missing)}."
            if unknown:
                guidance += f" Unknown argument(s): {', '.join(unknown)}."
            guidance += (
                f" Valid arguments: {', '.join(valid_arguments)}."
                " Please retry with corrected arguments."
            )

            # Superset-merge (never restructure): original errorCode/metadata
            # survive for downstream telemetry keyed on the dispatcher shape.
            return {
                **dict(result),
                "response_type": SCHEMA_FEEDBACK_RESPONSE_TYPE,
                "schemaFeedback": {
                    "missingRequired": missing,
                    "unknownArguments": unknown,
                    "validArguments": valid_arguments,
                },
                "retryGuidance": guidance,
                "retry_attempt": attempt,
                "max_attempts": self.max_attempts,
            }
        except Exception:
            return None

    # -- plugin-level sweep (discovered via ._plugin fan-out) ---------------

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        """Sweep attempt counters for the invocation that just finished.

        The control lives for the whole process; the invocation id is the
        scope key, so on run completion we drop every counter belonging to
        that invocation (same cleanup contract as the reflection plugins),
        via the runtime state's clear-on-turn-complete hook.
        """
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._default_state.clear_invocation(inv)


# -- helpers ----------------------------------------------------------------


def _scope_key(tool_context: Any) -> str:
    invocation_id = getattr(tool_context, "invocation_id", None)
    if isinstance(invocation_id, str) and invocation_id:
        return invocation_id
    return _GLOBAL_SCOPE_KEY


def build_schema_feedback_control(
    *,
    enabled: bool,
    max_attempts: int,
) -> MagiSchemaFeedbackControl | None:
    """Return a configured control, or ``None`` when the feature is disabled.

    The flag/budget are owned by ``magi_agent.config.env`` (single source);
    callers pass the resolved values here so this module stays import-light
    and free of env-parsing concerns.
    """
    if not enabled:
        return None
    return MagiSchemaFeedbackControl(max_attempts=max_attempts)


__all__ = [
    "SCHEMA_FEEDBACK_CONTROL_NAME",
    "SCHEMA_FEEDBACK_RESPONSE_TYPE",
    "MagiSchemaFeedbackControl",
    "build_schema_feedback_control",
]
