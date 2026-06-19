from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerAttachmentFlags,
    SourceLedgerRecord,
    SourceLedgerScope,
    evidence_record_from_source_inspected_event,
    public_source_ledger_report,
    source_ledger_record_from_source_inspected_event,
)
from magi_agent.evidence.reports import public_evidence_record_report


def _fixture_token() -> str:
    return "sk" + "-redaction-fixture"


def _fixture_bearer_value() -> str:
    return "Bear" + "er redaction-fixture"


def _fixture_auth_header() -> str:
    return "Auth" + "orization: " + _fixture_bearer_value()


def _fixture_api_key_field() -> str:
    return "api" + "Key"


def _fixture_secret_uri(query_key: str = "token") -> str:
    return f"https://docs.example.test/private?{query_key}={_fixture_token()}"


def _fixture_source_uri() -> str:
    return f"https://docs.example.test/source?token={_fixture_token()}"


def _fixture_secret_snippet(prefix: str = "private raw page contains") -> str:
    return f"{prefix} {_fixture_auth_header()}"


def _source_inspected_event(**source_overrides: object) -> dict[str, object]:
    source: dict[str, object] = {
        "sourceId": "src_7",
        "turnId": "turn-1",
        "toolName": "WebFetch",
        "toolUseId": "toolu_source_1",
        "kind": "web_fetch",
        "url": _fixture_secret_uri(),
        "title": "Example Docs",
        "contentHash": "sha256:abc",
        "contentType": "text/html",
        "trustTier": "official",
        "snippet": _fixture_secret_snippet(),
        "inspectedAt": 123,
        "metadata": {
            "safeLabel": "docs",
            "rawUri": _fixture_secret_uri(),
            "snippet": _fixture_secret_snippet(),
            _fixture_api_key_field(): _fixture_token(),
        },
        "evidenceId": "ev_source_1",
    }
    source.update(source_overrides)
    return {"type": "source_inspected", "source": source}


def test_source_inspected_event_normalizer_preserves_source_id_and_projects_evidence() -> None:
    record = source_ledger_record_from_source_inspected_event(_source_inspected_event())
    evidence = record.to_evidence_record()
    direct_evidence = evidence_record_from_source_inspected_event(_source_inspected_event())

    assert record.source_id == "src_7"
    assert record.turn_id == "turn-1"
    assert record.tool_name == "WebFetch"
    assert record.tool_use_id == "toolu_source_1"
    assert record.evidence_type == "SourceInspection"
    assert record.kind == "web_fetch"
    assert record.uri == _fixture_secret_uri()
    assert record.snippets == (_fixture_secret_snippet(),)
    assert record.inspected is True
    assert record.metadata["evidenceId"] == "ev_source_1"
    assert record.metadata["safeLabel"] == "docs"

    assert evidence.type == "SourceInspection"
    assert evidence.status == "ok"
    assert evidence.observed_at == 123
    assert evidence.source.tool_name == "WebFetch"
    assert evidence.source.tool_call_id == "toolu_source_1"
    assert evidence.source.metadata["sourceId"] == "src_7"
    assert evidence.source.metadata["sourceKind"] == "web_fetch"
    assert evidence.source.metadata["recordedLocalOnly"] is True
    assert evidence.source.metadata["evidenceId"] == "ev_source_1"
    assert evidence.fields["sourceId"] == "src_7"
    assert evidence.fields["sourceIds"] == ("src_7",)
    assert evidence.fields["sourceKind"] == "web_fetch"
    assert evidence.fields["inspected"] is True
    assert direct_evidence.model_dump(by_alias=True) == evidence.model_dump(by_alias=True)


def test_source_inspected_child_event_overrides_contradictory_main_scope() -> None:
    event = _source_inspected_event(
        turnId="turn-1::spawn::child-a",
        scope={
            "runOn": "main",
            "agentRole": "research",
            "spawnDepth": 0,
            "executionBoundary": "main",
        },
    )
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    normalized = source_ledger_record_from_source_inspected_event(event)
    imported = ledger.record_source_inspected_event(event)

    for record in (normalized, imported):
        assert record.scope.run_on == "child"
        assert record.scope.spawn_depth == 1
        assert record.scope.parent_turn_id == "turn-1"
        assert record.scope.child_turn_id == "child-a"
        assert record.scope.execution_boundary == "child"
        assert record.scope.child_execution_attached is False


