"""Live evidence-catalog view (PR-F2).

Assembles a per-evidence-type read-only view the Customize dashboard renders
under "What evidence is the runtime actually producing right now?". The view
fuses four already-existing surfaces — no new producers, no writes:

  - :data:`magi_agent.customize.shacl_compiler._BUILTIN_FIELD_HINTS` —
    the registry of fields each built-in evidence type *claims* to support
    (``registeredFields``).
  - :func:`magi_agent.evidence.ledger_store.EvidenceLedgerReader.read` —
    the durable per-session JSONL the CLI collector / hosted gate5b4c3 ALREADY
    write (``fieldsPopulatedRecently`` + ``samplePopulationCount``).
  - :func:`magi_agent.customize.what_menu.what_menu` — the WHAT-menu of refs a
    deterministic_ref custom rule may target (``refsUsing``).
  - :func:`magi_agent.customize.store.load_overrides` —
    ``verification.custom_rules`` (``rulesReferencing``).

The module is read-only and fail-open: every external call is wrapped so a
broken ledger, missing file, or import error degrades to an empty list rather
than raising. The HTTP route in ``transport/customize.py`` is a thin shell over
:func:`build_live_catalog`.

DESIGN NOTES
------------
* The sampling window is fixed at 100 turns. The window is a *recency* slice
  in append order — we collect the rows' distinct ``turnId``s in the order
  they appear in the JSONL and keep the most-recent ``_TURN_WINDOW``. Rows
  outside the window do not contribute to either ``fieldsPopulatedRecently``
  or ``samplePopulationCount``.

* ``fieldsPopulatedRecently`` is restricted to the intersection with the
  ``registeredFields`` hint — surfacing fields the runtime claims to support
  AND actually emitted. An evidence type with an honest-empty hint (e.g.
  ``GitDiff`` until the producer half of the F-series lands) will therefore
  always report ``fieldsPopulatedRecently=[]`` even when the type was
  observed; ``samplePopulationCount`` still reflects the observation count so
  the dashboard can render a "type observed, no fields surfaced" row.

* ``asOf`` is produced via :func:`_as_of_now` so tests can monkeypatch a
  deterministic value. Production calls :func:`time.time` once per request.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "LIVE_CATALOG_SAMPLING_WINDOW",
    "build_live_catalog",
]

# Public, descriptive label echoed back in the response body. Kept as a string
# so the front-end can render it verbatim without computing copy from a number.
_TURN_WINDOW = 100
LIVE_CATALOG_SAMPLING_WINDOW = f"last {_TURN_WINDOW} turns"


def _as_of_now() -> str:
    """Return the current ISO-8601 UTC timestamp (injection seam for tests)."""
    return datetime.fromtimestamp(time.time(), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_reader(base_dir: Path) -> Any:
    """Construct an :class:`EvidenceLedgerReader` (injection seam for tests).

    Tests monkeypatch this with a stub that raises so the fail-open path can
    be exercised without writing a broken JSONL file.
    """
    from magi_agent.evidence.ledger_store import EvidenceLedgerReader  # noqa: PLC0415

    return EvidenceLedgerReader(base_dir)


def _safe_field_hints() -> dict[str, list[str]]:
    """Return a defensive copy of ``_BUILTIN_FIELD_HINTS`` (fail-open)."""
    try:
        from magi_agent.customize.shacl_compiler import (  # noqa: PLC0415
            _BUILTIN_FIELD_HINTS,
        )

        return {k: list(v) for k, v in _BUILTIN_FIELD_HINTS.items()}
    except Exception:  # noqa: BLE001
        logger.debug("live_catalog: hint table import failed", exc_info=True)
        return {}


def _safe_resolve_ledger_dir(env: Mapping[str, str] | None) -> Path | None:
    """Resolve the durable evidence directory or ``None`` (fail-open)."""
    try:
        from magi_agent.evidence.ledger_store import (  # noqa: PLC0415
            resolve_evidence_ledger_dir,
        )

        return resolve_evidence_ledger_dir(env)
    except Exception:  # noqa: BLE001
        logger.debug("live_catalog: ledger-dir resolution failed", exc_info=True)
        return None


def _safe_what_menu(env: Mapping[str, str] | None) -> list[dict[str, Any]]:
    """Return the WHAT-menu, or an empty list on import/runtime error."""
    try:
        from magi_agent.customize.what_menu import what_menu  # noqa: PLC0415

        return what_menu(env=env)
    except Exception:  # noqa: BLE001
        logger.debug("live_catalog: what_menu lookup failed", exc_info=True)
        return []


def _safe_load_overrides() -> dict[str, Any]:
    """Load the persisted customize overrides (fail-open)."""
    try:
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415

        return load_overrides()
    except Exception:  # noqa: BLE001
        logger.debug("live_catalog: load_overrides failed", exc_info=True)
        return {}


def _coerce_record(obj: object) -> dict:
    """Return the inner ledger ``record`` as a dict.

    The writer always emits a dict, but the reader is tolerant of legacy
    string-stored records (mirrors ``run_view._coerce_record``).
    """
    if isinstance(obj, Mapping):
        return dict(obj)
    if isinstance(obj, str):
        text = obj.strip()
        if not text:
            return {}
        try:
            import json  # noqa: PLC0415

            parsed = json.loads(text)
        except Exception:  # noqa: BLE001
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _turn_window(rows: Iterable[Mapping[str, object]], window: int) -> set[object]:
    """Pick the most-recent ``window`` distinct ``turnId`` values in append order.

    The ledger writes one line per record in chronological order, so iterating
    forward and keeping the LAST ``window`` distinct turn ids is the natural
    'last N turns' selection. ``None`` ``turnId``s are skipped.
    """
    seen: list[object] = []
    seen_set: set[object] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        turn = row.get("turnId")
        if turn is None or turn in seen_set:
            continue
        seen.append(turn)
        seen_set.add(turn)
    if window <= 0 or len(seen) <= window:
        return set(seen)
    return set(seen[-window:])


def _refs_for_type(menu: list[dict[str, Any]], evidence_type: str) -> list[str]:
    """All WHAT-menu refs that surface a given evidence type."""
    refs: list[str] = []
    for entry in menu:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("evidenceType") == evidence_type:
            ref = entry.get("ref")
            if isinstance(ref, str):
                refs.append(ref)
    return refs


def _rules_referencing(
    custom_rules: Iterable[Mapping[str, object]],
    refs_for_this_type: set[str],
) -> list[str]:
    """Custom-rule ids whose ``deterministic_ref`` payload targets one of
    ``refs_for_this_type``. Empty when no rule matches.
    """
    out: list[str] = []
    for rule in custom_rules:
        if not isinstance(rule, Mapping):
            continue
        what = rule.get("what")
        if not isinstance(what, Mapping):
            continue
        if what.get("kind") != "deterministic_ref":
            continue
        payload = what.get("payload")
        if not isinstance(payload, Mapping):
            continue
        ref = payload.get("ref")
        if isinstance(ref, str) and ref in refs_for_this_type:
            rule_id = rule.get("id")
            if isinstance(rule_id, str) and rule_id:
                out.append(rule_id)
    return out


def build_live_catalog(
    *,
    session_id: str,
    env: Mapping[str, str] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Assemble the per-evidence-type live catalog view.

    Parameters
    ----------
    session_id
        Required. The ledger is partitioned per session.
    env
        Optional environment mapping. Defaults to :data:`os.environ`. Used to
        resolve ``MAGI_EVIDENCE_LEDGER_DIR`` and feed the WHAT-menu predicate.
    as_of
        Optional ISO timestamp. If absent, :func:`_as_of_now` is consulted.

    Returns
    -------
    dict
        ``{evidenceTypes: [...], samplingWindow: str, asOf: str}``. Only
        evidence types that either (a) appear in ``_BUILTIN_FIELD_HINTS`` OR
        (b) were observed in the ledger window contribute a row.
    """
    if env is None:
        env = os.environ
    if as_of is None:
        as_of = _as_of_now()

    hints = _safe_field_hints()
    menu = _safe_what_menu(env)
    overrides = _safe_load_overrides()
    custom_rules_raw = (
        overrides.get("verification", {}).get("custom_rules")
        if isinstance(overrides, Mapping)
        else None
    )
    custom_rules = custom_rules_raw if isinstance(custom_rules_raw, list) else []

    # Resolve + read ledger rows. Both halves fail-open.
    base_dir = _safe_resolve_ledger_dir(env)
    rows: list[dict] = []
    if base_dir is not None:
        try:
            reader = _make_reader(base_dir)
            read_rows = reader.read(session_id)
            if isinstance(read_rows, list):
                rows = [r for r in read_rows if isinstance(r, Mapping)]
        except Exception:  # noqa: BLE001
            # Honest empty: a broken read becomes "no observations" rather than 5xx.
            logger.debug(
                "live_catalog: ledger read failed for sessionId=%s",
                session_id,
                exc_info=True,
            )
            rows = []

    kept_turns = _turn_window(rows, _TURN_WINDOW)

    # Per-type accumulators: count + set of populated fields.
    populated_fields: dict[str, set[str]] = {}
    sample_count: dict[str, int] = {}

    for row in rows:
        if row.get("turnId") not in kept_turns:
            continue
        record = _coerce_record(row.get("record"))
        rec_type = record.get("type")
        if not isinstance(rec_type, str) or not rec_type:
            continue
        # Skip custom: / schema-versioned non-typed records (run bookend,
        # local-tool receipt, first-party activity) — they are not built-in
        # evidence types and the dashboard would not have a field hint for them.
        if rec_type not in hints:
            continue

        sample_count[rec_type] = sample_count.get(rec_type, 0) + 1

        registered = set(hints.get(rec_type, []))
        if not registered:
            continue
        fields_raw = record.get("fields")
        if not isinstance(fields_raw, Mapping):
            continue
        seen_here = {
            k
            for k, v in fields_raw.items()
            if isinstance(k, str) and k in registered and v is not None
        }
        if seen_here:
            populated_fields.setdefault(rec_type, set()).update(seen_here)

    # Only surface types that were actually observed in the ledger window.
    # The dashboard wants a "what is the runtime ACTUALLY producing" view,
    # not the static catalog of every possible built-in. That keeps the
    # response empty when the sink is disabled / session has no rows / read
    # errored, which is the documented fail-open contract.
    all_types = sorted(sample_count.keys())

    entries: list[dict[str, Any]] = []
    for evidence_type in all_types:
        registered = list(hints.get(evidence_type, []))
        populated = sorted(populated_fields.get(evidence_type, set()))
        refs = _refs_for_type(menu, evidence_type)
        rules = _rules_referencing(custom_rules, set(refs))
        entries.append(
            {
                "type": evidence_type,
                "registeredFields": registered,
                "fieldsPopulatedRecently": populated,
                "samplePopulationCount": sample_count.get(evidence_type, 0),
                "refsUsing": refs,
                "rulesReferencing": rules,
            }
        )

    return {
        "evidenceTypes": entries,
        "samplingWindow": LIVE_CATALOG_SAMPLING_WINDOW,
        "asOf": as_of,
    }
