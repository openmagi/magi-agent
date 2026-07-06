"""Tests for magi_agent/evidence/citation_registry.py (Wave 1)."""
from __future__ import annotations


def test_registry_allocates_unique_ids_per_registration() -> None:
    """Same URI registered twice returns the SAME record/id (dedup)."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-dedup-1")
    r1 = reg.register("web_fetch", "https://example.com", turn_id="t1", tool_name="web_fetch")
    r2 = reg.register("web_fetch", "https://example.com", turn_id="t1", tool_name="web_fetch")
    assert r1 is not None
    assert r2 is not None
    assert r1.source_id == r2.source_id, "same URI must return same id"


def test_registry_dedup_different_urls_get_different_ids() -> None:
    """Two different URLs each get a unique source id."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-dedup-2")
    r1 = reg.register("web_fetch", "https://alpha.com", turn_id="t1", tool_name="web_fetch")
    r2 = reg.register("web_fetch", "https://beta.com", turn_id="t1", tool_name="web_fetch")
    assert r1 is not None
    assert r2 is not None
    assert r1.source_id != r2.source_id, "different URLs must get different ids"


def test_registry_canonical_uri_strips_fragments() -> None:
    """URL with #anchor deduped with URL without fragment."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-frag")
    r1 = reg.register(
        "web_fetch", "https://example.com/page#section", turn_id="t1", tool_name="web_fetch"
    )
    r2 = reg.register(
        "web_fetch", "https://example.com/page", turn_id="t1", tool_name="web_fetch"
    )
    assert r1 is not None
    assert r2 is not None
    assert r1.source_id == r2.source_id, "fragment-only difference must dedup"


def test_registry_canonical_uri_strips_tracker_params() -> None:
    """URL with utm_source=x deduped with clean URL."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-tracker")
    r1 = reg.register(
        "web_fetch",
        "https://example.com/page?utm_source=google&utm_medium=cpc",
        turn_id="t1",
        tool_name="web_fetch",
    )
    r2 = reg.register(
        "web_fetch", "https://example.com/page", turn_id="t1", tool_name="web_fetch"
    )
    assert r1 is not None
    assert r2 is not None
    assert r1.source_id == r2.source_id, "tracker params must be stripped for dedup"


def test_registry_canonical_uri_lowercases_scheme_host() -> None:
    """HTTPS://EXAMPLE.COM == https://example.com for dedup."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-case")
    r1 = reg.register(
        "web_fetch", "HTTPS://EXAMPLE.COM/path", turn_id="t1", tool_name="web_fetch"
    )
    r2 = reg.register(
        "web_fetch", "https://example.com/path", turn_id="t1", tool_name="web_fetch"
    )
    assert r1 is not None
    assert r2 is not None
    assert r1.source_id == r2.source_id, "scheme/host case must be normalized for dedup"


def test_registry_session_cap_saturates_gracefully() -> None:
    """500+ registrations: new ones after cap return None."""
    from magi_agent.evidence.citation_registry import (
        SessionSourceRegistry,
        _SESSION_SOURCE_CAP,
    )

    reg = SessionSourceRegistry(session_id="sess-cap")
    for i in range(_SESSION_SOURCE_CAP):
        rec = reg.register(
            "web_fetch", f"https://example.com/page-{i}", turn_id="t1", tool_name="web_fetch"
        )
        assert rec is not None, f"registration {i} before cap must succeed"

    overflow = reg.register(
        "web_fetch", "https://example.com/overflow", turn_id="t1", tool_name="web_fetch"
    )
    assert overflow is None, "new registration after cap must return None"
    assert reg.is_saturated


def test_registry_dedup_hit_at_cap_still_returns_record() -> None:
    """After cap, existing URL still returns its record (dedup hit)."""
    from magi_agent.evidence.citation_registry import (
        SessionSourceRegistry,
        _SESSION_SOURCE_CAP,
    )

    reg = SessionSourceRegistry(session_id="sess-cap-dedup")
    first_url = "https://example.com/page-0"
    r0 = reg.register("web_fetch", first_url, turn_id="t1", tool_name="web_fetch")
    assert r0 is not None

    for i in range(1, _SESSION_SOURCE_CAP):
        reg.register(
            "web_fetch", f"https://example.com/page-{i}", turn_id="t1", tool_name="web_fetch"
        )

    r_hit = reg.register("web_fetch", first_url, turn_id="t2", tool_name="web_fetch")
    assert r_hit is not None, "dedup hit at cap must still return existing record"
    assert r_hit.source_id == r0.source_id


def test_registry_revision_on_content_hash_change() -> None:
    """Same URL with a different content hash keeps the same id AND records a
    revision entry (design 7.2). Id stability alone is guaranteed by dedup, so
    this asserts the revision was actually appended."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-rev")
    r1 = reg.register(
        "web_fetch",
        "https://example.com",
        turn_id="t1",
        tool_name="web_fetch",
        content_hash="hash-v1",
    )
    r2 = reg.register(
        "web_fetch",
        "https://example.com",
        turn_id="t2",
        tool_name="web_fetch",
        content_hash="hash-v2",
    )
    assert r1 is not None
    assert r2 is not None
    assert r1.source_id == r2.source_id, "content-hash change must not change id"

    snap = reg.snapshot()
    rec = next((r for r in snap if r.source_id == r1.source_id), None)
    assert rec is not None
    revisions = rec.metadata.get("revisions")
    assert revisions, "a content-hash change must append a revisions entry"
    assert any(
        entry.get("contentHash") == "hash-v2" for entry in revisions
    ), "the revision entry must record the changed content hash"
    assert all(
        entry.get("contentHash") != "hash-v1" for entry in revisions
    ), "the original hash is the record hash, not a revision"


