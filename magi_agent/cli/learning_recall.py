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
#: Scope limitation: only "general" learnings surface today because the
#: labeler writes all items under that kind.  To surface per-task-kind
#: learnings, thread the current task kind into build_cli_learning_recall_block
#: and down to _build_block's LearningScope here.
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

    # Everything below is wrapped so that ANY exception (including import
    # failures of memory_mode_guard or learning.config) returns "" and never
    # propagates to the caller.
    try:
        # --- guard: incognito memory mode ---
        from magi_agent.tools.memory_mode_guard import (  # noqa: PLC0415
            is_incognito_memory_mode,
        )

        if is_incognito_memory_mode(memory_mode):
            return ""

        # --- guard: injection gate (default-OFF) ---
        from magi_agent.learning.config import resolve_learning_config  # noqa: PLC0415

        cfg = resolve_learning_config()
        if not cfg.injection_effective:
            return ""

        # --- build result ---
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
    try:
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
    finally:
        store.close()


_LIVE_BLOCK_HEADER = "<learning-live-recall>"
_LIVE_BLOCK_FOOTER = "</learning-live-recall>"


def build_serve_live_learning_recall_block(
    *,
    workspace_root: str | None,
    recall_query: str,
    memory_mode: str,
    bot_id: str,
    user_id: str,
    readiness: object | None,
) -> str:
    """Return a ``<learning-live-recall>`` block, or ``""`` when suppressed.

    This is the SERVE consumer of the gated-live learning-recall harness
    (``harness/memory_recall.build_gated_live_learning_recall_harness``), which
    previously had ZERO serve callers — the live recall path was built + unit
    tested at the factory level but never wired into prompt assembly (the
    unified-rag B1 gap).  ``build_cli_instruction`` calls this so the serve
    prompt-assembly path (``transport.chat._local_adk_chat_sse`` →
    ``build_headless_runtime`` → ``build_cli_instruction``) actually consults
    the live recall harness when the learning-live readiness ladder is on.

    Spec PR4 default-state: *no new flags — consume the existing
    ``MAGI_LEARNING_LIVE_ENABLED`` + readiness config*.  Accordingly the
    ``readiness`` config is PROVIDED by the caller (the runtime/control-plane
    that already owns the selected-scope canary digests + environment); this
    seam never reads any net-new ``MAGI_LEARNING_LIVE_*`` env var.  Default-OFF
    is PRESERVED two ways:

    * ``readiness is None`` (the default CLI/local case) → no live binding;
    * the env gate ``MAGI_LEARNING_LIVE_ENABLED`` (default OFF) hard
      short-circuits ``resolve_learning_live_execution_mode`` to ``disabled``
      inside the gated factory regardless of the readiness config.

    The gated factory returns ``None`` for ``disabled``/``shadow`` (observe-only
    is not a serve injection), so this returns ``""`` and the prompt is
    byte-identical to before this seam existed.  Real ``bot_id``/``user_id`` are
    threaded from the serve caller so the canary digest match resolves against
    the genuine identity (not the literal ``"local"`` default).

    All early-exit conditions (no workspace, no readiness, incognito mode, gate
    disabled, shadow mode, no live binding, empty/blocked recall, any error)
    return ``""`` — the caller always receives a plain string and the function
    never raises.

    No ``Literal[False]`` authority flag is flipped — live behaviour is purely
    gate-derived through the existing readiness ladder + audit path.
    """
    if workspace_root is None or readiness is None:
        return ""

    try:
        from magi_agent.tools.memory_mode_guard import (  # noqa: PLC0415
            is_incognito_memory_mode,
        )

        if is_incognito_memory_mode(memory_mode):
            return ""

        from magi_agent.harness.memory_recall import (  # noqa: PLC0415
            build_gated_live_learning_recall_harness,
        )
        from magi_agent.learning.injection import (  # noqa: PLC0415
            DEFAULT_LEARNING_NAMESPACE_REF,
        )
        from magi_agent.learning.store import (  # noqa: PLC0415
            DEFAULT_LEARNING_DB_PATH,
            SqliteLearningStore,
        )

        from pathlib import Path  # noqa: PLC0415

        db_path = Path(workspace_root) / DEFAULT_LEARNING_DB_PATH
        if not db_path.exists():
            return ""

        store = SqliteLearningStore(
            db_path=DEFAULT_LEARNING_DB_PATH,
            workspace_root=workspace_root,
        )
        try:
            harness = build_gated_live_learning_recall_harness(
                store=store,
                readiness=readiness,
                bot_id=bot_id,
                user_id=user_id,
                namespace_ref=DEFAULT_LEARNING_NAMESPACE_REF,
            )
            # ``None`` == disabled/shadow → no live binding → no serve block.
            if harness is None:
                return ""
            snippets = _run_serve_live_recall(
                harness=harness,
                recall_query=recall_query,
                namespace_ref=DEFAULT_LEARNING_NAMESPACE_REF,
            )
        finally:
            store.close()

        if not snippets:
            return ""
        lines = [_LIVE_BLOCK_HEADER]
        lines.extend(f"- {snippet}" for snippet in snippets)
        lines.append(_LIVE_BLOCK_FOOTER)
        return "\n".join(lines)
    except Exception:
        logger.debug("Live learning recall failed; skipping", exc_info=True)
        return ""


