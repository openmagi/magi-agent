from __future__ import annotations

import re
import time
from collections.abc import Mapping

from magi_agent.evidence.extraction import evidence_records_from_tool_result
from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.evidence.types import EvidenceRecord
from magi_agent.tools.result import ToolResult


def _as_producer_control(record: object, producing_rule_id: str = "") -> object:
    """Re-stamp a runtime-control-written record with the trusted
    ``producer_control`` origin (+ producing rule id), safe-by-construction:
    the write path is the authority for provenance, never a caller-supplied
    record's declared origin. A record LIFTED from tool metadata never reaches
    this path (it keeps the default ``tool_declared``), so a tool cannot forge a
    ``producer_control`` record. Non-EvidenceRecord inputs pass through."""
    if isinstance(record, EvidenceRecord):
        return record.model_copy(
            update={"origin": "producer_control", "producing_rule_id": producing_rule_id}
        )
    return record


_PUBLIC_REF_PREFIXES = ("evidence:", "verifier:", "receipt:sha256:", "sha256:")
_SAFE_ARTIFACT_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_OUTPUT_ARTIFACT_REF_TOOL_NAMES = frozenset({"documentwrite", "filedeliver"})
_RECEIPT_METADATA_KEYS = (
    "toolExecutionReceipt",
    "codingMutationReceipt",
    "codeDiagnosticsReceipt",
    "gate5bFullToolhostReceipt",
    "generalAutomationReceipt",
    "generalAutomationReceiptLedgerEntry",
)
_TEST_COMMAND_PREFIXES = (
    "pytest",
    "python -m pytest",
    "npm test",
    "npm run test",
    "pnpm test",
    "pnpm run test",
    "yarn test",
)
# Cap on how many per-turn EvidenceLedgers ``evidence_ledgers_for_session``
# exposes to the live self-evidence view. The CLI ``tool_context_factory``
# calls it on EVERY tool dispatch and ``ToolContext.freeze_source_ledger``
# deep-freezes the whole returned tuple each time, so returning ALL ledgers is
# O(N^2) over a long session (N turns x M dispatches). Capping to the most
# recent K turns bounds per-dispatch cost while keeping recent cross-turn
# introspection ("did you read X earlier this session"). Older turns are
# deliberately dropped from the live self-evidence view — a lean cap, not an
# audit store.
_MAX_SESSION_LEDGERS = 25


def _public_record_projection(record: object) -> object:
    """JSON-safe projection of an evidence record for the durable sink."""
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", by_alias=True)
        except (TypeError, ValueError):
            return str(record)
    return str(record)


