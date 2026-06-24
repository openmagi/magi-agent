"""Capability-scope custom rule (F4).

A ``capability_scope`` rule fires at ``spawn`` lifecycle: when the runtime
derives the toolset for a spawned child agent, enabled capability_scope
rules further narrow the toolset by (a) removing named tools and (b) capping
the permission class. Always tighten-only — a rule can subtract from the
parent's resolved toolset, never widen it.

This is the *operator authoring surface* on top of the runtime's
``MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED`` parent-cap intersection: parent-cap
is "the child cannot exceed the spawning agent's tools", while capability_scope
is "the operator can declare additional caps that apply to every spawn,
regardless of the spawning agent". CC / opencode have no equivalent authoring
surface because they lack the runtime intersection mechanism.

Payload shape (v1, minimal):

    {
        "denyTools": [str, ...],              # exact tool names to remove
        "maxPermissionClass": "readonly"      # or "safe_write" or null
            | "safe_write" | null,
        "tightenOnly": true,                  # required True; widening rejected
    }

How ``maxPermissionClass`` is enforced (BLOCKER-fix, F4 honesty contract)
------------------------------------------------------------------------
The class is enforced at the spawn boundary by INTERSECTING the resolved
profile_tools with the class's allowed tool-name set (see
``tools_allowed_under_class``):

* ``"readonly"``   → intersect with read-only inspection tools
  (FileRead / Glob / Grep / GitDiff) — sourced from the canonical
  :data:`magi_agent.tools.local_readonly.LOCAL_READONLY_TOOL_NAMES`.
* ``"safe_write"`` → intersect with readonly ∪ edit-class tools
  (FileEdit / FileWrite / Edit / Write / ApplyPatch) — sourced from
  :data:`magi_agent.cli.permissions.EDIT_CLASS_TOOLS`. Bash / shell_exec
  / generic exec tools are dropped.
* ``None`` / absent → no further filtering (uncapped).

This is the observable, runtime-enforced behavior that the dashboard's
``Subagents ... capped at readonly permission class`` preview now actually
delivers — not just a UI label.

Deferred for follow-up:
    * ``appliesToRole``: child-agent role matching (requires a role taxonomy
      surfaced from the runtime ChildTaskRequest; not present today).
    * ``denyToolGroups``: requires a tool-group taxonomy authored separately.

Flag-gated by ``MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED`` + the master
``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` / ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``
profile flags. OFF keeps the toolset resolution byte-identical to pre-F4.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_PERMISSION_CLASS_ORDER: dict[str, int] = {
    # Lower = narrower. ``None`` (uncapped) is treated as +infinity.
    "readonly": 0,
    "safe_write": 1,
}

_PERMISSION_CLASSES = frozenset(_PERMISSION_CLASS_ORDER)


def tools_allowed_under_class(permission_class: str | None) -> frozenset[str] | None:
    """Return the tool-name set permitted under ``permission_class``.

    * ``"readonly"``   → ``LOCAL_READONLY_TOOL_NAMES``
      (FileRead / Glob / Grep / GitDiff).
    * ``"safe_write"`` → readonly ∪ ``EDIT_CLASS_TOOLS`` (file-mutating tools
      only; Bash / shell_exec / generic exec are excluded).
    * ``None`` / absent / unrecognised → ``None`` (uncapped — no filter).

    Imports are lazy so this module stays import-clean (callable from the
    spawn boundary without dragging in the full tool runtime).
    """
    if permission_class is None:
        return None
    # Lazy imports to keep the customize module import-clean.
    from magi_agent.tools.local_readonly import (  # noqa: PLC0415
        LOCAL_READONLY_TOOL_NAMES,
    )

    if permission_class == "readonly":
        return frozenset(LOCAL_READONLY_TOOL_NAMES)
    if permission_class == "safe_write":
        from magi_agent.cli.permissions import EDIT_CLASS_TOOLS  # noqa: PLC0415

        return frozenset(LOCAL_READONLY_TOOL_NAMES) | EDIT_CLASS_TOOLS
    # Unrecognised — fail-open to "uncapped"; validation rejects this at
    # author time, so reaching here means a stored rule slipped past
    # validation. Don't crash the spawn.
    return None


def apply_permission_class_filter(
    tools: Iterable[object],
    *,
    permission_class: str | None,
    tool_name_fn: Any,
) -> list[object]:
    """Intersect ``tools`` with the tool-name set permitted under
    ``permission_class``. ``None`` / unrecognised → no filter (uncapped).

    Pure: no I/O, no flag reads (caller decides gating).
    """
    allowed = tools_allowed_under_class(permission_class)
    if allowed is None:
        return list(tools)
    return [t for t in tools if tool_name_fn(t) in allowed]


def validate_capability_scope_payload(payload: Any) -> list[str]:
    """Return validation errors for a ``capability_scope`` rule payload.

    Empty list = valid. Caller (``custom_rules.validate_custom_rule``)
    appends these to the master rule errors.

    Validation rules:
    - ``denyTools`` must be a list of unique non-empty strings, or absent.
    - ``maxPermissionClass`` must be ``"readonly"`` / ``"safe_write"`` /
      ``None`` / absent.
    - ``tightenOnly`` must be present and ``True`` — explicit declaration that
      this rule cannot widen the parent's resolved toolset. The field exists
      to make widening attempts (``tightenOnly: false``) impossible to author.
    - At least ONE of ``denyTools`` or ``maxPermissionClass`` must be set so
      the rule does something.
    """
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["capability_scope payload must be an object"]

    deny_tools = payload.get("denyTools")
    if deny_tools is not None:
        if not isinstance(deny_tools, list):
            errors.append("denyTools must be a list of tool name strings")
        else:
            seen: set[str] = set()
            for index, entry in enumerate(deny_tools):
                if not isinstance(entry, str) or not entry.strip():
                    errors.append(
                        f"denyTools[{index}] must be a non-empty string"
                    )
                    continue
                if entry in seen:
                    errors.append(
                        f"denyTools[{index}] = {entry!r} is a duplicate"
                    )
                seen.add(entry)

    max_class = payload.get("maxPermissionClass")
    if max_class is not None and max_class not in _PERMISSION_CLASSES:
        errors.append(
            f"maxPermissionClass must be one of {sorted(_PERMISSION_CLASSES)} "
            "or null"
        )

    if "tightenOnly" not in payload:
        errors.append(
            "tightenOnly must be present and true (capability_scope cannot widen)"
        )
    elif payload.get("tightenOnly") is not True:
        errors.append(
            "tightenOnly must be true — capability_scope can only narrow, never widen"
        )

    has_deny = isinstance(deny_tools, list) and len(deny_tools) > 0
    has_cap = max_class is not None
    if not (has_deny or has_cap):
        errors.append(
            "capability_scope payload must set at least one of denyTools or "
            "maxPermissionClass — otherwise the rule does nothing"
        )

    return errors


def apply_capability_scope(
    tools: Iterable[object],
    *,
    rules: Iterable[Mapping[str, Any]],
    tool_name_fn: Any,
    current_permission_class: str | None = None,
) -> tuple[list[object], str | None]:
    """Apply enabled ``capability_scope`` rules to a resolved toolset.

    Returns ``(narrowed_tools, narrowed_permission_class)``:
    - ``narrowed_tools``: ``tools`` with ``denyTools`` from every enabled rule
      removed. Tool identity is decided by ``tool_name_fn(tool)``.
    - ``narrowed_permission_class``: the lower (= narrower) of the input
      ``current_permission_class`` and every rule's ``maxPermissionClass``;
      ``None`` means uncapped.

    The function is pure: no I/O, no flag reads (caller decides flag gating).
    Multiple rules COMPOSE — each rule narrows independently; no rule can
    widen another rule's effect (tighten-only invariant).
    """
    tools_list = list(tools)
    deny: set[str] = set()
    cap_class = current_permission_class
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        what = rule.get("what")
        if not isinstance(what, Mapping):
            continue
        payload = what.get("payload")
        if not isinstance(payload, Mapping):
            continue
        for entry in payload.get("denyTools") or []:
            if isinstance(entry, str) and entry.strip():
                deny.add(entry)
        rule_cap = payload.get("maxPermissionClass")
        if isinstance(rule_cap, str) and rule_cap in _PERMISSION_CLASSES:
            cap_class = _narrower_class(cap_class, rule_cap)

    if deny:
        tools_list = [t for t in tools_list if tool_name_fn(t) not in deny]
    return tools_list, cap_class


def _narrower_class(a: str | None, b: str | None) -> str | None:
    """Return the narrower (= lower-ordered) of two permission classes.

    ``None`` is treated as uncapped (= maximum), so any concrete class beats
    ``None``. When both are concrete, the lower-ordered (more restrictive)
    wins.
    """
    if a is None:
        return b
    if b is None:
        return a
    return a if _PERMISSION_CLASS_ORDER[a] <= _PERMISSION_CLASS_ORDER[b] else b