def build_serve_live_learning_write_audit(
    *,
    workspace_root: str | None,
    memory_mode: str,
    bot_id: str,
    user_id: str,
    readiness: object | None,
) -> dict[str, object] | None:
    """Return a PUBLIC-SAFE write-audit projection, or ``None`` when suppressed.

    Spec PR4 file-map requires *write 대칭* (write symmetry) and test (c)
    requires a *write audit* on the live path: ``build_gated_live_learning_
    write_harness`` (``harness/memory_write.py``) ALSO had ZERO serve callers.
    This is its serve consumer — it mirrors the recall seam exactly:

    * ``readiness is None`` / ``disabled`` / ``shadow`` → ``None`` (no write,
      no audit — observe-only is not a serve write);
    * ``live`` (env gate ON + caller readiness resolves live for the real
      identity) → run the gated write harness and return its PUBLIC-SAFE
      ``public_projection()`` audit dict.

    Every ``Literal[False]`` authority flag on the write harness stays
    frozen-False even on the live path — the audit proves the seam reached the
    write harness, never that an authority flag flipped.  Fail-soft: any error
    returns ``None`` and the function never raises.
    """
    if workspace_root is None or readiness is None:
        return None

    try:
        from magi_agent.tools.memory_mode_guard import (  # noqa: PLC0415
            is_incognito_memory_mode,
        )

        if is_incognito_memory_mode(memory_mode):
            return None

        from magi_agent.harness.memory_write import (  # noqa: PLC0415
            build_gated_live_learning_write_harness,
        )

        harness = build_gated_live_learning_write_harness(
            readiness=readiness,
            bot_id=bot_id,
            user_id=user_id,
        )
        # ``None`` == disabled/shadow → no live binding → no serve write/audit.
        if harness is None:
            return None
        audit = _run_serve_live_write(harness=harness)
        if audit is not None:
            logger.debug("learning-live serve write audit: %s", audit)
        return audit
    except Exception:
        logger.debug("Live learning write audit failed; skipping", exc_info=True)
        return None


def _run_serve_live_write(*, harness: object) -> dict[str, object] | None:
    """Run the gated-live write harness and return its public-safe audit dict.

    Isolated so tests can monkeypatch the write execution.  The request is a
    declarative no-op anchor (a ``remember`` of the learning-live serve seam
    activation) — it exists only to exercise the write boundary + emit an audit
    record; the receipt/authority projection is what the serve caller logs.
    """
    import asyncio  # noqa: PLC0415

    from magi_agent.harness.memory_write import (  # noqa: PLC0415
        MemoryWritePolicy,
        MemoryWriteRequest,
    )

    request = MemoryWriteRequest(
        providerId="learning-live-serve",
        turnId="learning-live-serve",
        operation="remember",
        content="learning-live serve seam active",
    )
    policy = MemoryWritePolicy(
        policyRef="policy:learning-live-serve",
        policySnapshotRef="policy-snapshot:learning-live-serve",
        approvalRequired=False,
        evidenceRequired=False,
        localFakeSuccessAllowed=True,
    )
    result = asyncio.run(harness.write(request=request, policy=policy))
    return result.public_projection()


def _run_serve_live_recall(
    *,
    harness: object,
    recall_query: str,
    namespace_ref: str,
) -> tuple[str, ...]:
    """Run the gated-live recall harness and return PUBLIC-SAFE snippets.

    The recall harness deliberately withholds prompt text
    (``promptProjectionAllowed`` / ``promptText`` are ``Literal[False]``/``""``);
    the public-safe surface is the per-reference sanitized ``snippet``.  We
    project those snippets only, never raw bodies.  Isolated so tests can
    monkeypatch the live recall execution.
    """
    import asyncio  # noqa: PLC0415

    from magi_agent.memory.contracts import RecallRequest  # noqa: PLC0415
    from magi_agent.memory.namespaces import MemoryNamespacePolicy  # noqa: PLC0415
    from magi_agent.recipes.first_party.memory_recall import (  # noqa: PLC0415
        MemoryRecallProjectionPolicy,
    )

    request = RecallRequest(
        scope={
            "tenantId": "local",
            "botId": "local",
            "sessionKey": "serve-live-learning",
        },
        query=recall_query,
        purpose="answer_user",
    )
    result = asyncio.run(
        harness.recall(
            request=request,
            namespace_policy=MemoryNamespacePolicy(namespaceRef=namespace_ref),
            projection_policy=MemoryRecallProjectionPolicy(
                latestUserText=recall_query,
                maxBytes=2048,
                policySnapshotRef="policy-snapshot:learning-live-serve",
            ),
        )
    )
    if result.status != "allowed":
        return ()
    snippets = tuple(
        ref.snippet for ref in result.projection.references if ref.snippet
    )
    return snippets


__all__ = [
    "build_cli_learning_recall_block",
    "build_serve_live_learning_recall_block",
    "build_serve_live_learning_write_audit",
]
