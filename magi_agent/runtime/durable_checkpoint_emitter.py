"""WS1 PR1c - persisted-aligned digests + checkpoint assembly (section 0.4).

This module computes a checkpoint's digests as CONCRETE PERSISTED VALUES over
CRASH-SURVIVING bytes, aligned at emit time by the component that owns the bytes
(the SessionLog-owning headless tap), so the boot sweep (PR1d) recomputes the
IDENTICAL digests from the IDENTICAL persisted inputs. :func:`compute_persisted_digests`
is the SINGLE function emit (tap) and recovery (boot sweep) share - there is no
fork. This is the section 0.4 correctness core.

Inputs are read ONLY from durable on-disk state:
- the Envelope log (``cli/session_log``) truncated at ``watermark_uuid``;
- the per-session evidence JSONL pinned by ``evidence_line_count`` (read via
  :class:`EvidenceLedgerReader`, whose :meth:`read` skips blank/torn lines and
  returns LOGICAL dict rows, so the digest is over re-serialized logical rows -
  NOT raw file bytes mixed with a logical line count, avoiding the desync hazard
  minor #6).

The evidence dir is resolved as a PURE function of the ``cwd`` argument on BOTH
emit and boot (the v1 continuation is CLI-only, section 3.4), never the serve
resolver variant (minor #7). It mirrors the writer's
:func:`resolve_evidence_ledger_dir` semantics (same ``MAGI_EVIDENCE_LEDGER_DIR``
env, same disable tokens) EXCEPT the unset/empty fallback derives from the
``cwd`` argument (``Path(cwd)/".magi"/"evidence"``) instead of ``Path.cwd()``.
That difference is the correctness fix: the boot sweep (PR1d) runs in a process
whose cwd differs from emit and may have the env var unset, so a ``Path.cwd()``
fallback would recompute a divergent ``ledger_head_digest`` and refuse the
resume forever. Deriving from the ``cwd`` argument keeps the digest a function
of inputs + durable bytes only.

The ``effective_policy_snapshot_digest`` is a FIXED SENTINEL: the 11
``build_effective_policy_snapshot`` inputs cannot be reconstructed at cold boot
(section 0.4 mechanism 2). Fail-closed via ``policy_available`` (True only when
the snapshot genuinely builds, which it never does here at emit time).
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from magi_agent.cli.session_log import (
    Envelope,
    load,
    reconstruct_linear_chain,
    reconstruct_messages,
    resolve_session_path,
)
from magi_agent.evidence.ledger_store import (
    EVIDENCE_LEDGER_DIR_ENV,
    EvidenceLedgerReader,
)
from magi_agent.runtime.checkpointing import ExecutionCheckpoint

__all__ = [
    "POLICY_SNAPSHOT_SENTINEL",
    "CheckpointDigests",
    "compute_persisted_digests",
    "build_checkpoint",
]

_DIGEST_PREFIX = "sha256:"

# Fixed sentinel for the policy dimension. A deterministic, non-secret marker
# hashed to a stable sha256: value so the ExecutionCheckpoint schema validator
# passes and the digest-equality clause of verify_resume_request does not
# constrain (the policy fail-closed rides on effectivePolicySnapshotAvailable).
POLICY_SNAPSHOT_SENTINEL: str = _DIGEST_PREFIX + hashlib.sha256(
    b"magi.ws1.effective_policy_snapshot.sentinel.v1"
).hexdigest()


@dataclass(frozen=True)
class CheckpointDigests:
    """The four checkpoint digests + the policy-availability fail-closed flag.

    ``state_digest``/``ledger_head_digest``/``context_projection_digest`` are
    persisted-aligned real sha256 digests; ``effective_policy_snapshot_digest``
    is the fixed sentinel. ``policy_available`` is the conservative
    ``effectivePolicySnapshotAvailable`` input (True only when the snapshot
    genuinely builds at emit time).
    """

    state_digest: str
    ledger_head_digest: str
    effective_policy_snapshot_digest: str
    context_projection_digest: str
    policy_available: bool


# Disable tokens, mirrored EXACTLY from ledger_store._DISABLE_TOKENS so the
# cwd-pure resolver below honours the same MAGI_EVIDENCE_LEDGER_DIR off-switch
# the writer (write_evidence_records via resolve_evidence_ledger_dir) honours.
_EVIDENCE_DISABLE_TOKENS = frozenset(
    {"off", "0", "false", "none", "disable", "disabled"}
)


def _resolve_evidence_dir_from_cwd(cwd: str, env: Mapping[str, str]) -> Path | None:
    """Resolve the evidence dir as a PURE function of ``cwd`` + ``env``.

    This is the correctness-critical reconciliation for cross-process boot
    (PR1d). ``ledger_store.resolve_evidence_ledger_dir`` falls back to
    ``Path.cwd() / ".magi" / "evidence"`` when ``MAGI_EVIDENCE_LEDGER_DIR`` is
    unset, which silently couples the digest to the PROCESS cwd. The boot sweep
    runs in a different process whose cwd differs from the emit-time process, so
    that fallback recomputes a divergent ``ledger_head_digest`` and refuses the
    resume forever.

    Here the unset/empty fallback is derived from the ``cwd`` ARGUMENT instead:
    ``Path(cwd) / ".magi" / "evidence"``. This matches the writer EXACTLY at
    emit time, because the writer resolves through ``Path.cwd()`` in the SAME
    process whose cwd equals the ``cwd`` value the tap passes (headless sets
    ``cwd = os.getcwd()`` and stores it in the DurableCheckpointStore ``cwd``
    column, so the emit-stored cwd and the boot-passed cwd are the same string
    that locates the evidence). A disable token returns ``None``; an explicit
    absolute path is honoured unchanged (already process-cwd-independent). The
    output depends ONLY on the arguments + durable on-disk bytes, never on
    process cwd.
    """
    raw = (env.get(EVIDENCE_LEDGER_DIR_ENV) or "").strip()
    if raw.lower() in _EVIDENCE_DISABLE_TOKENS:
        return None
    if raw:
        return Path(raw)
    return Path(cwd) / ".magi" / "evidence"


def _sha256(payload: str) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _truncate_chain_at(chain: list[Envelope], watermark_uuid: str) -> list[Envelope] | None:
    """Return the chain prefix up to and including ``watermark_uuid``.

    Returns ``None`` when the watermark is absent (a fresh-start signal: the
    chain was reorganized or the watermark line was lost). Never a guessed
    index (correction 4).
    """
    truncated: list[Envelope] = []
    for env in chain:
        truncated.append(env)
        if env.uuid == watermark_uuid:
            return truncated
    return None


def _derive_tool_state(prefix: list[Envelope]) -> tuple[tuple[str, ...], str | None]:
    """Reconstruct ``(pending_tool_ids, last_completed_tool_name)`` from a prefix.

    Tracks ``tool_start`` ids without a matching ``tool_end`` (pending) and the
    name of the most-recently-completed tool. Derived purely from the persisted
    ``tool_start``/``tool_end`` stream, so emit and boot agree.
    """
    pending: dict[str, str] = {}
    pending_order: list[str] = []
    last_completed_name: str | None = None
    for env in prefix:
        payload = env.payload if isinstance(env.payload, dict) else {}
        p_type = payload.get("type")
        call_id = payload.get("id")
        name = payload.get("name")
        if p_type == "tool_start" and isinstance(call_id, str):
            if call_id not in pending:
                pending[call_id] = name if isinstance(name, str) else ""
                pending_order.append(call_id)
        elif p_type == "tool_end" and isinstance(call_id, str):
            pending.pop(call_id, None)
            if call_id in pending_order:
                pending_order.remove(call_id)
            if isinstance(name, str):
                last_completed_name = name
    pending_ids = tuple(sorted(pending_order))
    return pending_ids, last_completed_name


def _emitted_text_len(prefix: list[Envelope]) -> int:
    """Character length of the assistant text folded from the prefix.

    Uses the EXISTING ``reconstruct_messages`` so emit and boot fold identically.
    """
    messages = reconstruct_messages(prefix)
    return sum(len(m.get("content", "")) for m in messages if m.get("role") == "assistant")


def _ledger_logical_prefix(rows: list[dict], evidence_line_count: int) -> list[dict]:
    """First ``evidence_line_count`` LOGICAL rows (clamped to what exists)."""
    if evidence_line_count <= 0:
        return []
    return rows[:evidence_line_count]


def compute_persisted_digests(
    *,
    session_id: str,
    cwd: str,
    watermark_uuid: str | None,
    evidence_line_count: int,
    bot_id: str = "",
    env: Mapping[str, str] | None = None,
) -> CheckpointDigests:
    """Compute the persisted-aligned checkpoint digests over crash-surviving bytes.

    THE one function emit (headless tap) and recovery (boot sweep) both call.
    Reads the Envelope prefix truncated at ``watermark_uuid`` + the persisted
    evidence JSONL pinned by ``evidence_line_count``; returns real sha256:
    digests for state/ledger/context plus the fixed policy sentinel.

    When ``watermark_uuid`` is absent from the chain the prefix degrades to
    empty (fresh-start), so the digests describe an empty turn rather than
    raising.
    """
    # 1. Envelope prefix truncated at the watermark (uuid-only truncation).
    path = resolve_session_path(bot_id, session_id, cwd)
    chain = reconstruct_linear_chain(load(path))
    prefix: list[Envelope] = []
    if watermark_uuid is not None:
        maybe = _truncate_chain_at(chain, watermark_uuid)
        if maybe is not None:
            prefix = maybe

    pending_tool_ids, last_completed_name = _derive_tool_state(prefix)
    emitted_text_len = _emitted_text_len(prefix)

    # 2. Evidence logical-row prefix pinned by evidence_line_count.
    # cwd-PURE evidence-dir resolution (NOT resolve_evidence_ledger_dir's
    # Path.cwd() fallback): the dir is a function of the cwd ARGUMENT so the
    # boot sweep (PR1d), running in a different process whose cwd differs and
    # with MAGI_EVIDENCE_LEDGER_DIR unset, recomputes the IDENTICAL
    # ledger_head_digest from the IDENTICAL bytes instead of diverging.
    resolved_env: Mapping[str, str] = env if env is not None else os.environ
    base_dir = _resolve_evidence_dir_from_cwd(cwd, resolved_env)
    rows: list[dict] = []
    if base_dir is not None:
        reader = EvidenceLedgerReader(base_dir)
        rows = reader.read(session_id)
    ledger_prefix = _ledger_logical_prefix(rows, evidence_line_count)

    # state_digest: concrete persisted-input digest.
    state_payload = json.dumps(
        {
            "watermark_uuid": watermark_uuid,
            "pending_tool_ids": list(pending_tool_ids),
            "emitted_text_len": emitted_text_len,
            "evidence_line_count": evidence_line_count,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    state_digest = _sha256(state_payload)

    # ledger_head_digest: over re-serialized LOGICAL rows (NOT raw bytes), so a
    # trailing blank/torn line never causes a false mismatch.
    serialized_rows = "\n".join(
        json.dumps(row, sort_keys=True) for row in ledger_prefix
    )
    ledger_payload = json.dumps(
        {"line_count": len(ledger_prefix), "rows": serialized_rows},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    ledger_head_digest = _sha256(ledger_payload)

    # context_projection_digest: over the canonical text fold of the prefix.
    context_messages = reconstruct_messages(prefix)
    context_payload = json.dumps(
        context_messages, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    context_projection_digest = _sha256(context_payload)

    return CheckpointDigests(
        state_digest=state_digest,
        ledger_head_digest=ledger_head_digest,
        effective_policy_snapshot_digest=POLICY_SNAPSHOT_SENTINEL,
        context_projection_digest=context_projection_digest,
        # Fail-closed: at emit time (and at cold boot) the 11 selection-dependent
        # snapshot inputs are not available, so the snapshot does not genuinely
        # build. Always False here; verify_resume_request therefore refuses every
        # realistic resume on the dimension it governs (Correction F1).
        policy_available=False,
    )


def build_checkpoint(
    *,
    run_id: str,
    turn_id: str,
    step_id: str,
    digests: CheckpointDigests,
    resumable: bool,
    workflow_version: str = "ws1-durable-v1",
    checkpoint_id: str | None = None,
    pending_approval_refs: tuple[str, ...] = (),
    created_at: datetime | None = None,
) -> ExecutionCheckpoint:
    """Assemble a frozen :class:`ExecutionCheckpoint` from persisted digests.

    The four digest fields carry the real persisted-aligned values (plus the
    policy sentinel), so the schema validator passes and the section 10
    follow-up / background-admission path can re-verify them. ``resumable``
    comes from the section 0.5 side-effect classifier.
    """
    cid = checkpoint_id if checkpoint_id is not None else f"ckpt-{run_id}-{turn_id}-{step_id}"
    ts = created_at if created_at is not None else datetime.now(timezone.utc)
    return ExecutionCheckpoint(
        runId=run_id,
        checkpointId=cid,
        stepId=step_id,
        workflowVersion=workflow_version,
        stateDigest=digests.state_digest,
        ledgerHeadDigest=digests.ledger_head_digest,
        effectivePolicySnapshotDigest=digests.effective_policy_snapshot_digest,
        contextProjectionDigest=digests.context_projection_digest,
        pendingApprovalRefs=pending_approval_refs,
        resumable=resumable,
        createdAt=ts,
    )
