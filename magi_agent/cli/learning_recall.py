"""CLI learning-recall block builder.

Retrieves active learnings from the local SqliteLearningStore and formats
them as a markdown block for injection into the CLI system prompt.

This is the integration point that closes the self-improvement loop for the
local / CLI prompt-assembly path.  The hosted multi-tenant path is
intentionally out of scope (governed by a separate gate5b policy).

Gate: ``resolve_learning_config().injection_effective``
(``MAGI_LEARNING_INJECTION_ENABLED``, default-OFF).  When the gate is off
(the default) this module returns ``""`` for every call, preserving byte-
identical behaviour relative to the pre-wiring baseline.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Scope task_kind used by the deterministic labeler for all CLI-originated
#: learnings.  Matches ``magi_agent.learning.labeler`` which writes items
#: with ``taskKind="general"`` (see labeler.py line 222).
_CLI_LEARNING_SCOPE_TASK_KIND = "general"

_BLOCK_HEADER = "## Learned from past sessions"


def build_cli_learning_recall_block(
    *,
    workspace_root: str | None,
    memory_mode: str,
) -> str:
    """Return a markdown block of active learnings, or ``""`` when suppressed.

    All early-exit conditions (gate off, incognito mode, missing db, empty
    store, any error) return ``""`` — the caller always receives a plain
    string and the function never raises.

    Args:
        workspace_root: Absolute path to the workspace root.  ``None`` means
            the agent is running without a workspace (e.g. bare CLI with no
            project directory); no learning db exists, so return ``""``.
        memory_mode: The session memory mode string (``"normal"``,
            ``"read_only"``, ``"incognito"``).  Incognito suppresses learning
            injection in the same way it suppresses memory-snapshot injection.

    Returns:
        A non-empty markdown block string when learnings are available and the
        gate is on, otherwise ``""``.
    """
    # --- guard: no workspace ---
    if workspace_root is None:
        return ""

    # --- guard: incognito memory mode ---
    from magi_agent.tools.memory_mode_guard import is_incognito_memory_mode  # noqa: PLC0415

    if is_incognito_memory_mode(memory_mode):
        return ""

    # --- guard: injection gate (default-OFF) ---
    from magi_agent.learning.config import resolve_learning_config  # noqa: PLC0415

    try:
        cfg = resolve_learning_config()
    except Exception:
        logger.debug("resolve_learning_config() failed; skipping learning recall", exc_info=True)
        return ""

    if not cfg.injection_effective:
        return ""

    # --- build result (any error here must be non-fatal) ---
    try:
        return _build_block(workspace_root=workspace_root)
    except Exception:
        logger.debug("Learning recall failed; skipping", exc_info=True)
        return ""


def _build_block(*, workspace_root: str) -> str:
    """Inner implementation — may raise; caller wraps in try/except."""
    from pathlib import Path  # noqa: PLC0415

    from magi_agent.learning.store import DEFAULT_LEARNING_DB_PATH, SqliteLearningStore  # noqa: PLC0415

    # --- guard: db file must exist (no learnings yet on a fresh workspace) ---
    db_path = Path(workspace_root) / DEFAULT_LEARNING_DB_PATH
    if not db_path.exists():
        return ""

    from magi_agent.learning.injection import build_learning_recall_payload  # noqa: PLC0415
    from magi_agent.learning.models import LearningScope  # noqa: PLC0415

    store = SqliteLearningStore(
        db_path=DEFAULT_LEARNING_DB_PATH,
        workspace_root=workspace_root,
    )
    scope = LearningScope(taskKind=_CLI_LEARNING_SCOPE_TASK_KIND)
    entries = build_learning_recall_payload(
        store,
        tenant_id="local",
        scope=scope,
        k=8,
    )

    if not entries:
        return ""

    lines = [_BLOCK_HEADER]
    for entry in entries:
        lines.append(f"- {entry.text}")
    return "\n".join(lines)


__all__ = ["build_cli_learning_recall_block"]