def test_source_ledger_scope_defaults_to_generic_when_role_is_not_supplied() -> None:
    scope = SourceLedgerScope()
    ledger = LocalResearchSourceLedger(
        ledgerId="generic-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
    )

    record = ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "FileRead",
            "evidenceType": "SourceInspection",
            "kind": "file",
            "uri": "file://safe-report",
            "title": "Safe report",
            "inspected": True,
        }
    )
    imported = source_ledger_record_from_source_inspected_event(_source_inspected_event())

    assert scope.agent_role == "general"
    assert ledger.agent_role == "general"
    assert record.scope.agent_role == "general"
    assert imported.scope.agent_role == "general"


def test_research_callers_can_still_select_research_scope_explicitly() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-explicit",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    record = ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebSearch",
            "evidenceType": "WebSearch",
            "kind": "web_search",
            "uri": "search:docs",
            "title": "Docs",
            "inspected": False,
        }
    )

    assert record.scope.agent_role == "research"


def test_record_source_inspected_event_public_report_redacts_raw_source_details() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    imported = ledger.record_source_inspected_event(_source_inspected_event())
    report = public_source_ledger_report(ledger)
    dumped = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert imported.source_id == "src_7"
    assert report.sources[0].source_id == "src_7"
    assert report.sources[0].uri == "[redacted]"
    assert report.sources[0].snippets == ("[redacted]",)
    assert report.sources[0].metadata["safeLabel"] == "docs"
    assert report.sources[0].metadata["rawUri"] == "[redacted]"
    assert report.sources[0].metadata["snippet"] == "[redacted]"
    assert report.sources[0].metadata[_fixture_api_key_field()] == "[redacted]"
    assert "docs.example.test/private" not in dumped
    assert _fixture_token() not in dumped
    assert _fixture_bearer_value() not in dumped
    assert "private raw page" not in dumped