class LocalToolEvidenceCollector:
    """Local-only collector for CLI/dashboard tool receipts.

    The engine consumes a ``Callable[[turn_id], Sequence[object]]``. This class
    keeps that interface while retaining sanitized tool evidence produced by the
    OSS CLI/local dashboard tool path. It does not call providers, mutate hosted
    storage, or grant write authority.
    """

    def __init__(self, *, general_automation_receipts: object | None = None) -> None:
        # Keyed by (session_id, turn_id).  Contains BOTH tool-receipt records
        # (appended by ``record_tool_result``) and first-party-origin records
        # (appended by ``record_first_party_activity``).  The first-party prune
        # path (_prune_first_party_state) NEVER evicts non-first-party records —
        # it filters selectively by type prefix; only ``_prune_session_ledgers``
        # caps ``_ledgers``.
        self._records: dict[tuple[str, str], list[object]] = {}
        self._general_automation_receipts = general_automation_receipts
        # Per-(session_id, turn_id) immutable EvidenceLedgers, built lazily on
        # the first recorded tool result for a key when the lifecycle flag is on.
        # Reused by ``evidence_ledgers_for_session`` to thread onto a
        # ``ToolContext.source_ledger`` so ``InspectSelfEvidence`` projects REAL
        # tool calls. Empty (and never built) when the flag is off.
        self._ledgers: dict[tuple[str, str], EvidenceLedger] = {}
        # First-party activity dedup: (session_id, turn_id, skillPath, bodyDigest)
        self._first_party_skill_seen: set[tuple[str, str, str, str]] = set()
        # Insertion-ordered set of (session_id, turn_id) keys that have at least
        # one first-party record.  Used by ``_prune_first_party_state`` to bound
        # the first-party turn count WITHOUT touching non-first-party (tool
        # receipt) records that share the same (session, turn) key.
        self._first_party_turns: dict[tuple[str, str], None] = {}
        # Session-scoped source registries for MAGI_SOURCE_CITATION_ENABLED.
        # Keyed by session_id. One registry per session, created lazily on first
        # tool result with citation enabled.
        self._session_source_registries: dict[str, object] = {}
        # Per-session authored paths: files created/edited by the agent this
        # session. FileRead of an authored path is NOT registered as a source.
        # Fed by codingMutationReceipt entries and write tool arguments.
        self._session_authored_paths: dict[str, set[str]] = {}

    def record_tool_result(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        result: ToolResult | Mapping[str, object],
        arguments: Mapping[str, object] | None = None,
    ) -> tuple[object, ...]:
        tool_result = (
            result if isinstance(result, ToolResult) else ToolResult.model_validate(result)
        )
        records: list[object] = []

        # One tool call may declare several evidence records (e.g. an edit that
        # produces BOTH an EditMatch and a CodeDiagnostics receipt), so lift the
        # full list rather than a single declaration.
        explicit_records = evidence_records_from_tool_result(
            tool_result,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        records.extend(explicit_records)

        # UPDATE authored-paths set from coding mutation receipts (for file exclusion).
        # Must happen BEFORE citation capture so a FileRead in the same batch
        # after an edit correctly excludes the edited file.
        _update_authored_paths(
            authored_paths=self._session_authored_paths.setdefault(session_id, set()),
            tool_result=tool_result,
            tool_name=tool_name,
            arguments=arguments or {},
        )

        # Citation capture (MAGI_SOURCE_CITATION_ENABLED, profile-aware default-ON).
        # Classifies the tool result for external-read sources, registers each in
        # the session registry, and emits producer_control EvidenceRecords.
        # Fail-quiet: any error leaves records unchanged and does not break the tool path.
        citation_records = _citation_capture_records(
            session_id=session_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_result=tool_result,
            arguments=arguments or {},
            registries=self._session_source_registries,
            authored_paths=frozenset(self._session_authored_paths.get(session_id, set())),
        )
        records.extend(citation_records)

        # Source-ledger projection (default-OFF
        # MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED). A read-only source tool
        # (FileRead / DocumentRead / Glob / Grep / GitDiff) records inspected
        # sources in its own ``LocalResearchSourceLedger`` and surfaces the
        # public ledger report under ``metadata["sourceProjection"]``. Those
        # records were NEVER projected into the collector's ``_records``, so the
        # pre-final gate's source-evidence ref had no live producer and a real
        # source-read still blocked. This projects each inspected SourceInspection
        # source into an EvidenceRecord using the EXISTING
        # ``SourceLedgerRecord.to_evidence_record()`` -- no new evidence shape.
        # When the flag is OFF this is skipped entirely so ``_records`` is
        # byte-identical to main.
        # When MAGI_SOURCE_CITATION_ENABLED is also ON, sourceProjection ids are
        # remapped to registry-allocated ids so SourceInspection records carry
        # unique src_N instead of the hardcoded src_1.
        source_projection_metadata: Mapping[str, object] = tool_result.metadata
        if citation_records and _source_citation_enabled():
            source_projection_metadata = _remap_source_projection_ids(
                metadata=tool_result.metadata,
                session_id=session_id,
                turn_id=turn_id,
                tool_name=tool_name,
                arguments=arguments or {},
                registries=self._session_source_registries,
            )
        records.extend(
            _projected_source_inspection_records(source_projection_metadata)
        )

        receipt = None
        if not self._has_canonical_general_automation_entry(
            turn_id=turn_id,
            metadata=tool_result.metadata,
        ):
            receipt = _local_receipt_projection(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=tool_result,
                arguments=arguments or {},
                synthesize_execution_receipt=not explicit_records,
            )
        if receipt is not None:
            records.append(receipt)

        if records:
            self._records.setdefault((session_id, turn_id), []).extend(records)
            self._maybe_persist_records(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status=tool_result.status,
                records=records,
            )

        self._maybe_append_evidence_ledger_record(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            status=tool_result.status,
        )
        return tuple(records)

    def record_first_party_activity(
        self,
        *,
        session_id: str,
        turn_id: str,
        activity: object,
    ) -> bool:
        """Append one first-party activity (ToolCall/SkillLoad/SubagentSpawn).

        Flows through the SAME machinery as tool receipts: ``_records`` (turn
        collection), ``_maybe_persist_records`` (default-ON JSONL), and — when
        the lifecycle flag is on — ``_append_turn_record`` (per-turn ledger for
        ``InspectSelfEvidence``). SkillLoad records are deduped per
        ``(session, turn, skillPath, bodyDigest)`` — the dedup key is added AFTER
        the successful append so a projection error cannot permanently poison the
        dedup set. False covers both dedup-skips and failures.
        """
        try:
            from magi_agent.evidence.first_party_activity import (  # noqa: PLC0415
                FirstPartyActivity,
                to_evidence_record,
            )

            if not isinstance(activity, FirstPartyActivity):
                return False
            if not session_id or not turn_id:
                return False
            skill_key: tuple[str, str, str, str] | None = None
            if activity.evidence_type == "SkillLoad":
                skill_key = (
                    session_id,
                    turn_id,
                    str(activity.detail.get("skillPath", "")),
                    str(activity.detail.get("bodyDigest", "")),
                )
                if skill_key in self._first_party_skill_seen:
                    return False
            record = to_evidence_record(activity)
            self._records.setdefault((session_id, turn_id), []).append(record)
            # Dedup key is added AFTER the successful append so that a raising
            # projection (to_evidence_record) does not permanently exclude the
            # skill on subsequent calls.
            if skill_key is not None:
                self._first_party_skill_seen.add(skill_key)
            # Register this (session, turn) as having a first-party record so
            # the prune helper can bound first-party turns without touching
            # non-first-party (tool receipt) records at the same key.
            self._first_party_turns[(session_id, turn_id)] = None
            self._prune_first_party_state(session_id)
            self._maybe_persist_records(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=activity.record_id,
                tool_name=activity.name,
                status=activity.status,
                records=[record],
            )
            from magi_agent.config.env import (  # noqa: PLC0415
                is_evidence_ledger_lifecycle_enabled,
            )

            if is_evidence_ledger_lifecycle_enabled():
                self._append_turn_record(
                    session_id=session_id,
                    turn_id=turn_id,
                    record=record,
                )
            return True
        except Exception:
            return False

    def _prune_first_party_state(self, session_id: str) -> None:
        """Cap per-session first-party turns to the most recent ``_MAX_SESSION_LEDGERS``.

        Only evicts ``custom:FirstParty*``-typed records from ``_records``.
        Non-first-party records (e.g. tool receipts appended by
        ``record_tool_result``) that share the same ``(session, turn)`` key are
        NEVER removed: the first-party prune path must not silently clear tool
        receipts that ``collect_for_turn`` / ``InspectSelfEvidence`` depends on.
        ``_ledgers`` is left entirely untouched here — ``_prune_session_ledgers``
        handles it independently.

        Algorithm:
        1. Collect all ``(session_id, turn_id)`` keys in ``_first_party_turns``
           that belong to ``session_id``, in insertion order.
        2. If the count exceeds ``_MAX_SESSION_LEDGERS``, evict the oldest surplus
           keys one by one:
           a. Filter ``custom:FirstParty*`` records OUT of ``_records[key]``
              (keep non-first-party records).
           b. If the filtered list is now empty, pop the key from ``_records``.
           c. Drop the key from ``_first_party_turns``.
           d. Purge matching entries from ``_first_party_skill_seen``.
        """
        # Gather this session's first-party turns in insertion order.
        session_fp_keys = [key for key in self._first_party_turns if key[0] == session_id]
        surplus = len(session_fp_keys) - _MAX_SESSION_LEDGERS
        if surplus <= 0:
            return
        evicted: set[tuple[str, str]] = set()
        for key in session_fp_keys[:surplus]:
            existing = self._records.get(key)
            if existing is not None:
                # Keep only non-first-party records (tool receipts etc.)
                kept = [
                    r
                    for r in existing
                    if not str(getattr(r, "type", "")).startswith("custom:FirstParty")
                ]
                if kept:
                    self._records[key] = kept
                else:
                    self._records.pop(key, None)
            self._first_party_turns.pop(key, None)
            evicted.add(key)
        if evicted:
            self._first_party_skill_seen = {
                fp_key
                for fp_key in self._first_party_skill_seen
                if (fp_key[0], fp_key[1]) not in evicted
            }

    @staticmethod
    def _maybe_persist_records(
        *,
        session_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        status: str,
        records: list[object],
    ) -> None:
        """Durable JSONL sink — ON by default (``MAGI_EVIDENCE_LEDGER_DIR``).

        The in-memory view keeps only the last ``_MAX_SESSION_LEDGERS`` turns —
        a lean live view, NOT an audit store. A governance-identity runtime
        ships its audit trail on by default: entries append to
        ``<cwd>/.magi/evidence/<session_id>.jsonl``. Set the env to a directory
        to relocate, or to ``off``/``0``/``false``/``none`` to disable.
        Fail-soft: persistence problems never break the tool path.
        """
        from magi_agent.evidence.ledger_store import (  # noqa: PLC0415
            evidence_ledger_path,
            write_evidence_records,
        )

        # Shared resolver with the reader (EvidenceLedgerReader) so writer and
        # reader agree on one location + disable/default-dir/sanitization rules.
        path = evidence_ledger_path(session_id)
        if path is None:
            return
        # Build the per-record flat dicts (same shape as before: toolCallId,
        # toolName, status, record) and delegate byte-writing to the shared
        # writer so the hosted runner can reuse the same sink.
        flat_records = [
            {
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "status": status,
                "record": _public_record_projection(record),
            }
            for record in records
        ]
        write_evidence_records(path.parent, session_id=session_id, turn_id=turn_id, records=flat_records)

    def evidence_ledgers_for_session(
        self,
        session_id: str,
    ) -> tuple[EvidenceLedger, ...]:
        """Return the per-(session, turn) EvidenceLedgers built for a session.

        Used by the CLI tool-context factories to populate
        ``ToolContext.source_ledger`` so ``InspectSelfEvidence`` can project the
        REAL tool calls recorded so far. Returns an empty tuple when the
        lifecycle flag is off (no ledgers are ever built) or no tool result has
        been recorded for the session yet.

        Bounded to the most recent ``_MAX_SESSION_LEDGERS`` turns (older turns
        dropped from the live self-evidence view) to keep the per-dispatch cost
        constant — see ``_MAX_SESSION_LEDGERS``. ``self._ledgers`` is a dict
        keyed by ``(session_id, turn_id)`` whose insertion order is the order
        each turn was first recorded; older per-session entries are pruned so
        process-lifetime retention matches the accessor cap.
        """
        self._prune_session_ledgers(session_id)
        return tuple(
            ledger
            for (stored_session_id, _turn_id), ledger in self._ledgers.items()
            if stored_session_id == session_id
        )

    def record_phase_reached(
        self,
        session_id: str,
        turn_id: str,
        phase_name: str,
    ) -> None:
        """Record that the turn reached ``phase_name`` as ledger evidence.

        Appends a ``custom:PhaseReached`` ``EvidenceRecord`` into the SAME
        per-``(session, turn)`` EvidenceLedger that Stage 1's tool-trace records
        share, so ``InspectSelfEvidence`` can project the REAL phases the agent
        went through this turn. Flag-gated on
        ``MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED`` (default OFF -> no record,
        byte-identical) and fail-open (never breaks a turn). The record carries
        NO ``toolName`` so the shared tool-call normalizer ignores it; the phase
        projection keys off the ``custom:PhaseReached`` type instead.
        """
        # Fail-open: phase synthesis/append must NEVER break a turn. Mirrors the
        # ``_maybe_append_evidence_ledger_record`` convention.
        try:
            from magi_agent.config.env import (  # noqa: PLC0415
                is_evidence_ledger_lifecycle_enabled,
            )

            if not is_evidence_ledger_lifecycle_enabled():
                return
            if not session_id or not turn_id or not phase_name:
                return
            self._append_turn_record(
                session_id=session_id,
                turn_id=turn_id,
                record=_synthesize_phase_reached_record(phase_name=phase_name),
            )
        except Exception:
            return

    def record_verifier_verdict(
        self,
        session_id: str,
        turn_id: str,
        stage: str,
        result: str,
    ) -> None:
        """Record a verifier verdict for the turn as ledger evidence.

        Appends a ``custom:VerifierVerdict`` ``EvidenceRecord`` into the SAME
        per-``(session, turn)`` EvidenceLedger that Stage 1's tool-trace and
        Stage 3's phase records share, so ``InspectSelfEvidence`` can project
        the REAL verifier verdicts the turn produced. Flag-gated on
        ``MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED`` (default OFF -> no record,
        byte-identical) and fail-open (never breaks a turn).

        Deliberately bypasses ``EvidenceLedger.append_verifier_verdict`` (which
        requires ``matched_evidence_refs`` pointing at evidence_records in the
        same per-turn ledger — the verifier bus produces public ref strings, not
        ledger evidence_refs, so that path would skip-on-mismatch and yield
        near-always-empty verdicts). The record carries NO ``toolName`` so the
        shared tool-call normalizer ignores it; the verdict projection keys off
        the ``custom:VerifierVerdict`` type and reads ``fields.stage`` /
        ``fields.result``.
        """
        # Fail-open: verdict synthesis/append must NEVER break a turn. Mirrors
        # the ``record_phase_reached`` convention.
        try:
            from magi_agent.config.env import (  # noqa: PLC0415
                is_evidence_ledger_lifecycle_enabled,
            )

            if not is_evidence_ledger_lifecycle_enabled():
                return
            if not session_id or not turn_id or not stage or not result:
                return
            self._append_turn_record(
                session_id=session_id,
                turn_id=turn_id,
                record=_synthesize_verifier_verdict_record(
                    stage=stage,
                    result=result,
                ),
            )
        except Exception:
            return

    def _maybe_append_evidence_ledger_record(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        status: str,
    ) -> None:
        # Fail-open: ledger synthesis/append must NEVER break a tool call. Any
        # failure (flag read, record validation, immutable append) is swallowed
        # and the per-key ledger is simply left untouched, matching the existing
        # ``except Exception`` convention in this file / tool_runtime.py.
        try:
            from magi_agent.config.env import (  # noqa: PLC0415
                is_evidence_ledger_lifecycle_enabled,
            )

            if not is_evidence_ledger_lifecycle_enabled():
                return
            if not session_id or not turn_id or not tool_name:
                return
            self._append_turn_record(
                session_id=session_id,
                turn_id=turn_id,
                record=_synthesize_tool_trace_record(
                    tool_name=tool_name,
                    status=status,
                ),
            )
        except Exception:
            return

    def _append_turn_record(
        self,
        *,
        session_id: str,
        turn_id: str,
        record: EvidenceRecord,
    ) -> None:
        # Shared lazy-ledger construction: phase markers and tool traces append
        # into ONE single-turn ledger per ``(session, turn)`` so the contiguous
        # append-only sequence + single-turn constraint hold across both kinds.
        # Immutable reassign (``append_evidence_record`` returns a fresh ledger).
        key = (session_id, turn_id)
        ledger = self._ledgers.get(key) or _new_tool_trace_ledger(
            session_id=session_id,
            turn_id=turn_id,
        )
        self._ledgers[key] = ledger.append_evidence_record(record)
        self._prune_session_ledgers(session_id)

    def _prune_session_ledgers(self, session_id: str) -> None:
        session_keys = [key for key in self._ledgers if key[0] == session_id]
        for key in session_keys[:-_MAX_SESSION_LEDGERS]:
            self._ledgers.pop(key, None)

    def append_evidence_record_for_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        record: object,
        producing_rule_id: str = "",
    ) -> None:
        """Append a pre-built evidence record under ``(session_id, turn_id)``.

        Used by declarative producers (e.g. ``DashboardProducerControl``) that
        synthesize their own :class:`EvidenceRecord` rather than going through
        the tool-receipt or first-party-activity paths. The record lands in the
        same ``_records`` corpus that ``collect_for_turn`` (and thus the
        pre-final verifier-bus gate) reads, so it is NOT gated on any lifecycle
        flag — it must always be collectible by the gate.

        This is a runtime-control write path, so the record is re-stamped
        ``origin="producer_control"`` (+ ``producing_rule_id``) here rather than
        trusting the caller: a record lifted from tool metadata never reaches
        this method, so it can never acquire the trusted origin.
        """
        stamped = _as_producer_control(record, producing_rule_id)
        self._records.setdefault((session_id, turn_id), []).append(stamped)

    def record_audit_evidence_for_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        record: object,
        tool_call_id: str | None = None,
    ) -> None:
        """Append a pre-built audit ``EvidenceRecord`` AND durably persist it.

        Mirrors :meth:`append_evidence_record_for_turn` (records land in the
        ``_records`` corpus the pre-final gate reads) but ALSO routes the record
        through the durable JSONL sink (:meth:`_maybe_persist_records`) so an
        ``action="audit"`` customize rule leaves a durable trace — the "trace"
        leg of rule/policy/trace. Single-writer invariant preserved: the durable
        write still goes through ``_maybe_persist_records`` →
        ``ledger_store.write_evidence_records``.

        Fail-soft: a persistence error never breaks the tool path; the live-view
        append always happens so the gate still sees the record.

        Runtime-control write path, so the record is stamped
        ``origin="producer_control"`` with an empty ``producing_rule_id`` (an
        audit record is not a producer binding, so it can never satisfy a
        session gate's producer-id match).
        """
        stamped = _as_producer_control(record)
        self._records.setdefault((session_id, turn_id), []).append(stamped)
        try:
            status = str(getattr(stamped, "status", "ok"))
            self._maybe_persist_records(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id or "customize-audit",
                tool_name=tool_name,
                status=status,
                records=[stamped],
            )
        except Exception:
            return

    def collect_for_turn(self, turn_id: str) -> tuple[object, ...]:
        local = tuple(
            record
            for (_session_id, stored_turn_id), records in self._records.items()
            if stored_turn_id == turn_id
            for record in records
        )
        external = self._general_automation_entries_for_turn(turn_id)
        return (*local, *external)

    def __call__(self, turn_id: str) -> tuple[object, ...]:
        return self.collect_for_turn(turn_id)

    def collect_for_session(self, session_id: str) -> tuple[object, ...]:
        """All ``_records`` for a session, across turns (session-scoped sibling
        of :meth:`collect_for_turn`).

        This is the corpus a session-scoped gate reads (a producer records
        credibility on an earlier turn; the gate on a later turn must see it).
        Reads only ``_records`` (the producer/tool-lift corpus), NOT the
        ``_ledgers`` lifecycle store, matching where producers actually write."""
        return tuple(
            record
            for (stored_session_id, _turn_id), records in self._records.items()
            if stored_session_id == session_id
            for record in records
        )

    def has_unlock_evidence(
        self,
        session_id: str,
        *,
        evidence_type: str,
        producing_rule_id: str,
    ) -> bool:
        """True if this session holds an UNLOCK-eligible evidence record.

        The security join (see the policy-abstraction design): a record may
        unlock a gated tool ONLY when it was written by the runtime producer
        control (``origin == "producer_control"``, never a tool-declared/lifted
        record), was emitted by the SPECIFIC bound producer
        (``producing_rule_id`` match, never a free-text type-name coincidence),
        carries the expected type, and passed (``status == "ok"``). An empty
        ``producing_rule_id`` never matches (audit/unbound records are not unlock
        keys).

        The corpus holds both :class:`EvidenceRecord` objects and plain dicts
        (receipt projections, general-automation entries), so the join is gated
        on ``isinstance(record, EvidenceRecord)`` FIRST: provenance is only ever
        read off a real record whose ``origin``/``producing_rule_id`` were set by
        the runtime write path, never off a duck-typed object that could
        self-declare trusted provenance (write-path-is-authority invariant)."""
        if not producing_rule_id:
            return False
        for record in self.collect_for_session(session_id):
            if (
                isinstance(record, EvidenceRecord)
                and record.origin == "producer_control"
                and record.producing_rule_id == producing_rule_id
                and record.type == evidence_type
                and record.status == "ok"
            ):
                return True
        return False

    def _general_automation_entries_for_turn(self, turn_id: str) -> tuple[object, ...]:
        entries_for_turn = getattr(
            self._general_automation_receipts,
            "entries_for_turn",
            None,
        )
        if not callable(entries_for_turn):
            return ()
        try:
            return tuple(entries_for_turn(turn_id))
        except Exception:
            return ()

    def _has_canonical_general_automation_entry(
        self,
        *,
        turn_id: str,
        metadata: Mapping[str, object],
    ) -> bool:
        if not any(
            key in metadata
            for key in ("generalAutomationReceipt", "generalAutomationReceiptLedgerEntry")
        ):
            return False
        return bool(self._general_automation_entries_for_turn(turn_id))


