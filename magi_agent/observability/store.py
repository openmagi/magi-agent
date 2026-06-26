from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

from magi_agent.observability.models import ActivityEvent

logger = logging.getLogger(__name__)

# Regex used by _payload_has_evidence to detect matched=N in detail strings.
# Matches the literal "matched=<digits>" embedded in evidence verdict detail
# strings from project_evidence_verdict_rule_event, e.g.
# "evidence verdict state=pass: matched=3 missing=0 failures=0 enforcement=hard"
_MATCHED_COUNT_RE = re.compile(r"\bmatched=(\d+)")


def _payload_has_evidence(payload: dict) -> bool:
    """Return True if a ``rule_check`` payload indicates evidence actually fired.

    Two payload shapes exist in the current runtime:

    1. ``project_verifier_result_rule_event`` emits ``evidenceRef`` at the top
       level — a sha256/receipt digest string (non-empty when evidence is present).

    2. ``project_evidence_verdict_rule_event`` encodes the matched count in the
       ``detail`` field.  In the current runtime ``detail`` is a plain string
       (e.g. ``"evidence verdict state=pass: matched=3 missing=0 ..."``) not a
       nested dict.  The task brief describes this as ``detail.matched_evidence``
       (int) > 0; this function handles both forms so it stays correct if emitters
       later switch to a structured dict.

    False-positive guard: a payload whose ``evidenceRef`` value is an empty
    string, or whose ``detail.matched_evidence`` is 0, is NOT considered
    evidence-bearing.  This is the key reason a pure SQL LIKE filter is
    insufficient (LIKE ``'%evidenceRef%'`` would match even empty-value rows).
    """
    # Path 1: top-level evidenceRef string (verifier result rule events).
    evidence_ref = payload.get("evidenceRef")
    if isinstance(evidence_ref, str) and evidence_ref:
        return True
    # Path 2: detail.matched_evidence in dict form (brief-specified; future-proof).
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return int(detail.get("matched_evidence") or 0) > 0
    # Path 3: matched=N embedded in detail string (current evidence verdict format).
    if isinstance(detail, str):
        m = _MATCHED_COUNT_RE.search(detail)
        return bool(m and int(m.group(1)) > 0)
    return False


_DDL = """
CREATE TABLE IF NOT EXISTS activity_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            REAL NOT NULL,
  session_id    TEXT,
  run_id        TEXT,
  parent_run_id TEXT,
  kind          TEXT NOT NULL,
  tool_name     TEXT,
  status        TEXT,
  summary       TEXT,
  payload_json  TEXT,
  elapsed_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ae_ts ON activity_events(ts);
CREATE INDEX IF NOT EXISTS idx_ae_session ON activity_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_ae_kind ON activity_events(kind, ts);
"""


def _label_from_session_id(session_id: str) -> str:
    """Tier-3 fallback: parse a human-readable label from a session_id string.

    Examples:
      "agent:main:app:demo:32"  ->  "demo #32"   (last segment numeric)
      "session:work"            ->  "session:work"
      "s1"                      ->  "s1"
    """
    parts = session_id.split(":")
    if len(parts) >= 2:
        last = parts[-1]
        if last.isdigit():
            return f"{parts[-2]} #{last}"
        return ":".join(parts[-2:])
    return session_id


_MAX_LABEL_SUMMARY_CHARS = 120
_MAX_LABEL_TOOLS = 5


def _derive_session_label(
    session_id: str,
    first_turn_summary: str | None,
    tool_names: list[str],
) -> str:
    """Deterministic 3-tier label derivation — no LLM.

    Tier 1: non-empty stripped summary from the session's first turn_start event.
    Tier 2: distinct tool names joined by ", " (capped at _MAX_LABEL_TOOLS).
    Tier 3: parsed from session_id via _label_from_session_id.
    """
    # Tier 1
    if first_turn_summary is not None:
        stripped = first_turn_summary.strip()
        if stripped:
            return stripped[:_MAX_LABEL_SUMMARY_CHARS]

    # Tier 2
    if tool_names:
        if len(tool_names) <= _MAX_LABEL_TOOLS:
            return ", ".join(tool_names)
        shown = ", ".join(tool_names[:_MAX_LABEL_TOOLS])
        extra = len(tool_names) - _MAX_LABEL_TOOLS
        return f"{shown} +{extra} more"

    # Tier 3
    return _label_from_session_id(session_id)


def _row_to_dict(row: sqlite3.Row) -> dict:
    payload = row["payload_json"]
    return {
        "id": row["id"],
        "ts": row["ts"],
        "session_id": row["session_id"],
        "run_id": row["run_id"],
        "parent_run_id": row["parent_run_id"],
        "kind": row["kind"],
        "tool_name": row["tool_name"],
        "status": row["status"],
        "summary": row["summary"],
        "payload": json.loads(payload) if payload else {},
        "elapsed_ms": row["elapsed_ms"],
    }


