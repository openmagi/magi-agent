"""Unit tests for the run-bookend record builder.

The builder turns a turn's human-facing bookends (goal, result, model, token
usage, status, cost) into ONE durable evidence-ledger record dict of the shape
``write_evidence_records`` accepts (``{toolName, status, record}``). The payload
is ALLOWLIST fail-closed: only known keys ever appear, and free-text goal/result
are redacted + truncated before they can reach a shared link.
"""
from __future__ import annotations

from magi_agent.evidence.run_bookend import (
    RUN_BOOKEND_SCHEMA_VERSION,
    RUN_BOOKEND_TOOL_NAME,
    build_run_bookend_record,
)


def _record(**overrides: object) -> dict:
    base: dict[str, object] = dict(
        session_id="sess-1",
        turn_id="turn-1",
        goal="Fix the lint errors and open a PR",
        result="Fixed 12 issues, opened PR #1234",
        status="ok",
        model="claude-opus-4-8",
        provider="anthropic",
        input_tokens=1500,
        output_tokens=800,
        cost_usd=0.0421,
    )
    base.update(overrides)
    return build_run_bookend_record(**base)  # type: ignore[arg-type]


def test_wrapper_shape_matches_write_evidence_records_contract() -> None:
    rec = _record()
    # write_evidence_records copies only these keys off the dict.
    assert rec["toolName"] == RUN_BOOKEND_TOOL_NAME
    assert rec["status"] == "ok"
    assert isinstance(rec["record"], dict)


def test_payload_carries_all_bookend_fields() -> None:
    payload = _record()["record"]
    assert payload["schemaVersion"] == RUN_BOOKEND_SCHEMA_VERSION
    assert payload["sessionId"] == "sess-1"
    assert payload["turnId"] == "turn-1"
    assert payload["status"] == "ok"
    assert payload["goal"] == "Fix the lint errors and open a PR"
    assert payload["result"] == "Fixed 12 issues, opened PR #1234"
    assert payload["model"] == {"label": "claude-opus-4-8", "provider": "anthropic"}
    assert payload["usage"] == {"inputTokens": 1500, "outputTokens": 800}
    assert payload["costUsd"] == 0.0421


def test_payload_is_allowlist_fail_closed() -> None:
    """Only the known top-level keys may ever appear in the payload."""
    payload = _record()["record"]
    allowed = {
        "schemaVersion",
        "sessionId",
        "turnId",
        "status",
        "goal",
        "result",
        "model",
        "usage",
        "costUsd",
    }
    assert set(payload).issubset(allowed)
    # model sub-object is likewise closed.
    assert set(payload["model"]).issubset({"label", "provider"})
    assert set(payload["usage"]).issubset({"inputTokens", "outputTokens"})


# Secret-shaped fixtures are assembled from fragments at runtime so no
# contiguous provider-token literal lands in committed source (GitHub push
# protection). The redactor sees the full assembled string and must scrub it.
def test_secret_in_goal_is_redacted() -> None:
    token = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    payload = _record(goal=f"run with token {token}")["record"]
    assert token not in payload["goal"]
    assert "[redacted]" in payload["goal"]


def test_secret_in_result_is_redacted() -> None:
    token = "sk-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    payload = _record(result=f"exported OPENAI_API_KEY={token}")["record"]
    assert token not in payload["result"]
    assert "[redacted]" in payload["result"]


def test_long_goal_is_truncated() -> None:
    payload = _record(goal="x" * 100_000)["record"]
    assert len(payload["goal"]) < 100_000


def test_quoted_secret_near_publish_boundary_is_redacted() -> None:
    """Redact runs BEFORE truncation, so a quoted key="..." secret whose closing
    quote sits past the published cap is still scrubbed (regression for the
    truncate-first leak)."""
    goal = ("x" * 180) + ' password="SUPERSECRETVALUE0123456789"'
    payload = _record(goal=goal)["record"]
    assert "SUPERSECRETVALUE0123456789" not in payload["goal"]


def test_max_turns_status_is_preserved() -> None:
    payload = _record(status="max_turns")["record"]
    assert payload["status"] == "max_turns"


def test_missing_optional_fields_are_omitted_not_null() -> None:
    payload = build_run_bookend_record(
        session_id="s",
        turn_id="t",
        goal="do a thing",
        result=None,
        status="aborted",
        model=None,
        provider=None,
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )["record"]
    # Allowlist fail-closed: absent values are omitted, never emitted as null.
    assert "result" not in payload
    assert "model" not in payload
    assert "usage" not in payload
    assert "costUsd" not in payload
    assert payload["goal"] == "do a thing"
    assert payload["status"] == "aborted"


def test_status_is_coerced_to_known_string() -> None:
    # An unexpected status object must not crash; it becomes a safe string.
    rec = build_run_bookend_record(
        session_id="s",
        turn_id="t",
        goal="g",
        result=None,
        status=object(),  # type: ignore[arg-type]
        model=None,
        provider=None,
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )
    assert isinstance(rec["status"], str)
    assert isinstance(rec["record"]["status"], str)
