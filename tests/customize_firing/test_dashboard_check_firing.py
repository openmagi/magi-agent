"""F1 firing test: prove a dashboard_check (after-tool regex) fires.

End-to-end-ish slice that goes from the on-disk sidecar persistence path used
by the dashboard authoring flow (``packs/dashboard_authored.write_pack`` ->
``dashboard-checks.json``) through the after-tool producer
(``DashboardProducerControl.on_after_tool``) and asserts that an audit
``EvidenceRecord`` is appended to the collector when the tool output matches
the user-authored regex.

Positive case mirrors the canonical "leaked AWS key" detector
(``AKIA[0-9A-Z]{16}`` against tool output containing ``AKIAIOSFODNN7EXAMPLE``)
with ``action="audit"`` and ``scope="coding"``; negative case asserts a clean
output produces no emission.

Note on the ``tool: '*'`` shape from the F1 spec: the current producer matches
``check.trigger.tool`` against ``getattr(tool, 'name', '')`` with a strict
equality check (see ``DashboardProducerControl.on_after_tool``) so there is no
wildcard support today. To keep the firing test honest about what actually
fires in production, the trigger pins an explicit tool name (``bash``) that
the simulated after-tool dispatch also uses; the regex itself is the
discriminator the test ultimately proves.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from magi_agent.adk_bridge.dashboard_producer_control import (
    DashboardProducerControl,
)
from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    DashboardCheck,
    write_pack,
)

AWS_KEY_REGEX = r"AKIA[0-9A-Z]{16}"
LEAKED_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
TOOL_NAME = "bash"


class _CollectingCollector:
    """Minimal stand-in for ``LocalToolEvidenceCollector``.

    Only ``append_evidence_record_for_turn`` is used by the producer; we record
    every call so the assertions can inspect the appended record.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_evidence_record_for_turn(
        self, *, session_id: str, turn_id: str, record: object
    ) -> None:
        self.calls.append(
            {"session_id": session_id, "turn_id": turn_id, "record": record}
        )


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class _FakeToolContext:
    def __init__(self, *, invocation_id: str, session_id: str) -> None:
        self.invocation_id = invocation_id
        self.session = _FakeSession(session_id)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authoring flag must be ON for the producer to read the sidecar."""
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")


def _seed_aws_key_check(tmp_path: Path) -> Path:
    """Persist the F1 dashboard_check via the production write_pack path."""
    pack_root = tmp_path / DASHBOARD_PACK_DIR_NAME
    check = DashboardCheck.model_validate(
        {
            "id": "test-aws-key",
            "label": "no leaked aws keys",
            "scope": "coding",
            "enabled": True,
            "trigger": {
                "tool": TOOL_NAME,
                "match": {"pattern": AWS_KEY_REGEX, "isRegex": True},
            },
            "action": "audit",
        }
    )
    write_pack(pack_root, [check])
    # Sanity: the sidecar the producer reads is on disk.
    assert (pack_root / "dashboard-checks.json").is_file()
    return tmp_path


def _drive_after_tool(
    control: DashboardProducerControl, *, tool: str, result: Any
) -> Any:
    return asyncio.run(
        control.on_after_tool(
            tool=_FakeTool(tool),
            args={},
            tool_context=_FakeToolContext(
                invocation_id="turn-f1", session_id="session-f1"
            ),
            result=result,
        )
    )


def test_dashboard_check_fires_on_aws_key_match(tmp_path: Path) -> None:
    """Positive: regex match against tool output emits an audit record."""
    base = _seed_aws_key_check(tmp_path)
    collector = _CollectingCollector()
    control = DashboardProducerControl(
        collector=collector, search_bases=lambda: [base]
    )

    leaked_output = (
        f"export AWS_ACCESS_KEY_ID={LEAKED_AWS_KEY}\n"
        "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
    )
    out = _drive_after_tool(control, tool=TOOL_NAME, result=leaked_output)

    # Producer is emit-only: ALWAYS returns None even on a match.
    assert out is None
    assert len(collector.calls) == 1, (
        "exactly one audit evidence record expected for a single match"
    )

    call = collector.calls[0]
    assert call["session_id"] == "session-f1"
    assert call["turn_id"] == "turn-f1"

    record = call["record"]
    # ``action="audit"`` → top-level ``status="ok"`` (observability, never blocks)
    assert record.type == "custom:DashboardCheck"
    assert record.status == "ok"
    assert record.source.tool_name == TOOL_NAME
    assert record.fields["ruleId"] == "test-aws-key"
    assert record.fields["action"] == "audit"
    assert record.fields["evidenceRef"] == "evidence:dashboard:test-aws-key"


def test_dashboard_check_does_not_fire_on_clean_output(tmp_path: Path) -> None:
    """Negative: clean tool output produces no emission and no return."""
    base = _seed_aws_key_check(tmp_path)
    collector = _CollectingCollector()
    control = DashboardProducerControl(
        collector=collector, search_bases=lambda: [base]
    )

    clean_output = "total 0\ndrwxr-xr-x  2 user  staff  64 Jan  1 00:00 .\n"
    out = _drive_after_tool(control, tool=TOOL_NAME, result=clean_output)

    assert out is None
    assert collector.calls == [], "no record should be appended for clean output"