def _source_ledger_evidence_gate_enabled() -> bool:
    import os  # noqa: PLC0415

    from magi_agent.config.env import (  # noqa: PLC0415
        parse_source_ledger_evidence_gate_enabled,
    )

    return parse_source_ledger_evidence_gate_enabled(os.environ)


def _source_citation_enabled() -> bool:
    import os  # noqa: PLC0415

    from magi_agent.config.env import parse_source_citation_enabled  # noqa: PLC0415

    return parse_source_citation_enabled(os.environ)


def _update_authored_paths(
    authored_paths: set[str],
    tool_result: ToolResult,
    tool_name: str,
    arguments: Mapping[str, object],
) -> None:
    """Add file paths written/edited by the agent to the authored-paths set.

    Called BEFORE citation capture so a FileRead after an edit correctly
    excludes the just-edited file from source registration.
    """
    if tool_result.status != "ok":
        return
    # From codingMutationReceipt in tool metadata
    receipt = tool_result.metadata.get("codingMutationReceipt")
    if isinstance(receipt, Mapping):
        path = (
            receipt.get("filePath")
            or receipt.get("file_path")
            or receipt.get("path")
        )
        if isinstance(path, str) and path:
            authored_paths.add(path)
    # From arguments for write tools
    if tool_name in {"FileWrite", "FileEdit", "PatchApply", "file_write", "file_edit"}:
        path = (
            arguments.get("path")
            or arguments.get("file_path")
            or arguments.get("filepath")
        )
        if isinstance(path, str) and path:
            authored_paths.add(path)


