"""F-MUT1 — ``prompt_injection`` custom_rule kind.

Adds the first mutator kind to the customize wizard's surface, exposing the
HookBus ``replace`` action shape as a constrained author surface. Two
lifecycle slots are honored today:

* ``before_tool_use`` — append a value to a chosen ``arguments`` key of a
  matched tool call BEFORE dispatch. Example: "append ``--dry-run`` to
  ``shell_exec.command``". Wired through :func:`apply_prompt_injection_to_tool_args`
  which is invoked from :func:`magi_agent.facades.execute_tool_with_hooks`
  after the ``before_result`` block branch (so a blocked call still fails
  closed).
* ``on_user_prompt_submit`` — append a value as a new section to the assembled
  system prompt. Example: "always append coding-standards context". Wired
  through :func:`apply_prompt_injection_to_prompt_sections` which is invoked
  alongside the existing :mod:`magi_agent.customize.lifecycle_audit` audit
  fan-out at the top of :func:`magi_agent.runtime.governed_turn.run_governed_turn`.

Author contract (validator below):

    {
      mode: "append"                                # v1 — only mode supported
      target_arg_key: str                           # before_tool_use only
      target: "system_prompt"                       # on_user_prompt_submit only
      value: str                                    # <= 4000 chars
      condition?: {regex?: str, tool?: str}        # optional pre-filter
    }

v1 explicitly rejects ``mode == "replace"`` (deferred to v2 with an admin-tier
flag) and caps ``value`` at 4000 characters to bound the prompt cost.

Apply contract:

* The two apply helpers are PURE — they take the inbound payload + a list of
  enabled rules and return the (possibly mutated) payload. Fail-safe-original
  on any rule-level error: a malformed rule never breaks the turn, it is
  silently dropped from the projection. Mirrors
  :func:`magi_agent.hooks.replace_payloads.coerce_replace_payload`'s
  "fail-safe to original on any validation error" semantics.
* Both helpers are no-ops when the master flag
  ``MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED`` is OFF; the caller is expected
  to check that flag before invoking (the helpers themselves are
  side-effect-free building blocks).
"""

from __future__ import annotations

import re
from typing import Any

# Hard cap on the value string (per spec §F-MUT1) — bounds prompt cost when an
# operator authors an unbounded value and prevents a runaway append loop.
VALUE_MAX = 4000


