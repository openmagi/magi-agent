"""Single source of truth for the scope vocabulary.

A "scope" is the ``custom_rules[].scope`` value. The vocabulary matches
:mod:`magi_agent.customize.custom_rules` (six values: ``always``, ``coding``,
``research``, ``delivery``, ``memory``, ``task``) so the schema exposed in the
UI speaks the same language.

PR-P5.3: the auto turn-scope classifier (``current_scope_from_task_profile``,
``scope_for_task_type``, ``TASK_TYPE_TO_SCOPE``) and ``preset_scope_matches``
were removed. They had zero production callers: the auto turn-scope axis was
never wired to enforcement (the runtime is scope-blind), and the axis is now
retired: a rule applies globally, or only while a user-selected mode scopes it
in. Only the vocabulary (``SCOPES`` / ``ALWAYS_SCOPE``, still consumed by
``verification_policy._rule_scope_matches`` for legacy scoped values) remains.
"""
from __future__ import annotations

from typing import Final

#: The full scope vocabulary, shared with :mod:`custom_rules`.
SCOPES: Final[frozenset[str]] = frozenset(
    {"always", "coding", "research", "delivery", "memory", "task"}
)

#: ``always`` applies to every turn regardless of the turn's scope.
ALWAYS_SCOPE: Final[str] = "always"
