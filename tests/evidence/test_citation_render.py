"""Wave 3a: pure render projection for source citations.

RED-first golden tests for ``magi_agent.evidence.citation_render``:
first-appearance numbering, dedup, dangling detection, tolerant display
normalization, verdict computation, and the terminal-frame payload shape.
No em-dashes anywhere per the citation feature style rule.
"""
from __future__ import annotations

from magi_agent.evidence.citation_registry import SessionSourceRegistry
from magi_agent.evidence.citation_render import (
    CitationRenderProjection,
    build_citations_payload,
    citations_payload_for,
    normalize_citation_token,
    project_citations,
    render_verdict,
)


def _registry(*uris: str) -> tuple[SessionSourceRegistry, list[str]]:
    """Register one web_fetch source per uri, returning (registry, source_ids)."""
    registry = SessionSourceRegistry(session_id="s-render")
    ids: list[str] = []
    for index, uri in enumerate(uris):
        record = registry.register(
            "web_fetch",
            uri,
            turn_id="t1",
            tool_name="web_fetch",
            title=f"Title {index}",
            trust_tier="secondary",
            inspected=True,
        )
        assert record is not None
        ids.append(record.source_id)
    return registry, ids


def test_first_appearance_numbering() -> None:
    registry, ids = _registry("https://a.example", "https://b.example")
    a, b = ids
    text = f"Revenue was up [{b}]. Cash held steady [{a}]."
    projection = project_citations(text, registry)
    # b appears first, so it is display index 1.
    assert projection.markers == ((b, 1), (a, 2))
    assert [s.display_index for s in projection.sources] == [1, 2]
    assert projection.sources[0].source_id == b
    assert projection.sources[0].uri == "https://b.example"
    assert projection.sources[0].kind == "web_fetch"
    assert projection.sources[0].trust_tier == "secondary"
    assert projection.sources[0].inspected is True
    assert projection.dangling_refs == ()


def test_repeated_ref_dedups_to_same_index() -> None:
    registry, ids = _registry("https://a.example")
    (a,) = ids
    text = f"First [{a}] and again [{a}] and once more [{a}]."
    projection = project_citations(text, registry)
    assert projection.markers == ((a, 1),)
    assert len(projection.sources) == 1


def test_dangling_ref_gets_no_entry_and_no_index() -> None:
    registry, ids = _registry("https://a.example")
    (a,) = ids
    text = f"Grounded [{a}] but fabricated [src_99]."
    projection = project_citations(text, registry)
    assert projection.markers == ((a, 1),)
    assert projection.dangling_refs == ("src_99",)
    # dangling ref consumes no display index
    assert [s.display_index for s in projection.sources] == [1]


def test_empty_and_no_citation_text() -> None:
    registry, _ = _registry("https://a.example")
    assert project_citations("", registry) == CitationRenderProjection()
    assert project_citations("plain answer, no citations", registry) == (
        CitationRenderProjection()
    )


def test_tolerant_normalization_display_only() -> None:
    # Tolerant near-misses normalize for DISPLAY only.
    assert normalize_citation_token("[src3]") == "src_3"
    assert normalize_citation_token("(src_3)") == "src_3"
    assert normalize_citation_token("[SRC_3]") == "src_3"
    assert normalize_citation_token("src_3") == "src_3"
    assert normalize_citation_token("footnote") is None
    # Marker/dangling extraction stays canonical: a malformed-only token is
    # neither a marker nor a dangling ref.
    registry, _ = _registry("https://a.example")
    projection = project_citations("malformed [src3] only", registry)
    assert projection.markers == ()
    assert projection.dangling_refs == ()


def test_verdict_states() -> None:
    registry, ids = _registry("https://a.example")
    (a,) = ids
    cited = project_citations(f"grounded [{a}]", registry)
    assert render_verdict(cited, has_registry_sources=True) == "cited"

    partial = project_citations(f"grounded [{a}] plus [src_99]", registry)
    assert render_verdict(partial, has_registry_sources=True) == "partial"

    uncited = project_citations("no markers here", registry)
    assert render_verdict(uncited, has_registry_sources=True) == "uncited"

    empty_registry = SessionSourceRegistry(session_id="s-empty")
    not_applicable = project_citations("no markers here", empty_registry)
    assert render_verdict(not_applicable, has_registry_sources=False) == (
        "not_applicable"
    )


def test_build_payload_shape() -> None:
    registry, ids = _registry("https://a.example")
    (a,) = ids
    payload = citations_payload_for(f"grounded [{a}]", registry)
    assert payload is not None
    assert payload["markers"] == [[a, 1]]
    assert payload["danglingRefs"] == []
    assert payload["verdict"] == "cited"
    assert payload["sources"] == [
        {
            "n": 1,
            "sourceId": a,
            "uri": "https://a.example",
            "title": "Title 0",
            "kind": "web_fetch",
            "trustTier": "secondary",
            "inspected": True,
        }
    ]
    # Wire sources entry never carries turn_id (that lives on the display entry).
    assert "turn_id" not in payload["sources"][0]
    assert "turnId" not in payload["sources"][0]


def test_citations_payload_none_registry() -> None:
    assert citations_payload_for("grounded [src_1]", None) is None


def test_not_applicable_payload_when_no_sources() -> None:
    empty_registry = SessionSourceRegistry(session_id="s-empty")
    payload = citations_payload_for("plain answer", empty_registry)
    assert payload is not None
    assert payload["verdict"] == "not_applicable"
    assert payload["markers"] == []
    assert payload["sources"] == []


def test_determinism() -> None:
    registry, ids = _registry("https://a.example", "https://b.example")
    a, b = ids
    text = f"[{a}] then [{b}] then [{a}] again"
    first = project_citations(text, registry)
    second = project_citations(text, registry)
    assert first == second


def test_direct_payload_builder_matches_helper() -> None:
    registry, ids = _registry("https://a.example")
    (a,) = ids
    projection = project_citations(f"grounded [{a}]", registry)
    verdict = render_verdict(projection, has_registry_sources=True)
    assert build_citations_payload(projection, verdict) == citations_payload_for(
        f"grounded [{a}]", registry
    )