class ActivityStore:
    """SQLite-backed append-only store. Thread-safe, fail-open at call sites."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._closed = False
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(_DDL)
            self._conn.commit()

    def record_event(self, event: ActivityEvent) -> int:
        if self._closed:
            return -1
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO activity_events "
                    "(ts, session_id, run_id, parent_run_id, kind, tool_name, status, summary, payload_json, elapsed_ms) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.ts, event.session_id, event.run_id, event.parent_run_id,
                        event.kind, event.tool_name, event.status, event.summary,
                        json.dumps(event.payload, separators=(",", ":"), default=str) if event.payload else None,
                        event.elapsed_ms,
                    ),
                )
                self._conn.commit()
                return int(cur.lastrowid)
        except Exception:
            logger.debug("activity store record_event failed", exc_info=True)
            return -1

    def list_events(
        self,
        *,
        session_id: str | None = None,
        kind: str | None = None,
        exclude_kind: str | None = None,
        status: str | None = None,
        q: str | None = None,
        since_id: int | None = None,
        before_id: int | None = None,
        limit: int = 200,
        has_evidence: bool = False,
    ) -> list[dict]:
        """Return events matching the given filters.

        ``has_evidence=True`` restricts results to ``kind='rule_check'`` rows
        whose payload indicates evidence fired (``evidenceRef`` is present and
        non-empty, OR ``detail.matched_evidence`` > 0).  The filter uses a
        hybrid strategy: SQL pins ``kind='rule_check'`` (exact, no false
        positives), then a Python post-filter on the parsed payload handles the
        structured evidence check — eliminating false positives that a pure
        ``payload_json LIKE '%evidenceRef%'`` clause would produce for payloads
        that merely mention the string without a truthy value.

        All other active filters combine with ``has_evidence`` via AND.
        """
        if self._closed:
            return []
        clauses: list[str] = []
        args: list[object] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            args.append(session_id)
        if kind is not None:
            tokens = [t.strip() for t in kind.split(",") if t.strip()]
            if len(tokens) > 1:
                placeholders = ",".join("?" * len(tokens))
                clauses.append(f"kind IN ({placeholders})")
                args.extend(tokens)
            else:
                clauses.append("kind = ?")
                args.append(tokens[0] if tokens else kind)
        if exclude_kind is not None:
            tokens = [t.strip() for t in exclude_kind.split(",") if t.strip()]
            if tokens:
                placeholders = ",".join("?" * len(tokens))
                clauses.append(f"kind NOT IN ({placeholders})")
                args.extend(tokens)
        if status is not None:
            tokens = [t.strip() for t in status.split(",") if t.strip()]
            if tokens:
                placeholders = ",".join("?" * len(tokens))
                clauses.append(f"status IN ({placeholders})")
                args.extend(tokens)
        if q is not None:
            clauses.append("summary LIKE ?")
            args.append(f"%{q}%")
        if since_id is not None:
            clauses.append("id > ?")
            args.append(since_id)
        if before_id is not None:
            clauses.append("id < ?")
            args.append(before_id)
        if has_evidence:
            # SQL pre-filter: evidence can only appear on rule_check events.
            # AND'd with any caller-supplied kind filter; zero rows result if
            # kinds are incompatible (correct AND semantics, no special-casing).
            clauses.append("kind = ?")
            args.append("rule_check")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 1000))
        sql = f"SELECT * FROM activity_events{where} ORDER BY id ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        result = [_row_to_dict(r) for r in rows]
        if has_evidence:
            result = [r for r in result if _payload_has_evidence(r["payload"])]
        return result

    def count_events(self) -> int:
        if self._closed:
            return 0
        try:
            with self._lock:
                return int(self._conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0])
        except Exception:
            logger.debug("activity store count_events failed", exc_info=True)
            return 0

    def kind_breakdown(self) -> dict[str, int]:
        """Return {kind: count} across all events, grouped by kind.

        Read-only; deterministic; no side effects.  Returns {} when the store
        is closed or when no events have been recorded yet.
        """
        if self._closed:
            return {}
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT kind, COUNT(*) AS cnt FROM activity_events GROUP BY kind"
                ).fetchall()
            return {r["kind"]: r["cnt"] for r in rows}
        except Exception:
            logger.debug("activity store kind_breakdown failed", exc_info=True)
            return {}

    def prune(self, *, max_events: int | None = None, retention_days: int | None = None) -> int:
        if self._closed:
            return 0
        removed = 0
        try:
            with self._lock:
                if retention_days is not None:
                    cutoff = time.time() - retention_days * 86400
                    cur = self._conn.execute("DELETE FROM activity_events WHERE ts < ?", (cutoff,))
                    removed += cur.rowcount or 0
                if max_events is not None:
                    cur = self._conn.execute(
                        "DELETE FROM activity_events WHERE id NOT IN "
                        "(SELECT id FROM activity_events ORDER BY id DESC LIMIT ?)",
                        (max_events,),
                    )
                    removed += cur.rowcount or 0
                self._conn.commit()
        except Exception:
            logger.debug("activity store prune failed", exc_info=True)
        return removed

    def list_sessions(self, *, limit: int = 100) -> list[dict]:
        if self._closed:
            return []
        limit = max(1, min(int(limit), 1000))

        # Query 1: main aggregation (existing fields + error/rule_check counts).
        # error_count: events with kind='error' or kind='aborted' — these are the
        # dedicated lifecycle error kinds emitted by the projector (onError/onAbort
        # hooks), distinct from tool_end events that may carry status='error'.
        sql_main = (
            "SELECT session_id AS id, COUNT(*) AS event_count, "
            "MIN(ts) AS started_at, MAX(ts) AS last_active, "
            "SUM(CASE WHEN kind='tool_start' THEN 1 ELSE 0 END) AS tool_count, "
            "SUM(CASE WHEN kind IN ('error','aborted') THEN 1 ELSE 0 END) AS error_count, "
            "SUM(CASE WHEN kind='rule_check' THEN 1 ELSE 0 END) AS rule_check_count "
            "FROM activity_events WHERE session_id IS NOT NULL "
            "GROUP BY session_id ORDER BY last_active DESC LIMIT ?"
        )

        # Query 2: per-session per-kind counts for kind_breakdown.
        sql_breakdown = (
            "SELECT session_id, kind, COUNT(*) AS cnt "
            "FROM activity_events WHERE session_id IS NOT NULL "
            "GROUP BY session_id, kind"
        )

        # Query 3: summary of the FIRST turn_start event per session (by MIN ts).
        # Uses a derived-table join — no correlated subquery; one pass.
        # ORDER BY ae.id ASC ensures that when two turn_start events share the
        # exact same ts, the row with the lower id (earlier insert) wins — this
        # makes the tie-break fully deterministic.
        sql_first_turn = (
            "SELECT ae.session_id, ae.summary "
            "FROM activity_events ae "
            "JOIN ("
            "  SELECT session_id, MIN(ts) AS min_ts "
            "  FROM activity_events "
            "  WHERE kind='turn_start' AND session_id IS NOT NULL "
            "  GROUP BY session_id"
            ") AS ft ON ae.session_id = ft.session_id AND ae.ts = ft.min_ts "
            "WHERE ae.kind = 'turn_start' "
            "ORDER BY ae.id ASC"
        )

        # Query 4: distinct tool_names per session ordered by first use.
        sql_tools = (
            "SELECT session_id, tool_name "
            "FROM activity_events "
            "WHERE tool_name IS NOT NULL AND session_id IS NOT NULL "
            "GROUP BY session_id, tool_name "
            "ORDER BY session_id, MIN(ts) ASC"
        )

        try:
            with self._lock:
                main_rows = self._conn.execute(sql_main, (limit,)).fetchall()
                breakdown_rows = self._conn.execute(sql_breakdown).fetchall()
                first_turn_rows = self._conn.execute(sql_first_turn).fetchall()
                tool_rows = self._conn.execute(sql_tools).fetchall()
        except Exception:
            logger.debug("activity store list_sessions failed", exc_info=True)
            return []

        # Build lookup structures.
        breakdown: dict[str, dict[str, int]] = {}
        for r in breakdown_rows:
            breakdown.setdefault(r["session_id"], {})[r["kind"]] = r["cnt"]

        # First-write-wins: rows are ordered by ae.id ASC (see sql_first_turn),
        # so the first row seen for each session_id is always the lowest-id
        # (most deterministic) winner when multiple rows share the same MIN ts.
        first_turn_summary: dict[str, str | None] = {}
        for r in first_turn_rows:
            if r["session_id"] not in first_turn_summary:
                first_turn_summary[r["session_id"]] = r["summary"]

        tool_names_by_session: dict[str, list[str]] = {}
        for r in tool_rows:
            tool_names_by_session.setdefault(r["session_id"], []).append(r["tool_name"])

        result: list[dict] = []
        for row in main_rows:
            sid = row["id"]
            session = dict(row)
            session["kind_breakdown"] = breakdown.get(sid, {})
            session["label"] = _derive_session_label(
                sid,
                first_turn_summary.get(sid),
                tool_names_by_session.get(sid, []),
            )
            result.append(session)
        return result

    def latest_event_with_kind_like(self, needle: str) -> dict | None:
        if self._closed:
            return None
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM activity_events WHERE kind LIKE ? ORDER BY id DESC LIMIT 1",
                    (f"%{needle}%",),
                ).fetchone()
            return _row_to_dict(row) if row else None
        except Exception:
            logger.debug("activity store latest_event_with_kind_like failed", exc_info=True)
            return None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()