def _citation_capture_records(
    *,
    session_id: str,
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    tool_result: ToolResult,
    arguments: Mapping[str, object],
    registries: dict[str, object],
    authored_paths: frozenset[str],
) -> list[object]:
    """Classify + register sources and return producer_control EvidenceRecords.

    Returns [] when citation is disabled or an error occurs (fail-quiet).
    """
    try:
        if not _source_citation_enabled():
            return []
        if tool_result.status != "ok":
            return []
        from magi_agent.evidence.citation_registry import SessionSourceRegistry  # noqa: PLC0415
        from magi_agent.evidence.citation_capture import (  # noqa: PLC0415
            classify_tool_result_for_citation,
        )

        registry: SessionSourceRegistry = registries.setdefault(  # type: ignore[assignment]
            session_id, SessionSourceRegistry(session_id=session_id)
        )
        specs = classify_tool_result_for_citation(
            tool_name,
            tool_result,
            arguments,
            authored_paths=authored_paths,
        )
        citation_ev_records: list[object] = []
        for spec in specs:
            record = registry.register(
                spec.kind,
                spec.uri,
                turn_id=turn_id,
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                title=spec.title,
                content_hash=spec.content_hash,
                trust_tier=spec.trust_tier,
                snippets=spec.snippets,
                metadata=spec.metadata,
                inspected=spec.inspected,
            )
            if record is None:
                continue
            ev = record.to_evidence_record()
            citation_ev_records.append(
                _as_producer_control(ev, "source_citation.capture")
            )
        return citation_ev_records
    except Exception:
        return []