@pytest.mark.parametrize(
    "event",
    (
        pytest.param({"type": "tool_end", "source": {}}, id="wrong-event-type"),
        pytest.param({"type": "source_inspected"}, id="missing-source"),
        pytest.param(
            _source_inspected_event(sourceId="src_public_1"),
            id="public-source-id",
        ),
        pytest.param(_source_inspected_event(sourceId="source-1"), id="malformed-source-id"),
        pytest.param(
            _source_inspected_event(attachmentFlags={"sourceFetched": True}),
            id="attachment-flag",
        ),
        pytest.param(
            _source_inspected_event(metadata={"safeLabel": "docs", "webSearchExecuted": True}),
            id="nested-live-flag",
        ),
        pytest.param(
            _source_inspected_event(liveToolDispatched=True),
            id="top-level-live-flag",
        ),
    ),
)
def test_source_inspected_event_normalizer_rejects_unsafe_or_non_authoritative_inputs(
    event: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        source_ledger_record_from_source_inspected_event(event)


@pytest.mark.parametrize(
    ("metadata", "forbidden_key"),
    (
        pytest.param(
            {"safeLabel": "docs", "runnerInvoked": 1},
            "runnerInvoked",
            id="runner-invoked-int",
        ),
        pytest.param(
            {"safeLabel": "docs", "productionAuthority": -1},
            "productionAuthority",
            id="production-authority-negative-int",
        ),
        pytest.param(
            {"safeLabel": "docs", "webSearchExecuted": 0.5},
            "webSearchExecuted",
            id="web-search-float",
        ),
    ),
)
def test_source_inspected_event_rejects_numeric_truthy_live_claims_before_projection(
    metadata: dict[str, object],
    forbidden_key: str,
) -> None:
    event = _source_inspected_event(metadata=metadata)
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    with pytest.raises((ValueError, ValidationError)):
        source_ledger_record_from_source_inspected_event(event)
    with pytest.raises((ValueError, ValidationError)):
        ledger.record_source_inspected_event(event)

    report = public_source_ledger_report(ledger)
    dumped_source_metadata = json.dumps(
        [dict(source.metadata) for source in report.sources],
        sort_keys=True,
    )
    assert report.sources == ()
    assert forbidden_key not in dumped_source_metadata


@pytest.mark.parametrize(
    "forbidden_key",
    (
        "networkFetched",
        "browserWorkerAttached",
        "cdpSessionAttached",
        "rawSnapshotInjected",
        "rawToolLogInjected",
        "rawToolLogsInjected",
        "rawBrowserSnapshotInjected",
        "parentContextInjected",
        "parentContextRawInjection",
    ),
)
def test_source_inspected_event_rejects_web_acquisition_browser_live_raw_aliases_before_projection(
    forbidden_key: str,
) -> None:
    event = _source_inspected_event(metadata={"safeLabel": "docs", forbidden_key: True})
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    with pytest.raises((ValueError, ValidationError)):
        ledger.record_source_inspected_event(event)

    report = public_source_ledger_report(ledger)
    dumped_source_metadata = json.dumps(
        [dict(source.metadata) for source in report.sources],
        sort_keys=True,
    )
    assert report.sources == ()
    assert forbidden_key not in dumped_source_metadata


@pytest.mark.parametrize(
    "forbidden_key",
    (
        "networkFetched",
        "rawToolLogsInjected",
        "parentContextRawInjection",
    ),
)
def test_record_source_rejects_web_acquisition_browser_live_raw_aliases_before_projection(
    forbidden_key: str,
) -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    with pytest.raises((ValueError, ValidationError)):
        ledger.record_source(
            {
                "turnId": "turn-1",
                "toolName": "WebFetch",
                "evidenceType": "SourceInspection",
                "kind": "web_fetch",
                "uri": "https://docs.example.test/source",
                "inspected": True,
                "metadata": {"safeLabel": "docs", forbidden_key: True},
            }
        )

    report = public_source_ledger_report(ledger)
    dumped_source_metadata = json.dumps(
        [dict(source.metadata) for source in report.sources],
        sort_keys=True,
    )
    assert report.sources == ()
    assert forbidden_key not in dumped_source_metadata


@pytest.mark.parametrize(
    ("metadata", "forbidden_key"),
    (
        pytest.param(
            {"safeLabel": "docs", "networkFetched": [True]},
            "networkFetched",
            id="list-value",
        ),
        pytest.param(
            {"safeLabel": "docs", "rawToolLogsInjected": {"claim": True}},
            "rawToolLogsInjected",
            id="mapping-value",
        ),
        pytest.param(
            {"safeLabel": "docs", "parentContextRawInjection": (True,)},
            "parentContextRawInjection",
            id="tuple-value",
        ),
    ),
)
def test_source_inspected_event_rejects_container_live_raw_alias_values_before_projection(
    metadata: dict[str, object],
    forbidden_key: str,
) -> None:
    event = _source_inspected_event(metadata=metadata)
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    with pytest.raises((ValueError, ValidationError)):
        source_ledger_record_from_source_inspected_event(event)
    with pytest.raises((ValueError, ValidationError)):
        ledger.record_source_inspected_event(event)

    report = public_source_ledger_report(ledger)
    dumped_source_metadata = json.dumps(
        [dict(source.metadata) for source in report.sources],
        sort_keys=True,
    )
    assert report.sources == ()
    assert forbidden_key not in dumped_source_metadata


@pytest.mark.parametrize(
    ("source_overrides", "forbidden_key"),
    (
        pytest.param(
            {"networkFetched": [True]},
            "networkFetched",
            id="top-level-list-value",
        ),
        pytest.param(
            {"rawToolLogsInjected": {"claim": True}},
            "rawToolLogsInjected",
            id="top-level-mapping-value",
        ),
        pytest.param(
            {"parentContextRawInjection": (True,)},
            "parentContextRawInjection",
            id="top-level-tuple-value",
        ),
    ),
)
def test_source_inspected_event_rejects_top_level_container_live_raw_alias_values(
    source_overrides: dict[str, object],
    forbidden_key: str,
) -> None:
    event = _source_inspected_event(**source_overrides)
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    with pytest.raises((ValueError, ValidationError)):
        ledger.record_source_inspected_event(event)

    report = public_source_ledger_report(ledger)
    dumped_source_metadata = json.dumps(
        [dict(source.metadata) for source in report.sources],
        sort_keys=True,
    )
    assert report.sources == ()
    assert forbidden_key not in dumped_source_metadata


def test_record_source_rejects_false_forbidden_alias_metadata_but_preserves_flags() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    with pytest.raises((ValueError, ValidationError)):
        ledger.record_source(
            {
                "turnId": "turn-1",
                "toolName": "WebFetch",
                "evidenceType": "SourceInspection",
                "kind": "web_fetch",
                "uri": "https://docs.example.test/source",
                "inspected": True,
                "metadata": {"safeLabel": "docs", "networkFetched": False},
            }
        )

    record = ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/source",
            "inspected": True,
            "metadata": {"safeLabel": "docs"},
            "attachmentFlags": {"sourceFetched": False},
        }
    )

    assert record.attachment_flags.source_fetched is False
    assert set(record.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_public_source_ledger_report_omits_forbidden_alias_metadata_for_accepted_records() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/source",
            "inspected": True,
            "metadata": {"safeLabel": "docs"},
        }
    )

    report = public_source_ledger_report(ledger)
    dumped_source_metadata = json.dumps(
        [dict(source.metadata) for source in report.sources],
        sort_keys=True,
    )

    assert report.sources[0].metadata["safeLabel"] == "docs"
    assert "networkFetched" not in dumped_source_metadata
    assert "rawToolLogsInjected" not in dumped_source_metadata
    assert "parentContextRawInjection" not in dumped_source_metadata


