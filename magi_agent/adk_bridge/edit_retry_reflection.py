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
from collections.abc import Mapping
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.recipes.retry_repair_policies import coding_edit_retry_repair_rules
from magi_agent.runtime.turn_utilities import RetryController


EDIT_RETRY_REFLECTION_PLUGIN_NAME = "magi_edit_retry_reflection_plugin"

# Tools whose failures represent an "edit apply" attempt against file content.
_EDIT_TOOL_NAMES = frozenset({"FileEdit", "PatchApply"})

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


class MagiEditRetryReflectionPlugin(BasePlugin):
    """ADK plugin that re-injects corrective guidance after an edit failure.

    The plugin owns one :class:`RetryController` per tracking scope (the ADK
    invocation id) so that ``max_attempts`` is enforced per turn and never grows
    unbounded across turns. The controller and the repair rules are reused
    as-is; this plugin only adapts the ADK tool boundary to them.
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
        # attempt counts keyed by (scope_key, tool_name)
        self._attempts: dict[tuple[str, str], int] = {}
        # One controller for the whole plugin: repair-rule selection is pure (it
        # depends only on the per-call decision input), and the controller's own
        # ``exhausted``/``last_error`` state is unused here — the attempt budget
        # is enforced via the external ``_attempts`` counter we feed in. Caching
        # avoids allocating rule objects on every failed edit.
        self._controller = RetryController(
            max_attempts=self.max_attempts,
            repair_rules=coding_edit_retry_repair_rules(),
        )

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
        ``_attempts`` would grow unbounded: keys only pop on a *successful* edit
        (``_reset``), never when the budget is exhausted or the turn simply ends
        without a final success. The invocation id is the scope key, so on run
        completion we drop every counter belonging to that invocation.
        """
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._attempts = {k: v for k, v in self._attempts.items() if k[0] != inv}

    # -- core -------------------------------------------------------------

    def _maybe_reflect(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        reason: str,
    ) -> dict[str, Any] | None:
        tool_name = _tool_name(tool)
        if tool_name not in _EDIT_TOOL_NAMES:
            return None

        scope_key = _scope_key(tool_context)
        attempt = self._attempts.get((scope_key, tool_name), 0) + 1
        self._attempts[(scope_key, tool_name)] = attempt

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

    def _reset(self, tool_context: Any, tool_name: str) -> None:
        self._attempts.pop((_scope_key(tool_context), tool_name), None)


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
    "MagiEditRetryReflectionPlugin",
    "build_edit_retry_reflection_plugin",
]