def _remap_source_projection_ids(
    *,
    metadata: Mapping[str, object],
    session_id: str,
    turn_id: str,
    tool_name: str,
    arguments: Mapping[str, object],
    registries: dict[str, object],
) -> Mapping[str, object]:
    """Remap hardcoded src_1 ids in sourceProjection to registry-allocated ids.

    When MAGI_SOURCE_CITATION_ENABLED is ON and _tool_result_from_outcome has
    synthesized a sourceProjection with hardcoded 'src_1', this replaces the
    id with the registry-allocated id for the same source. Returns the original
    metadata unchanged if there is nothing to remap or on any error.
    """
    try:
        projection = metadata.get("sourceProjection")
        if not isinstance(projection, Mapping):
            return metadata
        sources = projection.get("sources")
        if not isinstance(sources, (list, tuple)):
            return metadata

        registry = registries.get(session_id)
        if registry is None:
            return metadata

        remapped = []
        changed = False
        for source_entry in sources:
            if not isinstance(source_entry, Mapping):
                remapped.append(source_entry)
                continue
            original_id = source_entry.get("sourceId")
            kind = source_entry.get("kind", "file")

            uri = _infer_uri_for_source_projection(kind, tool_name, arguments)
            if uri:
                from magi_agent.evidence.citation_registry import SessionSourceRegistry  # noqa: PLC0415
                if isinstance(registry, SessionSourceRegistry):
                    src_record = registry.lookup(kind, uri)
                    if src_record is not None and src_record.source_id != original_id:
                        remapped.append(
                            {**dict(source_entry), "sourceId": src_record.source_id}
                        )
                        changed = True
                        continue
            remapped.append(source_entry)

        if not changed:
            return metadata

        new_projection = {**dict(projection), "sources": remapped}
        return {**dict(metadata), "sourceProjection": new_projection}
    except Exception:
        return metadata