def test_registry_no_revision_when_content_hash_unchanged() -> None:
    """Re-reading the same URL with the SAME content hash records no revision."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-rev-noop")
    r1 = reg.register(
        "web_fetch", "https://example.com", turn_id="t1",
        tool_name="web_fetch", content_hash="hash-v1",
    )
    reg.register(
        "web_fetch", "https://example.com", turn_id="t2",
        tool_name="web_fetch", content_hash="hash-v1",
    )
    assert r1 is not None
    snap = reg.snapshot()
    rec = next((r for r in snap if r.source_id == r1.source_id), None)
    assert rec is not None
    assert not rec.metadata.get("revisions"), "identical hash must not add a revision"


def test_registry_snapshot_returns_all_records() -> None:
    """snapshot() returns all registered records."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-snap")
    reg.register("web_fetch", "https://a.com", turn_id="t1", tool_name="web_fetch")
    reg.register("web_fetch", "https://b.com", turn_id="t1", tool_name="web_fetch")
    snap = reg.snapshot()
    assert len(snap) == 2, f"expected 2 records in snapshot, got {len(snap)}"


def test_registry_lookup_by_kind_and_uri() -> None:
    """lookup() finds a registered record by kind and URI."""
    from magi_agent.evidence.citation_registry import SessionSourceRegistry

    reg = SessionSourceRegistry(session_id="sess-lookup")
    url = "https://example.com/article"
    r = reg.register("web_fetch", url, turn_id="t1", tool_name="web_fetch")
    assert r is not None

    found = reg.lookup("web_fetch", url)
    assert found is not None, "lookup must find the registered record"
    assert found.source_id == r.source_id


def test_flag_off_collector_emits_zero_citation_records(monkeypatch) -> None:
    """With citation flag OFF (safe/eval profile), record_tool_result produces
    ZERO citation records -- behavior is byte-identical to before wave 1."""
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    monkeypatch.delenv("MAGI_SOURCE_CITATION_ENABLED", raising=False)

    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.tools.result import ToolResult

    collector = LocalToolEvidenceCollector()
    result = ToolResult(status="ok", output={"url": "https://example.com"}, metadata={})
    records = collector.record_tool_result(
        session_id="s",
        turn_id="t",
        tool_call_id="c",
        tool_name="web_fetch",
        result=result,
        arguments={"url": "https://example.com"},
    )
    citation_records = [
        r
        for r in records
        if getattr(r, "producing_rule_id", None) == "source_citation.capture"
    ]
    assert citation_records == [], (
        "flag OFF (safe profile) must emit zero citation capture records"
    )
