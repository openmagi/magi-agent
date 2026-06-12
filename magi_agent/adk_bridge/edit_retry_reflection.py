"""Edit-failure reflection / retry wiring for the live ADK Runner.

PR2: magi-agent already ships OpenCode-style "edit failed -> re-inject a
corrective hidden message into the next model turn" logic in
``magi_agent.recipes.retry_repair_policies`` + ``RetryController``. That logic
was previously only instantiated in tests. This module *activates* it on the
live ADK turn engine by exposing it as a Google ADK ``BasePlugin``.

Live integration point
-----------------------
ADK's ``Runner.run_async`` owns the multi-step tool loop. When a tool raises,
``google.adk.flows.llm_flows.functions`` invokes
``PluginManager.run_on_tool_error_callback``; when a tool returns an
error-shaped dict, ``run_after_tool_callback`` fires. In *both* cases, if a
plugin returns a ``dict``, that dict **replaces** the tool's function response
and is fed to the model as the tool result on the next LLM call
(see ``__build_response_event``: the dict becomes a ``role="user"``
function_response Part). That is exactly the "hidden corrective message"
channel — it never surfaces to the end user, only to the model.

So this plugin:

1. detects an edit-apply failure (``FileEdit``/``PatchApply`` raising
   ``ValueError`` or returning an error dict),
2. maps the failure to an ``edit_apply_failed`` error code,
3. runs the shared :class:`RetryController` (reusing
   ``coding_edit_retry_repair_rules`` verbatim — no logic duplication),
4. returns the controller's ``hidden_user_message`` as the replacement tool
   response so the model re-attempts with corrective guidance, and
5. fails closed once ``max_attempts`` is reached (returns ``None`` ->
   the original error/result is used and the model is told to stop).

Error-code mapping & PR1 soft dependency
----------------------------------------
gate5b FileEdit on ``main`` today raises ``ValueError("old_text_not_found")``
(a no-match case). PR1 (separate branch) adds
``ValueError("old_text_not_unique")``. This module maps:

* ``old_text_not_unique`` / ``multiple_matches`` / ``not_unique`` -> rule
  ``not_unique`` (PR1 forward-compat; flows automatically once PR1 lands),
* a placeholder ``new_string`` (e.g. ``// ... rest unchanged``) -> rule
  ``lazy_output`` (heuristic),
* everything else (``old_text_not_found`` / ``no_match`` / empty) -> the
  catch-all no-match edit rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, MutableMapping
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.packs.context import PerInvocationState
from magi_agent.recipes.retry_repair_policies import coding_edit_retry_repair_rules
from magi_agent.runtime.turn_utilities import RetryController


EDIT_RETRY_REFLECTION_PLUGIN_NAME = "magi_edit_retry_reflection_plugin"

# Tools whose failures represent an "edit apply" attempt against file content.
_EDIT_TOOL_NAMES = frozenset({"FileEdit", "PatchApply"})

# Per-control namespace for the shared PerInvocationState scalar key (S-C). The
# three S-C reflection controls (edit-retry, schema-feedback, tool-exception) may
# all receive the SAME runtime-owned PerInvocationState and may all key a counter
# for the SAME tool in the SAME invocation. Keying by ``(invocation_id,
# tool_name)`` alone made them collide on that shared state and consume each
# other's ``max_attempts`` budget (failing closed early). Each control namespaces
# its scalar ``name`` component with a distinct prefix so their counters are
# disjoint. The separator is a control char that cannot appear in a tool name.
_STATE_NS_SEP = "\x1f"
EDIT_RETRY_STATE_NAMESPACE = "edit_retry"


def scoped_state_name(namespace: str, name: str) -> str:
    """Compose a control-namespaced ``name`` for a shared PerInvocationState key."""
    return f"{namespace}{_STATE_NS_SEP}{name}"

# Marker placed on the replacement tool response so downstream evidence/telemetry
# can recognise an injected corrective message and so it is never mistaken for a
# real tool success. Kept model-visible (it is the corrective guidance) but not
# user-visible (it travels on the function_response channel only).
EDIT_RETRY_REFLECTION_RESPONSE_TYPE = "MAGI_EDIT_RETRY_REFLECTION"

# Heuristic: a "lazy" new_string that elides real code with a placeholder
# comment instead of writing the full replacement.
_LAZY_PLACEHOLDER_RE = re.compile(
    r"(?:#|//|/\*|<!--)\s*\.{2,}\s*"
    r"(?:rest|remaining|existing|unchanged|previous|the same|as before)"
    r"|\.{3}\s*(?:rest|remaining|existing|unchanged)\s+(?:of\s+)?(?:the\s+)?"
    r"(?:code|file|function|implementation)",
    re.IGNORECASE,
)

# Failure-reason fragments (from exception text or error dicts) that indicate a
# uniqueness failure. ``old_text_not_unique`` is the PR1 code (soft dependency).
_NOT_UNIQUE_MARKERS = (
    "old_text_not_unique",
    "not_unique",
    "multiple_matches",
    "more than once",
    "appears multiple",
)

# Failure-reason fragments that indicate an explicit no-match / not-found
# failure. These must win over the lazy-placeholder heuristic: a genuine
# "old_string was not found" error is a not_found case even if the model's
# new_string happened to contain a lazy placeholder comment. Mislabeling it as
# ``lazy_output`` would corrupt telemetry and pick the wrong repair message.
_NOT_FOUND_MARKERS = (
    "old_text_not_found",
    "not_found",
    "no_match",
)


class _ScopedScalarView(MutableMapping[tuple[str, str], Any]):
    """Live write-through ``(scope, name) -> value`` view over a PerInvocationState.

    Phase 5 / S-C moves the edit-retry attempt counters out of a plugin-private
    ``dict`` and into the runtime-owned :class:`PerInvocationState` scalar store
    (which is keyed identically by ``(invocation_id, name)``). This view exposes
    the SAME mapping surface the legacy ``self._attempts`` dict had — item
    assignment, membership, iteration, equality — but every read/write goes
    straight through to ``PerInvocationState`` so there is exactly one owner of
    the mutable state. Writes route through ``set_scoped`` so the LRU bound is
    enforced on insert, mirroring the pre-migration behavior.
    """

    def __init__(self, state: PerInvocationState, namespace: str | None = None) -> None:
        self._state = state
        # When set, every ``(scope, name)`` operation is transparently keyed by the
        # control-namespaced name in the underlying state, so each control's view
        # is disjoint from the others sharing the same PerInvocationState. The
        # un-namespaced 2-tuple surface is preserved for legacy callers/tests.
        self._namespace = namespace

    def _stored_name(self, name: str) -> str:
        if self._namespace is None:
            return name
        return scoped_state_name(self._namespace, name)

    def _own_prefix(self) -> str | None:
        if self._namespace is None:
            return None
        return f"{self._namespace}{_STATE_NS_SEP}"

    def __getitem__(self, key: tuple[str, str]) -> Any:
        scope, name = key
        sentinel = object()
        value = self._state.get_scoped(scope, self._stored_name(name), default=sentinel)
        if value is sentinel:
            raise KeyError(key)
        return value

    def __setitem__(self, key: tuple[str, str], value: Any) -> None:
        scope, name = key
        self._state.set_scoped(scope, self._stored_name(name), value)

    def __delitem__(self, key: tuple[str, str]) -> None:
        scope, name = key
        if key not in self:
            raise KeyError(key)
        self._state.pop_scoped(scope, self._stored_name(name))

    def _own_items(self) -> Iterator[tuple[str, str]]:
        prefix = self._own_prefix()
        for scope, name in dict(self._state._store):
            if prefix is None:
                yield (scope, name)
            elif name.startswith(prefix):
                # present the un-namespaced tool name for this control's view
                yield (scope, name[len(prefix):])

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return self._own_items()

    def __len__(self) -> int:
        return sum(1 for _ in self._own_items())

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"_ScopedScalarView({dict(self.items())!r})"


class MagiEditRetryReflectionPlugin(BasePlugin):
    """ADK plugin that re-injects corrective guidance after an edit failure.

    The plugin owns one :class:`RetryController` per tracking scope (the ADK
    invocation id) so that ``max_attempts`` is enforced per turn and never grows
    unbounded across turns. The controller and the repair rules are reused
    as-is; this plugin only adapts the ADK tool boundary to them.

    Phase 5 / S-C: the per-invocation attempt counters live in a runtime-owned
    :class:`PerInvocationState` (``self._default_state``) rather than a private
    dict, so a user-authored equivalent control gets the same state struct off
    the typed context. The legacy ``self._attempts`` mapping is preserved as a
    live write-through view over that state (the ADK callbacks below feed the
    default state; the dispatcher supplies a context-owned state in Phase 6).
    """

    def __init__(
        self,
        *,
        max_attempts: int,
        name: str = EDIT_RETRY_REFLECTION_PLUGIN_NAME,
    ) -> None:
        super().__init__(name)
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        # Runtime-owned per-invocation state (the ONE owner of the mutable attempt
        # counters). Replaces the old plugin-private ``self._attempts`` dict; the
        # legacy attribute is now a live write-through view over this struct.
        self._default_state = PerInvocationState()
        # One controller for the whole plugin: repair-rule selection is pure (it
        # depends only on the per-call decision input), and the controller's own
        # ``exhausted``/``last_error`` state is unused here — the attempt budget
        # is enforced via the external attempt counter we feed in. Caching
        # avoids allocating rule objects on every failed edit.
        self._controller = RetryController(
            max_attempts=self.max_attempts,
            repair_rules=coding_edit_retry_repair_rules(),
        )

    @property
    def _attempts(self) -> MutableMapping[tuple[str, str], Any]:
        """Live ``(scope_key, tool_name) -> attempt`` view over the runtime state.

        Backward-compatible surface: the per-invocation attempt counters now live
        in ``self._default_state`` (a :class:`PerInvocationState`). This view lets
        existing callers/tests read, assign, and sweep counters exactly as they did
        against the old dict, while the actual storage is the runtime-owned struct.
        The view is namespaced so this control's counters stay disjoint from the
        other S-C controls' when they share one PerInvocationState.
        """
        return _ScopedScalarView(self._default_state, EDIT_RETRY_STATE_NAMESPACE)

    # -- ADK callbacks ----------------------------------------------------

    async def on_tool_error_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> dict[str, Any] | None:
        """A tool *raised*. gate5b FileEdit raises ``ValueError(...)``."""
        return self._maybe_reflect(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            reason=str(error),
        )

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        """A tool *returned* — handle error-shaped results, reset on success."""
        # Never recurse on our own injected response.
        if (
            isinstance(result, Mapping)
            and result.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
        ):
            return None

        error_reason = _error_reason_from_result(result)
        if error_reason is None:
            # Successful (or non-error) edit -> reset the per-tool attempt count.
            self._reset(tool_context, _tool_name(tool))
            return None

        return self._maybe_reflect(
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            reason=error_reason,
        )

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        """Sweep attempt counters for the invocation that just finished.

        The plugin instance lives for the whole process, so without this cleanup
        the attempt counters would grow unbounded: keys only pop on a *successful*
        edit (``_reset``), never when the budget is exhausted or the turn simply
        ends without a final success. The invocation id is the scope key, so on
        run completion we drop every counter belonging to that invocation via the
        runtime state's clear-on-turn-complete hook.
        """
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._default_state.clear_invocation(inv)

    # -- core -------------------------------------------------------------

    def reflect_with_state(
        self,
        *,
        state: PerInvocationState,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        reason: str,
    ) -> dict[str, Any] | None:
        """Pure decision over a runtime-owned :class:`PerInvocationState` (S-C).

        Replaces the instance-private ``self._attempts`` mutation: the caller
        (the ADK callbacks below, or the typed-context control adapter) supplies
        the shared state. The attempt counter is read/incremented on ``state``;
        everything else (error classification, repair-rule selection, fail-closed
        budget) is byte-identical to the pre-migration ``_maybe_reflect``.
        """
        tool_name = _tool_name(tool)
        if tool_name not in _EDIT_TOOL_NAMES:
            return None

        scope_key = _scope_key(tool_context)
        state_name = scoped_state_name(EDIT_RETRY_STATE_NAMESPACE, tool_name)
        attempt = state.get_scoped(scope_key, state_name, default=0) + 1
        state.set_scoped(scope_key, state_name, attempt)

        error_code = _classify_edit_error_code(reason, tool_args)

        # Repair-rule selection is pure (it depends only on this call's input),
        # and the attempt budget is enforced via the ``attempt`` value we feed
        # in, so ``max_attempts`` is fail-closed exactly as the controller
        # defines. The cached controller is reused across calls — see __init__.
        decision = self._controller.next(
            {
                "kind": "edit_apply_failed",
                "reason": reason,
                "attempt": attempt,
                "errorCode": error_code,
            }
        )

        if decision.action != "resample" or not decision.hidden_user_message:
            # Fail-closed at budget (or no corrective message available): return
            # None so ADK propagates the original error / keeps the original
            # result and the model is not handed an infinite retry loop.
            return None

        return {
            "response_type": EDIT_RETRY_REFLECTION_RESPONSE_TYPE,
            "error_type": "edit_apply_failed",
            "error_code": error_code,
            "retry_attempt": attempt,
            "max_attempts": self.max_attempts,
            # The hidden corrective message the model sees as the tool result.
            "reflection_guidance": decision.hidden_user_message,
        }

    def _maybe_reflect(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        reason: str,
    ) -> dict[str, Any] | None:
        # ADK-callback path: feed the plugin's runtime-owned default state. The
        # typed-context path supplies a context-owned state instead (Phase 6).
        return self.reflect_with_state(
            state=self._default_state,
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            reason=reason,
        )

    def _reset(self, tool_context: Any, tool_name: str) -> None:
        self._default_state.pop_scoped(
            _scope_key(tool_context),
            scoped_state_name(EDIT_RETRY_STATE_NAMESPACE, tool_name),
        )


# -- helpers --------------------------------------------------------------


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else ""


def _scope_key(tool_context: Any) -> str:
    invocation_id = getattr(tool_context, "invocation_id", None)
    if isinstance(invocation_id, str) and invocation_id:
        return invocation_id
    return "__magi_edit_retry_global__"


def _classify_edit_error_code(reason: str, tool_args: Mapping[str, Any]) -> str:
    lowered = reason.lower()
    # Uniqueness failures take top priority (PR1 forward-compat).
    if any(marker in lowered for marker in _NOT_UNIQUE_MARKERS):
        return "not_unique"
    # An explicit no-match/not-found error is authoritative: it must win over the
    # lazy-placeholder heuristic so a genuine ``old_text_not_found`` is never
    # mislabeled ``lazy_output`` (which would pick the wrong repair message and
    # corrupt telemetry).
    if any(marker in lowered for marker in _NOT_FOUND_MARKERS):
        return "not_found"
    new_text = _new_text_from_args(tool_args)
    if new_text and _LAZY_PLACEHOLDER_RE.search(new_text):
        return "lazy_output"
    # Default: no-match / not-found path -> catch-all edit rule (error_code=None
    # is what the catch-all rule keys on, but we surface "not_found" for
    # evidence; the rule matcher only requires kind == edit_apply_failed when
    # error_code does not match a specialised rule).
    return "not_found"


def _new_text_from_args(tool_args: Mapping[str, Any]) -> str:
    for key in ("newText", "new_text", "newString", "new_string", "content"):
        value = tool_args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _error_reason_from_result(result: Any) -> str | None:
    """Extract a failure reason from an error-shaped tool result, else None."""
    if not isinstance(result, Mapping):
        return None
    status = result.get("status")
    if isinstance(status, str) and status.lower() in {"error", "failed", "blocked"}:
        return _result_reason_text(result)
    for key in ("error", "error_code", "errorCode"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _result_reason_text(result: Mapping[str, Any]) -> str:
    for key in ("error", "error_code", "errorCode", "reason", "message", "detail"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return "edit_apply_failed"


def build_edit_retry_reflection_plugin(
    *,
    enabled: bool,
    max_attempts: int,
) -> MagiEditRetryReflectionPlugin | None:
    """Return a configured plugin, or ``None`` when the feature is disabled.

    The flag/budget are owned by ``magi_agent.config.env`` (single source);
    callers pass the resolved values here so the plugin module stays
    import-light and free of env-parsing concerns.
    """
    if not enabled:
        return None
    return MagiEditRetryReflectionPlugin(max_attempts=max_attempts)


__all__ = [
    "EDIT_RETRY_REFLECTION_PLUGIN_NAME",
    "EDIT_RETRY_REFLECTION_RESPONSE_TYPE",
    "EDIT_RETRY_STATE_NAMESPACE",
    "MagiEditRetryReflectionPlugin",
    "build_edit_retry_reflection_plugin",
    "scoped_state_name",
]