def _infer_uri_for_source_projection(
    kind: str,
    tool_name: str,
    arguments: Mapping[str, object],
) -> str | None:
    """Infer the URI used by citation_capture for this tool call."""
    if kind in ("file", "external_repo"):
        path = (
            arguments.get("path")
            or arguments.get("file")
            or arguments.get("filepath")
            or arguments.get("file_path")
            or arguments.get("pattern")
            or arguments.get("directory")
        )
        if isinstance(path, str) and path:
            return f"file://{path}" if path.startswith("/") else f"file://{path}"
    return None


def _projected_source_inspection_records(
    metadata: Mapping[str, object],
) -> list[object]:
    """Project a read-only tool's source-ledger report into EvidenceRecords.

    Default-OFF behind ``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED``; returns
    ``[]`` (byte-identical to main) when the flag is off, when the result has no
    ``sourceProjection``, or when nothing was inspected.

    Reuses the EXISTING ``SourceLedgerRecord.to_evidence_record()`` (which
    returns ``EvidenceRecord(type="SourceInspection")``) — it reconstructs a
    ``SourceLedgerRecord`` from each public source report entry and projects it.
    Only ``inspected`` ``SourceInspection`` sources are surfaced. Fail-open: any
    error yields ``[]`` so a malformed projection can never wedge the turn.
    """
    if not _source_ledger_evidence_gate_enabled():
        return []
    projection = metadata.get("sourceProjection")
    if not isinstance(projection, Mapping):
        return []
    sources = projection.get("sources")
    if not isinstance(sources, (list, tuple)):
        return []
    turn_id = projection.get("turnId")
    records: list[object] = []
    try:
        from magi_agent.evidence.source_ledger import (  # noqa: PLC0415
            SourceLedgerRecord,
        )

        for source in sources:
            if not isinstance(source, Mapping):
                continue
            if source.get("evidenceType") != "SourceInspection":
                continue
            if source.get("inspected") is not True:
                continue
            source_id = source.get("sourceId")
            kind = source.get("kind")
            inspected_at = source.get("inspectedAt")
            if not isinstance(source_id, str) or not isinstance(kind, str):
                continue
            payload: dict[str, object] = {
                "sourceId": source_id,
                "turnId": turn_id if isinstance(turn_id, str) and turn_id else "unknown-turn",
                "toolName": _SOURCE_LEDGER_PROJECTION_TOOL_NAME,
                "evidenceType": "SourceInspection",
                "kind": kind,
                # The public report redacts the uri; the projected EvidenceRecord
                # only needs a non-empty placeholder (it is never sent anywhere).
                "uri": "source://redacted",
                "inspectedAt": inspected_at if isinstance(inspected_at, (int, float)) else 1,
                "inspected": True,
            }
            try:
                record = SourceLedgerRecord.model_validate(payload)
            except Exception:
                continue
            records.append(record.to_evidence_record())
    except Exception:
        return []
    return records