def validate_prompt_injection_payload(
    payload: Any, fires_at: Any
) -> list[str]:
    """Validate a ``prompt_injection.payload`` shape; return error list.

    Empty list means valid (matches the convention used by
    :func:`magi_agent.customize.custom_rules.validate_custom_rule`). The
    ``fires_at`` parameter selects which target shape is required:

    * ``before_tool_use`` — ``target_arg_key`` (non-empty str) is required;
      ``target`` is rejected.
    * ``on_user_prompt_submit`` — ``target`` must equal ``"system_prompt"``;
      ``target_arg_key`` is rejected.

    Mode ``"replace"`` is explicitly rejected with a pointer to the v2
    admin-tier flag deferral. ``value`` is capped at :data:`VALUE_MAX`.
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["prompt_injection.payload must be an object"]

    mode = payload.get("mode")
    if mode == "replace":
        errors.append(
            "prompt_injection.payload.mode 'replace' requires admin-tier "
            "flag (deferred to v2)"
        )
    elif mode != "append":
        errors.append(
            "prompt_injection.payload.mode must be 'append' (v1)"
        )

    value = payload.get("value")
    if not isinstance(value, str) or not value.strip():
        errors.append("prompt_injection.payload.value must be a non-empty string")
    elif len(value) > VALUE_MAX:
        errors.append(
            f"prompt_injection.payload.value exceeds the {VALUE_MAX}-char cap"
        )

    if fires_at == "before_tool_use":
        if "target" in payload:
            errors.append(
                "prompt_injection.payload.target is only valid for "
                "on_user_prompt_submit rules"
            )
        target_arg_key = payload.get("target_arg_key")
        if not isinstance(target_arg_key, str) or not target_arg_key.strip():
            errors.append(
                "prompt_injection.payload.target_arg_key is required "
                "(non-empty string) for before_tool_use rules"
            )
    elif fires_at == "on_user_prompt_submit":
        if "target_arg_key" in payload:
            errors.append(
                "prompt_injection.payload.target_arg_key is only valid for "
                "before_tool_use rules"
            )
        target = payload.get("target")
        if target != "system_prompt":
            errors.append(
                "prompt_injection.payload.target must be 'system_prompt' "
                "for on_user_prompt_submit rules"
            )
    else:
        errors.append(
            "prompt_injection rules may only fire at 'before_tool_use' or "
            "'on_user_prompt_submit'"
        )

    condition = payload.get("condition")
    if condition is not None:
        if not isinstance(condition, dict):
            errors.append("prompt_injection.payload.condition must be an object")
        else:
            regex = condition.get("regex")
            if regex is not None:
                if not isinstance(regex, str) or not regex.strip():
                    errors.append(
                        "prompt_injection.payload.condition.regex must be a "
                        "non-empty string if provided"
                    )
                else:
                    try:
                        re.compile(regex)
                    except re.error:
                        errors.append(
                            "prompt_injection.payload.condition.regex is "
                            "not a valid regex"
                        )
            tool = condition.get("tool")
            if tool is not None and (
                not isinstance(tool, str) or not tool.strip()
            ):
                errors.append(
                    "prompt_injection.payload.condition.tool must be a "
                    "non-empty string if provided"
                )

    return errors


def _payload(rule: dict[str, Any]) -> dict[str, Any] | None:
    """Extract ``what.payload`` from a rule dict, or ``None`` on shape error."""
    what = rule.get("what")
    if not isinstance(what, dict):
        return None
    payload = what.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _condition_matches_tool(
    payload: dict[str, Any], tool_name: str, args: dict[str, object]
) -> bool:
    """Return whether the optional condition pre-filter matches.

    The condition object is fully optional — when absent the rule fires
    unconditionally for every matched tool. When present:

    * ``condition.tool`` (str) — fire only when ``tool_name`` matches exactly.
    * ``condition.regex`` (str) — fire only when the regex matches the
      string-coerced ``args[target_arg_key]`` (or the empty string when the
      key is missing). Compilation/match errors fail-closed: rule is skipped.
    """
    condition = payload.get("condition")
    if not isinstance(condition, dict):
        return True
    tool_filter = condition.get("tool")
    if isinstance(tool_filter, str) and tool_filter.strip():
        if tool_name != tool_filter.strip():
            return False
    regex = condition.get("regex")
    if isinstance(regex, str) and regex.strip():
        target_arg_key = payload.get("target_arg_key")
        if not isinstance(target_arg_key, str):
            return False
        raw = args.get(target_arg_key)
        try:
            if not re.search(regex, "" if raw is None else str(raw)):
                return False
        except re.error:
            return False
    return True


def apply_prompt_injection_to_tool_args(
    args: dict[str, object],
    rules: list[dict[str, Any]],
    tool_name: str,
) -> dict[str, object]:
    """Return ``args`` mutated by every matching ``before_tool_use`` rule.

    Pure function — input ``args`` is NOT mutated; a new dict is returned. For
    each enabled ``prompt_injection`` rule with ``firesAt == "before_tool_use"``
    that passes :func:`_condition_matches_tool`, the rule's ``value`` is
    appended to ``args[target_arg_key]`` (string coercion if the existing
    value is non-string; the empty string when the key is missing). Multiple
    matching rules compose in stored order (last-write-wins is undefined —
    the contract is "append in declared order").

    Malformed rules are silently dropped so a buggy rule never wedges a tool
    call.
    """
    out = dict(args)
    for rule in rules:
        try:
            if not rule.get("enabled", False):
                continue
            if rule.get("firesAt") != "before_tool_use":
                continue
            what = rule.get("what")
            if not isinstance(what, dict) or what.get("kind") != "prompt_injection":
                continue
            payload = _payload(rule)
            if payload is None:
                continue
            if payload.get("mode") != "append":
                continue
            target_arg_key = payload.get("target_arg_key")
            value = payload.get("value")
            if not isinstance(target_arg_key, str) or not target_arg_key.strip():
                continue
            if not isinstance(value, str):
                continue
            if not _condition_matches_tool(payload, tool_name, out):
                continue
            existing = out.get(target_arg_key, "")
            existing_str = "" if existing is None else str(existing)
            out[target_arg_key] = existing_str + value
        except Exception:  # noqa: BLE001 — fail-safe per module docstring
            continue
    return out


def apply_prompt_injection_to_prompt_sections(
    sections: list[str],
    rules: list[dict[str, Any]],
) -> list[str]:
    """Return ``sections`` with every matching ``on_user_prompt_submit`` rule
    appended as a NEW section.

    Pure function — input list is NOT mutated; a new list is returned. For
    each enabled ``prompt_injection`` rule with ``firesAt ==
    "on_user_prompt_submit"`` whose target is ``"system_prompt"`` and whose
    ``mode == "append"``, the rule's ``value`` is appended as a fresh
    section. Authoring order is preserved.

    The optional ``condition`` object is unused at this slot today (there is
    no per-call tool context to filter on); a condition.regex pre-filter for
    the inbound user prompt may be added in a follow-up PR.

    Malformed rules are silently dropped so a buggy rule never wedges prompt
    assembly.
    """
    out = list(sections)
    for rule in rules:
        try:
            if not rule.get("enabled", False):
                continue
            if rule.get("firesAt") != "on_user_prompt_submit":
                continue
            what = rule.get("what")
            if not isinstance(what, dict) or what.get("kind") != "prompt_injection":
                continue
            payload = _payload(rule)
            if payload is None:
                continue
            if payload.get("mode") != "append":
                continue
            if payload.get("target") != "system_prompt":
                continue
            value = payload.get("value")
            if not isinstance(value, str) or not value:
                continue
            out.append(value)
        except Exception:  # noqa: BLE001 — fail-safe per module docstring
            continue
    return out


__all__ = [
    "VALUE_MAX",
    "apply_prompt_injection_to_prompt_sections",
    "apply_prompt_injection_to_tool_args",
    "validate_prompt_injection_payload",
]