def test_recorded_sources_receive_stable_refs_and_child_scope_metadata() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    parent = ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebSearch",
            "evidenceType": "WebSearch",
            "kind": "web_search",
            "uri": "search:openmagi adk citation ledger",
            "title": "Search results",
            "inspected": False,
        }
    )
    inspected = ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": _fixture_source_uri(),
            "title": "Example Docs",
            "contentHash": "sha256:abc",
            "contentType": "text/html",
            "trustTier": "official",
            "snippets": [_fixture_secret_snippet()],
            "inspected": True,
            "metadata": {_fixture_api_key_field(): _fixture_token(), "safeLabel": "docs"},
        }
    )
    child = ledger.record_source(
        {
            "turnId": "turn-1::spawn::child-a",
            "toolName": "KnowledgeSearch",
            "evidenceType": "KnowledgeSearch",
            "kind": "kb",
            "uri": "kb://collection/private-doc",
            "title": "Child KB result",
            "inspected": True,
            "scope": {
                "runOn": "child",
                "agentRole": "research",
                "spawnDepth": 1,
                "parentTurnId": "turn-1",
                "childTurnId": "child-a",
            },
        }
    )

    assert (parent.source_id, inspected.source_id, child.source_id) == (
        "src_1",
        "src_2",
        "src_3",
    )
    assert [record.source_id for record in ledger.snapshot()] == ["src_1", "src_2", "src_3"]
    assert [record.source_id for record in ledger.sources_for_turn("turn-1")] == [
        "src_1",
        "src_2",
        "src_3",
    ]
    assert child.scope.run_on == "child"
    assert child.scope.parent_turn_id == "turn-1"
    assert child.scope.child_turn_id == "child-a"
    assert child.scope.child_execution_attached is False
    assert set(child.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_public_source_ledger_report_redacts_uri_snippet_and_secret_metadata() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": _fixture_secret_uri(query_key="api_key"),
            "title": "Example Docs",
            "snippets": [_fixture_secret_snippet(prefix="secret snippet")],
            "inspected": True,
            "metadata": {
                _fixture_api_key_field(): _fixture_token(),
                "authorization": _fixture_bearer_value(),
                "safeLabel": "docs",
            },
        }
    )

    report = public_source_ledger_report(ledger)
    dumped = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert report.sources[0].source_id == "src_1"
    assert report.sources[0].inspected is True
    assert report.sources[0].uri == "[redacted]"
    assert report.sources[0].snippets == ("[redacted]",)
    assert report.sources[0].metadata[_fixture_api_key_field()] == "[redacted]"
    assert report.sources[0].metadata["authorization"] == "[redacted]"
    assert report.sources[0].metadata["safeLabel"] == "docs"
    assert _fixture_token() not in dumped
    assert _fixture_bearer_value() not in dumped
    assert "secret snippet" not in dumped
    assert "docs.example.test/private" not in dumped