_SOURCE_LEDGER_PROJECTION_TOOL_NAME = "SourceInspection"


def _local_receipt_projection(
    *,
    session_id: str,
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    result: ToolResult,
    arguments: Mapping[str, object],
    synthesize_execution_receipt: bool,
) -> dict[str, object] | None:
    metadata: Mapping[str, object] = result.metadata
    if result.coding_mutation_receipt is not None and "codingMutationReceipt" not in metadata:
        metadata = {
            **result.metadata,
            "codingMutationReceipt": result.coding_mutation_receipt,
        }
    receipts = _receipt_projections(metadata)
    # A4 — GA deliverable gate visibility. ``localArtifactReceipt`` (written by
    # the spreadsheet write tool) is intentionally NOT in
    # ``_RECEIPT_METADATA_KEYS``: keeping it out preserves the flag-OFF record
    # shape byte-identical to main. With ``MAGI_GA_DELIVERABLE_GATE_ENABLED``
    # ON it must be visible to the engine's pre-final deliverable check or a
    # successfully delivered artifact would false-block the gate.
    if _ga_deliverable_gate_enabled():
        deliverable_receipt = metadata.get("localArtifactReceipt")
        if deliverable_receipt is not None:
            receipts["localArtifactReceipt"] = _receipt_value(deliverable_receipt)
        deliverable_artifact_refs = _deliverable_artifact_refs(
            tool_name=tool_name,
            result=result,
        )
    else:
        deliverable_artifact_refs = ()
    execution_receipt = receipts.get("toolExecutionReceipt")
    if synthesize_execution_receipt and (
        not isinstance(execution_receipt, Mapping) or not execution_receipt
    ):
        receipts["toolExecutionReceipt"] = _tool_execution_receipt(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result,
            arguments=arguments,
        )
    refs = set(_top_level_metadata_refs(metadata))
    refs.update(_public_refs(result.artifact_refs))
    refs.update(_public_refs(result.file_refs))
    refs.update(_public_refs(result.delivery_receipts))
    refs.update(_public_refs(receipts))
    refs.update(_inferred_refs(tool_name=tool_name, result=result, arguments=arguments))
    if not refs and not receipts:
        return None

    evidence_refs = sorted(ref for ref in refs if ref.startswith("evidence:"))
    validator_refs = sorted(ref for ref in refs if ref.startswith("verifier:"))
    receipt_refs = sorted(ref for ref in refs if ref.startswith(("receipt:sha256:", "sha256:")))
    projection: dict[str, object] = {
        "schemaVersion": "openmagi.localToolEvidenceReceipt.v1",
        "sessionId": session_id,
        "turnId": turn_id,
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "status": result.status,
        "metadataOnly": True,
        "trafficAttached": False,
        "executionAttached": True,
        "productionWriteAllowed": False,
        "evidenceRefs": evidence_refs,
        "validatorRefs": validator_refs,
        "receiptRefs": receipt_refs,
        "receipts": receipts,
    }
    if deliverable_artifact_refs:
        projection["artifactRefs"] = deliverable_artifact_refs
    return projection


def _ga_deliverable_gate_enabled() -> bool:
    import os  # noqa: PLC0415

    from magi_agent.config.env import (  # noqa: PLC0415
        parse_ga_deliverable_gate_enabled,
    )

    return parse_ga_deliverable_gate_enabled(os.environ)


def _receipt_projections(metadata: Mapping[str, object]) -> dict[str, object]:
    receipts: dict[str, object] = {}
    for key in _RECEIPT_METADATA_KEYS:
        value = metadata.get(key)
        if value is None:
            continue
        receipts[key] = _receipt_value(value)
    return receipts


def _deliverable_artifact_refs(
    *,
    tool_name: str,
    result: ToolResult,
) -> tuple[str, ...]:
    refs: list[str] = []
    refs.extend(_safe_artifact_refs(result.artifact_refs))
    if _output_artifact_refs_are_safe_for_tool(tool_name):
        refs.extend(_safe_artifact_refs(_output_artifact_ref_values(result.output)))
    return tuple(dict.fromkeys(refs))


def _output_artifact_refs_are_safe_for_tool(tool_name: str) -> bool:
    normalized = "".join(char for char in tool_name.casefold() if char.isalnum())
    return normalized in _OUTPUT_ARTIFACT_REF_TOOL_NAMES


def _output_artifact_ref_values(output: object) -> tuple[object, ...]:
    if not isinstance(output, Mapping):
        return ()
    values: list[object] = []
    if "artifactRef" in output:
        values.append(output["artifactRef"])
    if "artifactRefs" in output:
        values.append(output["artifactRefs"])
    return tuple(values)


def _safe_artifact_refs(value: object) -> tuple[str, ...]:
    refs: list[str] = []
    _collect_safe_artifact_refs(value, refs, depth=0)
    return tuple(dict.fromkeys(refs))


def _collect_safe_artifact_refs(value: object, refs: list[str], *, depth: int) -> None:
    if depth > 4:
        return
    if isinstance(value, str):
        ref = value.strip()
        if ref.startswith("artifact:") and _SAFE_ARTIFACT_REF_RE.fullmatch(ref):
            refs.append(ref)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for item in value:
            _collect_safe_artifact_refs(item, refs, depth=depth + 1)


def _tool_execution_receipt(
    *,
    tool_call_id: str,
    tool_name: str,
    result: ToolResult,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schemaVersion": "openmagi.localToolExecutionReceipt.v1",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "status": result.status,
        "argumentKeys": sorted(str(key) for key in arguments if _public_receipt_key(str(key))),
    }


