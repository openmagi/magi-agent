"""Tests for GET /v1/app/customize/evidence/live-catalog (F2).

This endpoint returns per-evidence-type stats so the dashboard can show
the user, for each built-in evidence type:

  - which fields the runtime *claims* to support (registeredFields from the
    ``_BUILTIN_FIELD_HINTS`` table in ``shacl_compiler``),
  - which of those fields have actually appeared in the durable evidence
    ledger over the last N turns (``fieldsPopulatedRecently``),
  - the sample count those populated-field observations cover,
  - which WHAT-menu refs surface this evidence type (``refsUsing``),
  - which user-authored ``custom_rules`` (deterministic_ref) reference one
    of those refs (``rulesReferencing``).

The endpoint is read-only and fail-open: a missing/disabled ledger directory
or any read error returns a valid empty response (never 5xx).

Tests follow the existing ``test_customize_routes`` shape: a real
``OpenMagiRuntime`` + ``TestClient(create_app(runtime))`` so the route is
exercised exactly as it will be in production.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.evidence.ledger_store import write_evidence_records
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"
_FIXED_AS_OF = "2026-06-23T00:00:00Z"


def _runtime(*, gateway_token: str = _TOKEN) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=gateway_token,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(*, with_auth: bool = True) -> TestClient:
    client = TestClient(create_app(_runtime()))
    if with_auth:
        client.headers.update({"x-gateway-token": _TOKEN})
    return client


def _write_record(
    base_dir: Path,
    *,
    session_id: str,
    turn_id: str,
    rec_type: str,
    fields: dict,
    status: str = "ok",
    tool_name: str = "Bash",
) -> None:
    """Append one record into the durable ledger under ``base_dir``."""
    write_evidence_records(
        base_dir,
        session_id=session_id,
        turn_id=turn_id,
        records=[
            {
                "toolCallId": f"call-{turn_id}-{rec_type}",
                "toolName": tool_name,
                "status": status,
                "record": {
                    "type": rec_type,
                    "status": status,
                    "observedAt": int(time.time() * 1000),
                    "fields": fields,
                },
            }
        ],
    )


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def test_live_catalog_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / "evidence"))
    client = _client(with_auth=False)  # no x-gateway-token header
    resp = client.get(
        "/v1/app/customize/evidence/live-catalog",
        params={"sessionId": "sess-1"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# fail-open: ledger disabled or unreadable returns a valid empty body
# ---------------------------------------------------------------------------


def test_live_catalog_returns_empty_when_sink_disabled(tmp_path, monkeypatch):
    """``MAGI_EVIDENCE_LEDGER_DIR=off`` disables the writer/reader entirely.

    The endpoint must respond 200 with an empty (but well-formed) body —
    fail-open, no 5xx.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", "off")
    client = _client()
    resp = client.get(
        "/v1/app/customize/evidence/live-catalog",
        params={"sessionId": "sess-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidenceTypes"] == []
    assert body["samplingWindow"] == "last 100 turns"
    assert "asOf" in body


def test_live_catalog_returns_empty_when_session_missing(tmp_path, monkeypatch):
    """A sessionId with no persisted rows still returns a valid empty body."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / "evidence"))
    client = _client()
    resp = client.get(
        "/v1/app/customize/evidence/live-catalog",
        params={"sessionId": "never-seen"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidenceTypes"] == []


def test_live_catalog_requires_sessionId(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / "evidence"))
    client = _client()
    resp = client.get("/v1/app/customize/evidence/live-catalog")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# happy path: per-type stats reflect the seeded ledger
# ---------------------------------------------------------------------------


def test_live_catalog_aggregates_per_type_stats(tmp_path, monkeypatch):
    """Seed the ledger with TestRun + GitDiff rows for one session.

    The response must:
      - list one entry per evidence type that was observed,
      - include ``registeredFields`` from the field-hint table,
      - report which of those fields were actually populated
        (``fieldsPopulatedRecently``),
      - count the number of records that contributed to that population
        (``samplePopulationCount``),
      - cross-reference the WHAT-menu (``refsUsing``).
    """
    evidence_dir = tmp_path / "evidence"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(evidence_dir))

    # 2 TestRun rows — one populates {command,exitCode}, one only {command}.
    _write_record(
        evidence_dir,
        session_id="sess-1",
        turn_id="turn-1",
        rec_type="TestRun",
        fields={"command": "pytest", "exitCode": 0},
    )
    _write_record(
        evidence_dir,
        session_id="sess-1",
        turn_id="turn-2",
        rec_type="TestRun",
        fields={"command": "pytest -q"},
    )
    # 1 GitDiff row — empty hints (honest empty), but type is observed.
    _write_record(
        evidence_dir,
        session_id="sess-1",
        turn_id="turn-3",
        rec_type="GitDiff",
        fields={"changedFiles": ["a.py", "b.py"]},
    )

    client = _client()
    resp = client.get(
        "/v1/app/customize/evidence/live-catalog",
        params={"sessionId": "sess-1"},
    )
    assert resp.status_code == 200
    body = resp.json()

    by_type = {e["type"]: e for e in body["evidenceTypes"]}
    assert "TestRun" in by_type
    assert "GitDiff" in by_type

    test_run = by_type["TestRun"]
    # Built-in registered fields for TestRun are {command, exitCode}.
    assert set(test_run["registeredFields"]) == {"command", "exitCode"}
    # Both registered fields appeared (one in row 1, both in row 1 too).
    assert set(test_run["fieldsPopulatedRecently"]) == {"command", "exitCode"}
    # Two TestRun rows contributed.
    assert test_run["samplePopulationCount"] == 2
    # The WHAT-menu surfaces TestRun via 'verifier:dev-coding:test-evidence'
    # and 'evidence:test-run' (both _BASE_MENU entries).
    assert "evidence:test-run" in test_run["refsUsing"]

    git_diff = by_type["GitDiff"]
    # GitDiff is now a live producer (changedFiles/fileCount/digest).
    assert set(git_diff["registeredFields"]) == {"changedFiles", "fileCount", "digest"}
    assert git_diff["samplePopulationCount"] == 1
    # Only ``changedFiles`` was populated in the sample row, so it is the sole
    # recently-populated registered field.
    assert git_diff["fieldsPopulatedRecently"] == ["changedFiles"]
    assert "evidence:git-diff" in git_diff["refsUsing"]


def test_live_catalog_rulesReferencing_lists_custom_rule_ids(tmp_path, monkeypatch):
    """If a custom_rule references ``evidence:test-run`` then the TestRun
    entry's ``rulesReferencing`` should include that rule id."""
    cfile = tmp_path / "customize.json"
    cfile.write_text(
        json.dumps(
            {
                "verification": {
                    "custom_rules": [
                        {
                            "id": "cr_tests_pass",
                            "scope": "coding",
                            "enabled": True,
                            "what": {
                                "kind": "deterministic_ref",
                                "payload": {"ref": "evidence:test-run"},
                            },
                            "firesAt": "pre_final",
                            "action": "block",
                        }
                    ]
                }
            }
        )
    )
    evidence_dir = tmp_path / "evidence"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(evidence_dir))

    _write_record(
        evidence_dir,
        session_id="sess-2",
        turn_id="turn-1",
        rec_type="TestRun",
        fields={"command": "pytest"},
    )

    client = _client()
    resp = client.get(
        "/v1/app/customize/evidence/live-catalog",
        params={"sessionId": "sess-2"},
    )
    assert resp.status_code == 200
    by_type = {e["type"]: e for e in resp.json()["evidenceTypes"]}
    assert "cr_tests_pass" in by_type["TestRun"]["rulesReferencing"]