def test_public_source_ledger_report_sanitizes_unsafe_title_secrets() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/private",
            "title": _fixture_auth_header(),
            "inspected": True,
        }
    )

    report = public_source_ledger_report(ledger)
    dumped = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert report.sources[0].title == "Authorization: Bearer [redacted]"
    assert _fixture_bearer_value() not in dumped


def test_public_source_ledger_report_redacts_private_paths_in_title_and_metadata() -> None:
    private_paths = (
        "/" + "Users/kevin/private/source.txt",
        "https://internal.example/customer/acme",
        "s3://private-bucket/customer/acme",
        "/" + "private/var/folders/zz/source.txt",
        "/" + "var/folders/zz/source.txt",
        "/" + "tmp/openmagi/source.txt",
        "/" + "opt/openmagi/source.txt",
        "/" + "srv/app/source.txt",
        "/" + "app/source.txt",
        "/" + "etc/passwd",
        "/" + "Applications/App/source.txt",
        "/" + "Volumes/Data/source.txt",
        "/" + "workspace",
        "/" + "data",
        "/" + "etc",
        "~" + "/secret/source.txt",
    )
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )
    for private_path in private_paths:
        ledger.record_source(
            {
                "turnId": "turn-1",
                "toolName": "WebFetch",
                "evidenceType": "SourceInspection",
                "kind": "web_fetch",
                "uri": "https://docs.example.test/private",
                "title": private_path,
                "inspected": True,
                "metadata": {
                    "safeLabel": private_path,
                    "nested": {"publicLabel": private_path},
                },
            }
        )

    dumped = json.dumps(public_source_ledger_report(ledger).model_dump(by_alias=True), sort_keys=True)

    for private_path in private_paths:
        assert private_path not in dumped
    assert "/Users/" not in dumped
    assert "internal.example" not in dumped
    assert "private-bucket" not in dumped
    assert "/private/var/" not in dumped
    assert "/var/folders/" not in dumped
    assert "/tmp/openmagi/" not in dumped
    assert "/opt/openmagi/" not in dumped
    assert "/srv/app/" not in dumped
    assert "/app/" not in dumped
    assert "/etc/passwd" not in dumped
    assert "/Applications/" not in dumped
    assert "/Volumes/" not in dumped
    assert "/workspace" not in dumped
    assert "/data" not in dumped
    assert "/etc" not in dumped
    assert "~/secret/" not in dumped


@pytest.mark.parametrize(
    ("ledger_id", "session_id", "turn_id"),
    (
        pytest.param(
            "/" + "Users/kevin/private/token=synthetic",
            "session:cookie=synthetic",
            "/" + "Users/kevin/private/token=synthetic/turn",
            id="multi-component-private-path-and-secret",
        ),
        pytest.param(
            "/" + "workspace",
            "/" + "data",
            "/" + "etc",
            id="single-component-private-paths",
        ),
    ),
)
def test_public_source_ledger_report_redacts_private_ledger_identifiers(
    ledger_id: str,
    session_id: str,
    turn_id: str,
) -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId=ledger_id,
        sessionId=session_id,
        turnId=turn_id,
        agentRole="research",
    )

    dumped = json.dumps(public_source_ledger_report(ledger).model_dump(by_alias=True), sort_keys=True)

    assert ledger_id not in dumped
    assert session_id not in dumped
    assert turn_id not in dumped
    assert "synthetic" not in dumped
    assert "/Users/" not in dumped
    assert "/workspace" not in dumped
    assert "/data" not in dumped
    assert "/etc" not in dumped
    assert "cookie=synthetic" not in dumped


