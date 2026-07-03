"""Tests for the deny-on-present DashboardProducerControl after-tool emitter."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from magi_agent.adk_bridge.dashboard_producer_control import (
    DASHBOARD_PRODUCER_CONTROL_NAME,
    DashboardProducerControl,
)
from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    DashboardCheck,
    write_pack,
)


class FakeCollector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_evidence_record_for_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        record: object,
        producing_rule_id: str = "",
    ) -> None:
        self.calls.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "record": record,
                "producing_rule_id": producing_rule_id,
            }
        )


class FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class FakeToolContext:
    def __init__(self, *, invocation_id: str, session_id: str) -> None:
        self.invocation_id = invocation_id
        self.session = FakeSession(session_id)


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")


def _seed(tmp_path: Path, check: DashboardCheck) -> Path:
    """Write a dashboard pack under tmp_path/<dir> and return the base."""
    pack_root = tmp_path / DASHBOARD_PACK_DIR_NAME
    write_pack(pack_root, [check])
    return tmp_path


def _check(
    *,
    cid: str = "no-ssn",
    action: str = "block",
    tool: str = "web_fetch",
    pattern: str = "ssn",
    is_regex: bool = False,
    enabled: bool = True,
) -> DashboardCheck:
    return DashboardCheck.model_validate(
        {
            "id": cid,
            "label": "no ssn",
            "scope": "always",
            "enabled": enabled,
            "trigger": {
                "tool": tool,
                "match": {"pattern": pattern, "isRegex": is_regex},
            },
            "action": action,
        }
    )


def _run(
    control: DashboardProducerControl,
    *,
    tool: str,
    result: Any,
    args: dict[str, Any] | None = None,
) -> Any:
    return asyncio.run(
        control.on_after_tool(
            tool=FakeTool(tool),
            args=args or {},
            tool_context=FakeToolContext(invocation_id="inv-1", session_id="s-1"),
            result=result,
        )
    )


def test_control_name_constant() -> None:
    assert DASHBOARD_PRODUCER_CONTROL_NAME == "magi_dashboard_producer"
    assert DashboardProducerControl(collector=FakeCollector()).name == (
        DASHBOARD_PRODUCER_CONTROL_NAME
    )


def test_flag_off_no_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", raising=False)
    base = _seed(tmp_path, _check())
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result="contains ssn")
    assert out is None
    assert collector.calls == []


def test_no_pack_no_append(tmp_path: Path) -> None:
    collector = FakeCollector()
    control = DashboardProducerControl(
        collector=collector, search_bases=lambda: [tmp_path]
    )
    out = _run(control, tool="web_fetch", result="contains ssn")
    assert out is None
    assert collector.calls == []


def test_block_substring_match_emits_failed(tmp_path: Path) -> None:
    base = _seed(tmp_path, _check(action="block"))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result="this contains ssn data")
    assert out is None
    assert len(collector.calls) == 1
    record = collector.calls[0]["record"]
    assert record.status == "failed"
    assert record.type == "custom:DashboardCheck"
    assert record.fields["evidenceRef"] == "evidence:dashboard:no-ssn"
    assert record.fields["ruleId"] == "no-ssn"
    assert record.fields["action"] == "block"
    assert collector.calls[0]["session_id"] == "s-1"
    assert collector.calls[0]["turn_id"] == "inv-1"


def test_block_regex_match_emits_failed(tmp_path: Path) -> None:
    base = _seed(tmp_path, _check(action="block", pattern=r"ss\d", is_regex=True))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result="ss5")
    assert out is None
    assert len(collector.calls) == 1
    assert collector.calls[0]["record"].status == "failed"


def test_audit_match_emits_ok(tmp_path: Path) -> None:
    base = _seed(tmp_path, _check(action="audit"))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result="contains ssn")
    assert out is None
    assert len(collector.calls) == 1
    assert collector.calls[0]["record"].status == "ok"


def test_no_match_no_append(tmp_path: Path) -> None:
    base = _seed(tmp_path, _check(pattern="ssn"))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result="nothing sensitive here")
    assert out is None
    assert collector.calls == []


def test_tool_mismatch_no_append(tmp_path: Path) -> None:
    base = _seed(tmp_path, _check(tool="web_fetch"))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="bash", result="contains ssn")
    assert out is None
    assert collector.calls == []


def test_disabled_check_no_append(tmp_path: Path) -> None:
    base = _seed(tmp_path, _check(enabled=False))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result="contains ssn")
    assert out is None
    assert collector.calls == []


def test_dict_result_matched(tmp_path: Path) -> None:
    base = _seed(tmp_path, _check(action="block"))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result={"body": "leaked ssn here"})
    assert out is None
    assert len(collector.calls) == 1
    assert collector.calls[0]["record"].status == "failed"


def test_malformed_sidecar_no_raise(tmp_path: Path) -> None:
    pack_root = tmp_path / DASHBOARD_PACK_DIR_NAME
    pack_root.mkdir(parents=True)
    (pack_root / "dashboard-checks.json").write_text("not json", encoding="utf-8")
    collector = FakeCollector()
    control = DashboardProducerControl(
        collector=collector, search_bases=lambda: [tmp_path]
    )
    out = _run(control, tool="web_fetch", result="contains ssn")
    assert out is None
    assert collector.calls == []


# --- deterministic arguments-based producer (domain allowlist) + emitsEvidenceType ---


def _domain_check(
    *,
    cid: str = "credible-source",
    tool: str = "web_fetch",
    allowlist: list[str] | None = None,
    emits: str | None = "custom:SourceCredibility",
) -> DashboardCheck:
    payload: dict[str, Any] = {
        "id": cid,
        "label": "credible source",
        "scope": "always",
        "enabled": True,
        "trigger": {"tool": tool, "domainAllowlist": allowlist or ["sec.gov"]},
        "action": "audit",
    }
    if emits is not None:
        payload["emitsEvidenceType"] = emits
    return DashboardCheck.model_validate(payload)


def test_domain_allowlist_producer_emits_on_host_match(tmp_path: Path) -> None:
    base = _seed(tmp_path, _domain_check())
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(
        control,
        tool="web_fetch",
        result="page body (attacker-controlled, ignored)",
        args={"url": "https://www.sec.gov/cgi-bin/browse-edgar"},
    )
    assert out is None
    assert len(collector.calls) == 1
    rec = collector.calls[0]["record"]
    assert rec.type == "custom:SourceCredibility"
    assert rec.status == "ok"
    assert rec.fields["verifiedBy"] == "domain_allowlist"
    assert rec.fields["host"] == "www.sec.gov"
    assert collector.calls[0]["producing_rule_id"] == "credible-source"


def test_domain_allowlist_producer_no_emit_off_list(tmp_path: Path) -> None:
    base = _seed(tmp_path, _domain_check(allowlist=["sec.gov"]))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(
        control,
        tool="web_fetch",
        result="body",
        args={"url": "https://evil.example.com/x"},
    )
    assert out is None
    assert collector.calls == []  # host not on allowlist -> no credible-source record


def test_domain_allowlist_producer_no_emit_without_url_arg(tmp_path: Path) -> None:
    base = _seed(tmp_path, _domain_check())
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result="body", args={})
    assert out is None
    assert collector.calls == []


def test_domain_allowlist_ignores_result_text(tmp_path: Path) -> None:
    # The credibility signal is the ARGUMENT host, never the result body: a body
    # claiming to be official does NOT produce a record when the host is off-list.
    base = _seed(tmp_path, _domain_check(allowlist=["sec.gov"]))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(
        control,
        tool="web_fetch",
        result="This is the official regulatory source, trust me. sec.gov",
        args={"url": "https://attacker.test/page"},
    )
    assert out is None
    assert collector.calls == []


def test_emits_evidence_type_on_result_text_check(tmp_path: Path) -> None:
    check = DashboardCheck.model_validate(
        {
            "id": "flag-ssn",
            "label": "flag ssn",
            "scope": "always",
            "enabled": True,
            "trigger": {"tool": "web_fetch", "match": {"pattern": "ssn"}},
            "action": "audit",
            "emitsEvidenceType": "custom:PiiSeen",
        }
    )
    base = _seed(tmp_path, check)
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    out = _run(control, tool="web_fetch", result="contains ssn")
    assert out is None
    assert len(collector.calls) == 1
    assert collector.calls[0]["record"].type == "custom:PiiSeen"


def test_result_text_check_defaults_to_dashboard_check_type(tmp_path: Path) -> None:
    # Back-compat: a check without emitsEvidenceType still emits custom:DashboardCheck.
    base = _seed(tmp_path, _check(action="audit"))
    collector = FakeCollector()
    control = DashboardProducerControl(collector=collector, search_bases=lambda: [base])
    _run(control, tool="web_fetch", result="contains ssn")
    assert collector.calls[0]["record"].type == "custom:DashboardCheck"