def test_live_catalog_asOf_can_be_injected(tmp_path, monkeypatch):
    """The module-level ``_as_of_now`` indirection is the injection seam so
    tests are deterministic (no wall-clock-derived ``asOf``)."""
    from magi_agent.customize import live_catalog as live_catalog_mod

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / "evidence"))
    monkeypatch.setattr(live_catalog_mod, "_as_of_now", lambda: _FIXED_AS_OF)

    client = _client()
    resp = client.get(
        "/v1/app/customize/evidence/live-catalog",
        params={"sessionId": "sess-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["asOf"] == _FIXED_AS_OF


def test_live_catalog_fails_open_on_read_error(tmp_path, monkeypatch):
    """If the underlying reader raises unexpectedly, the route still returns
    200 with an empty list rather than 5xx."""
    from magi_agent.customize import live_catalog as live_catalog_mod

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / "evidence"))

    class _Boom:
        def read(self, _session_id):  # noqa: D401, ANN001
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(
        live_catalog_mod, "_make_reader", lambda _base_dir: _Boom()
    )

    client = _client()
    resp = client.get(
        "/v1/app/customize/evidence/live-catalog",
        params={"sessionId": "sess-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["evidenceTypes"] == []


def test_live_catalog_turn_window_caps_input(tmp_path, monkeypatch):
    """The endpoint reports a fixed sampling window ('last 100 turns');
    older turns beyond that window are not counted into the per-type
    ``samplePopulationCount``.
    """
    evidence_dir = tmp_path / "evidence"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(evidence_dir))

    # Write 105 TestRun rows across 105 distinct turns. With a 100-turn
    # window only the most-recent 100 should contribute.
    for i in range(105):
        _write_record(
            evidence_dir,
            session_id="sess-window",
            turn_id=f"turn-{i:04d}",
            rec_type="TestRun",
            fields={"command": f"pytest-{i}", "exitCode": 0},
        )

    client = _client()
    resp = client.get(
        "/v1/app/customize/evidence/live-catalog",
        params={"sessionId": "sess-window"},
    )
    assert resp.status_code == 200
    by_type = {e["type"]: e for e in resp.json()["evidenceTypes"]}
    # 100, not 105 — older rows fell outside the window.
    assert by_type["TestRun"]["samplePopulationCount"] == 100