def _top_level_metadata_refs(metadata: Mapping[str, object]) -> tuple[str, ...]:
    candidates: list[object] = []
    for key in (
        "evidenceRef",
        "evidenceRefs",
        "validatorRef",
        "validatorRefs",
        "receiptRef",
        "receiptRefs",
    ):
        if key in metadata:
            candidates.append(metadata[key])
    return _public_refs(candidates)


def _receipt_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _receipt_value(nested)
            for key, nested in value.items()
            if _public_receipt_key(str(key))
        }
    if isinstance(value, list | tuple):
        return [_receipt_value(item) for item in value]
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)[:240]


def _public_receipt_key(key: str) -> bool:
    normalized = key.replace("_", "").replace("-", "").lower()
    if normalized.startswith("raw"):
        return False
    if any(term in normalized for term in ("authorization", "cookie", "secret", "token")):
        return False
    if normalized in {"output", "content", "log", "logs", "stdout", "stderr"}:
        return False
    return True


def _public_refs(value: object) -> tuple[str, ...]:
    refs: list[str] = []
    _collect_public_refs(value, refs, depth=0)
    return tuple(dict.fromkeys(refs))


def _collect_public_refs(value: object, refs: list[str], *, depth: int) -> None:
    if depth > 8:
        return
    if isinstance(value, str):
        if value.startswith(_PUBLIC_REF_PREFIXES):
            refs.append(value)
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_public_refs(nested, refs, depth=depth + 1)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for nested in value:
            _collect_public_refs(nested, refs, depth=depth + 1)


def _inferred_refs(
    *,
    tool_name: str,
    result: ToolResult,
    arguments: Mapping[str, object],
) -> tuple[str, ...]:
    if result.status != "ok":
        return ()
    refs: list[str] = []
    if tool_name == "GitDiff":
        refs.append("evidence:git-diff")
    if tool_name in {"Bash", "SafeCommand", "TestRun"} and _is_test_command(
        _command_from(arguments, result)
    ):
        refs.append("evidence:test-run")
        refs.append("verifier:dev-coding:test-evidence")
    return tuple(refs)


def _command_from(arguments: Mapping[str, object], result: ToolResult) -> str | None:
    for key in ("command", "cmd", "shellCommand", "shell_command"):
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    fields = result.metadata.get("fields")
    if isinstance(fields, Mapping):
        value = fields.get("command")
        if isinstance(value, str):
            return value
    return None


def _is_test_command(command: str | None) -> bool:
    if command is None:
        return False
    normalized = " ".join(command.strip().split()).lower()
    return any(
        normalized == prefix or normalized.startswith(f"{prefix} ")
        for prefix in _TEST_COMMAND_PREFIXES
    )


# EvidenceStatus is Literal["ok", "failed", "unknown"]; ToolResult.status is
# Literal["ok", "error", "blocked", "needs_approval"]. Map onto the evidence
# vocabulary so the downstream ``normalize_tool_status`` consumer
# (introspection/mapping.py) projects the right canonical token: "ok"->"ok",
# "error"->"failed"(->"error"), everything else -> "unknown".
_TOOL_STATUS_TO_EVIDENCE_STATUS: Mapping[str, str] = {
    "ok": "ok",
    "error": "failed",
}


def _new_tool_trace_ledger(*, session_id: str, turn_id: str) -> EvidenceLedger:
    return EvidenceLedger.model_validate(
        {
            "ledgerId": f"{session_id}:{turn_id}:evidence",
            "sessionId": session_id,
            "turnId": turn_id,
            "runOn": "main",
            "agentRole": "general",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "tool_host",
            "metadata": {},
        }
    )


def _synthesize_tool_trace_record(*, tool_name: str, status: str) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "custom:ToolTrace",
            "status": _TOOL_STATUS_TO_EVIDENCE_STATUS.get(status, "unknown"),
            "observedAt": time.time(),
            "source": {"kind": "tool_trace", "toolName": tool_name},
        }
    )


def _synthesize_phase_reached_record(*, phase_name: str) -> EvidenceRecord:
    # "phase"/"runtime" are NOT valid EvidenceSourceKind values; reuse the
    # "tool_trace" kind (the only local-origin source kind) but deliberately set
    # NO ``toolName`` so the shared ``tool_call_from_evidence_record`` normalizer
    # skips it. The phase projection discriminates on the ``custom:PhaseReached``
    # type and reads ``fields.phaseName`` / ``fields.reached``.
    return EvidenceRecord.model_validate(
        {
            "type": "custom:PhaseReached",
            "status": "ok",
            "observedAt": time.time(),
            "source": {"kind": "tool_trace"},
            "fields": {"phaseName": phase_name, "reached": True},
        }
    )


def _synthesize_verifier_verdict_record(*, stage: str, result: str) -> EvidenceRecord:
    # Mirror the phase-reached pattern: "verifier"/"runtime" are NOT valid
    # EvidenceSourceKind values; reuse the "tool_trace" kind (the only
    # local-origin source kind) but deliberately set NO ``toolName`` so the
    # shared ``tool_call_from_evidence_record`` normalizer skips it. The verdict
    # projection discriminates on the ``custom:VerifierVerdict`` type and reads
    # ``fields.stage`` / ``fields.result``.
    return EvidenceRecord.model_validate(
        {
            "type": "custom:VerifierVerdict",
            "status": "ok",
            "observedAt": time.time(),
            "source": {"kind": "tool_trace"},
            "fields": {"stage": stage, "result": result},
        }
    )


__all__ = ["LocalToolEvidenceCollector"]