def test_public_source_ledger_report_redacts_record_scope_private_turn_ids() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="/" + "workspace",
        agentRole="research",
    )
    ledger.record_source(
        {
            "turnId": "/" + "workspace::spawn::/data",
            "toolName": "KnowledgeSearch",
            "evidenceType": "KnowledgeSearch",
            "kind": "kb",
            "uri": "kb://collection/private-doc",
            "title": "Child KB result",
            "inspected": True,
        }
    )

    report = public_source_ledger_report(ledger)
    dumped = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert "/workspace" not in dumped
    assert "/data" not in dumped
    assert report.sources[0].scope.parent_turn_id == "[redacted]"
    assert report.sources[0].scope.child_turn_id == "[redacted]"


def test_source_evidence_record_public_report_redacts_private_tool_identifiers() -> None:
    private_tool_call = "/" + "Users/kevin/private/tool-call-token"
    record = SourceLedgerRecord.model_validate(
        {
            "sourceId": "src_1",
            "turnId": "turn-1",
            "toolName": "C:" + "\\Users\\kevin\\private\\WebFetch.exe",
            "toolUseId": private_tool_call,
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/source",
            "inspectedAt": 1,
            "inspected": True,
        }
    )

    report = public_evidence_record_report(record.to_evidence_record())
    dumped = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert private_tool_call not in dumped
    assert "/Users/" not in dumped
    assert "C:" not in dumped
    assert report.source["toolCallId"] == "[redacted]"
    assert report.source["toolName"] == "[redacted]"


def test_source_ledger_rejects_nan_inspected_at() -> None:
    with pytest.raises(ValidationError):
        SourceLedgerRecord.model_validate(
            {
                "sourceId": "src_1",
                "turnId": "turn-1",
                "toolName": "WebFetch",
                "evidenceType": "SourceInspection",
                "kind": "web_fetch",
                "uri": "https://docs.example.test/source",
                "inspectedAt": float("nan"),
            }
        )


def test_source_ledger_force_falses_forged_live_authority_flags() -> None:
    # C-4: ``SourceLedgerAttachmentFlags`` is owned by the
    # ``FalseOnlyAuthorityModel`` kernel; every Literal[False] flag is
    # force-falsed on every construction surface (validate, construct, copy).
    # The legacy ``_validate_false_flags`` raise validator is subsumed by the
    # kernel's coerce-on-validate path (strictly stronger -- the security
    # contract "live authority flags stay false" is preserved on every
    # construction surface, including the construct/copy escape hatches).
    coerced_validate = SourceLedgerAttachmentFlags.model_validate(
        {"webSearchExecuted": True}
    )
    assert coerced_validate.web_search_executed is False

    # Nested attachment flags inside a SourceLedgerRecord payload are also
    # force-falsed (the nested model is revalidated through the kernel).
    record = SourceLedgerRecord.model_validate(
        {
            "sourceId": "src_1",
            "turnId": "turn-1",
            "toolName": "WebSearch",
            "evidenceType": "WebSearch",
            "kind": "web_search",
            "uri": "search:example",
            "inspectedAt": 1,
            "attachmentFlags": {"liveToolDispatched": True},
        }
    )
    assert record.attachment_flags.live_tool_dispatched is False

    constructed = SourceLedgerAttachmentFlags.model_construct(web_search_executed=True)
    assert set(constructed.model_dump(by_alias=True).values()) == {False}
    copied = SourceLedgerAttachmentFlags().model_copy(update={"sourceFetched": True})
    assert set(copied.model_dump(by_alias=True).values()) == {False}


def test_source_ledger_import_stays_adk_toolhost_fetch_memory_route_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.evidence.source_ledger")
assert hasattr(module, "LocalResearchSourceLedger")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.runtime",
    "magi_agent.routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.memory",
    "magi_agent.browser",
    "magi_agent.search",
    "magi_agent.fetch",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"source ledger import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
