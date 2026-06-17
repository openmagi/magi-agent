"""TDD tests for gate5b4c3 evidence production (Task 3).

Three tests covering:
1. Evidence is written to disk when MAGI_SERVE_EVIDENCE_ENABLED=1.
2. No evidence is written when the flag is absent (default-OFF).
3. Observable output (public_events + transcript_records + result) is
   unchanged when the flag is ON — evidence write must not perturb the
   SSE/transcript contract.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from magi_agent.evidence.ledger_store import EvidenceLedgerReader
from tests.support.gate5b4c3_capture import capture_boundary
from tests.support.gate5b4c3_fakes import (
    _FunctionCallThenFinalRunner,
    _FunctionCallOnlyEvent,
    _ManualCalculationTool,
)
from tests.test_gate5b4c3_live_runner_boundary import (
    _enabled_config,
    _selected_full_toolhost_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOLDEN_DIR = Path(__file__).parent / "golden" / "gate5b4c3"

# Shadow session ID that the fake request produces (mirrors the golden file).
# _shadow_session_id() strips "sha256:" and takes the first 24 hex chars from
# sessionKeyDigest; the fake SESSION_DIGEST is "sha256:" + "f" * 64.
_EXPECTED_SESSION_ID = "gate5b4c3-shadow-ffffffffffffffffffffffff"

_LATENCY_SENTINEL = "<normalized>"


def _normalize(snap: dict) -> dict:
    """Replace volatile fields so golden comparison is deterministic."""
    records = []
    for rec in snap.get("transcript_records", []):
        rec = dict(rec)
        if "latency_ms" in rec:
            rec["latency_ms"] = _LATENCY_SENTINEL
        records.append(rec)
    public_events = []
    for evt in snap.get("public_events", []):
        evt = dict(evt)
        if "durationMs" in evt:
            evt["durationMs"] = _LATENCY_SENTINEL
        public_events.append(evt)
    return {**snap, "transcript_records": records, "public_events": public_events}


def _fresh_runner() -> _FunctionCallThenFinalRunner:
    """Return a freshly reset _FunctionCallThenFinalRunner."""
    _FunctionCallThenFinalRunner.calls = []
    _FunctionCallThenFinalRunner.event_factory = _FunctionCallOnlyEvent
    _ManualCalculationTool.calls = []
    return _FunctionCallThenFinalRunner()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_evidence_produced_when_flag_on(tmp_path: Path) -> None:
    """When MAGI_SERVE_EVIDENCE_ENABLED=1 and MAGI_EVIDENCE_LEDGER_DIR=<tmp>,
    the boundary must write at least one evidence row per-turn.  The rows must
    carry toolName/record fields that reflect the tool call."""
    env_patch = {
        "MAGI_SERVE_EVIDENCE_ENABLED": "1",
        "MAGI_EVIDENCE_LEDGER_DIR": str(tmp_path),
    }
    old_env = {k: os.environ.get(k) for k in env_patch}
    try:
        os.environ.update(env_patch)
        asyncio.run(
            capture_boundary(
                _selected_full_toolhost_request(),
                _fresh_runner(),
                config=_enabled_config(),
                adk_tools=(_ManualCalculationTool,),
            )
        )
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    reader = EvidenceLedgerReader(tmp_path)
    rows = reader.read(_EXPECTED_SESSION_ID)
    assert len(rows) >= 1, (
        f"Expected ≥1 evidence row for session {_EXPECTED_SESSION_ID!r}; got {rows!r}"
    )
    # At least one row must carry toolName == "Calculation".
    tool_names = {r.get("toolName") for r in rows}
    assert "Calculation" in tool_names, (
        f"No row with toolName='Calculation'; got toolNames={tool_names!r}, rows={rows!r}"
    )
    # At least one row must carry a non-empty record dict.
    records_with_payload = [r for r in rows if r.get("record")]
    assert records_with_payload, (
        f"No row with a non-empty 'record' payload; rows={rows!r}"
    )
    # All rows must have the correct sessionId.
    for row in rows:
        assert row.get("sessionId") == _EXPECTED_SESSION_ID, (
            f"Unexpected sessionId in row: {row!r}"
        )
    # All rows must have the correct turnId.
    for row in rows:
        assert row.get("turnId") == "turn_opaque_001", (
            f"Unexpected turnId in row: {row!r}"
        )


def test_no_evidence_when_flag_off(tmp_path: Path) -> None:
    """When MAGI_SERVE_EVIDENCE_ENABLED is absent, no JSONL file must be
    written under tmp_path — the flag is default-OFF."""
    env_patch = {"MAGI_EVIDENCE_LEDGER_DIR": str(tmp_path)}
    old_flag = os.environ.pop("MAGI_SERVE_EVIDENCE_ENABLED", None)
    old_ledger = os.environ.get("MAGI_EVIDENCE_LEDGER_DIR")
    try:
        os.environ.update(env_patch)
        asyncio.run(
            capture_boundary(
                _selected_full_toolhost_request(),
                _fresh_runner(),
                config=_enabled_config(),
                adk_tools=(_ManualCalculationTool,),
            )
        )
    finally:
        if old_flag is None:
            os.environ.pop("MAGI_SERVE_EVIDENCE_ENABLED", None)
        else:
            os.environ["MAGI_SERVE_EVIDENCE_ENABLED"] = old_flag
        if old_ledger is None:
            os.environ.pop("MAGI_EVIDENCE_LEDGER_DIR", None)
        else:
            os.environ["MAGI_EVIDENCE_LEDGER_DIR"] = old_ledger

    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert not jsonl_files, (
        f"Expected no evidence files with flag OFF; found: {jsonl_files}"
    )


def test_observable_output_unchanged_with_flag_on(tmp_path: Path) -> None:
    """With MAGI_SERVE_EVIDENCE_ENABLED=1, the captured public_events +
    transcript_records + result must exactly match the committed golden file
    for the tool_then_final scenario.  The evidence disk-write must be
    completely invisible to the observable output."""
    golden_path = _GOLDEN_DIR / "tool_then_final.json"
    assert golden_path.exists(), (
        f"Golden file not found: {golden_path}.  "
        "Run UPDATE_GOLDEN=1 pytest tests/test_gate5b4c3_output_golden.py first."
    )
    stored = json.loads(golden_path.read_text(encoding="utf-8"))

    env_patch = {
        "MAGI_SERVE_EVIDENCE_ENABLED": "1",
        "MAGI_EVIDENCE_LEDGER_DIR": str(tmp_path),
    }
    old_env = {k: os.environ.get(k) for k in env_patch}
    try:
        os.environ.update(env_patch)
        snap = asyncio.run(
            capture_boundary(
                _selected_full_toolhost_request(),
                _fresh_runner(),
                config=_enabled_config(),
                adk_tools=(_ManualCalculationTool,),
            )
        )
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    normalised = _normalize(snap)
    blob = json.dumps(normalised, indent=2, sort_keys=True, default=str) + "\n"
    stored_blob = json.dumps(stored, indent=2, sort_keys=True, default=str) + "\n"
    assert blob == stored_blob, (
        "Observable output changed when MAGI_SERVE_EVIDENCE_ENABLED=1.\n"
        "Evidence write must not perturb public_events / transcript_records / result.\n"
        f"Golden path: {golden_path}"
    )
