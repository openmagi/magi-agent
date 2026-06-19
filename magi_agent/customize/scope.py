"""Single source of truth for scope vocabulary + task-type → scope mapping.

A "scope" partitions customize enforcement by *kind of turn*. The vocabulary
matches :mod:`magi_agent.customize.custom_rules` (six values: ``always``,
``coding``, ``research``, ``delivery``, ``memory``, ``task``) so the schema
exposed in the UI (``custom_rules[].scope``) and the schema exposed on preset
catalog rows (``PresetSeam.scope``) speak the same language.

The runtime computes a single ``current_scope`` per turn from the harness's
``taskProfile.taskTypes`` and filters preset / custom-rule enforcement by it.
``always`` is the universal scope — refs marked ``always`` apply to every turn.

This module owns the vocabulary and the mapping so engine code, customize
enforcement, and tests cannot drift.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final

#: The full scope vocabulary, shared with :mod:`custom_rules`.
SCOPES: Final[frozenset[str]] = frozenset(
    {"always", "coding", "research", "delivery", "memory", "task"}
)

#: ``always`` applies to every turn regardless of the turn's scope.
ALWAYS_SCOPE: Final[str] = "always"

#: Default current-scope when no harness signal is present.
DEFAULT_CURRENT_SCOPE: Final[str] = "always"


# A taskType maps to at most one scope (a turn is one kind of work). ``always``
# is not in this table — it is the universal fallback, never matched by a
# taskType alone.
TASK_TYPE_TO_SCOPE: Final[dict[str, str]] = {
    # coding
    "coding": "coding",
    # research
    "research": "research",
    "web-acquisition": "research",
    "browser-automation": "research",
    # delivery
    "artifact-delivery": "delivery",
    "document": "delivery",
    "office": "delivery",
    "spreadsheet": "delivery",
    "telegram": "delivery",
    # memory
    "learning": "memory",
    "self-improvement": "memory",
    # task / automation
    "mission": "task",
    "scheduled-work": "task",
    "automation": "task",
    "workflow": "task",
    "superpowers": "task",
}


def scope_for_task_type(task_type: str) -> str | None:
    """Return the scope a task type maps to, or ``None`` if not classified."""
    return TASK_TYPE_TO_SCOPE.get(task_type.strip().lower())


def current_scope_from_task_profile(
    task_profile: Mapping[str, object] | None,
) -> str:
    """Compute the single scope for a turn from its ``taskProfile.taskTypes``.

    Resolution rules (deterministic, no LLM):

    * If ``taskTypes`` is missing/empty → :data:`DEFAULT_CURRENT_SCOPE`
      (``always``) so the un-classified turn flows through the universal layer.
    * If any task type maps to a coding scope → ``coding`` (coding signals
      always win — a turn that edits code is a coding turn).
    * Otherwise the first classified task type's scope is returned.
    * Unknown task types are ignored.
    """
    if not isinstance(task_profile, Mapping):
        return DEFAULT_CURRENT_SCOPE
    raw = task_profile.get("taskTypes") or task_profile.get("task_types") or ()
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return DEFAULT_CURRENT_SCOPE

    matched: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        scope = scope_for_task_type(item)
        if scope is not None:
            matched.append(scope)
    if not matched:
        return DEFAULT_CURRENT_SCOPE
    if "coding" in matched:
        return "coding"
    return matched[0]


def preset_scope_matches(
    preset_scopes: Iterable[str],
    current_scope: str,
) -> bool:
    """A preset applies when its scope tuple contains the current scope or ``always``.

    Multi-scope presets (e.g. a security preset listed under both ``always`` and
    ``coding``) match every turn the way ``always`` does — listing ``always`` in
    the tuple is sufficient.
    """
    scopes = tuple(preset_scopes)
    if not scopes:
        return True  # missing tuple ⇒ legacy preset; do not regress
    if ALWAYS_SCOPE in scopes:
        return True
    return current_scope in scopes
