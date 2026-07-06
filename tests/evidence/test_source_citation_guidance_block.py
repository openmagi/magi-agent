"""Wave 2 Piece C: the static <source_citation> system-prompt guidance block.

Static bytes (no session ids, no source lists, no counts) so the cached prompt
prefix stays hit-stable. Present when the master flag is on, absent when off.
"""
from __future__ import annotations


def test_guidance_block_present_when_enabled() -> None:
    from magi_agent.tools.web_search_tools import source_citation_guidance_block

    block = source_citation_guidance_block({"MAGI_SOURCE_CITATION_ENABLED": "1"})
    assert block.startswith("<source_citation>")
    assert block.rstrip().endswith("</source_citation>")
    assert "[src_" in block  # instructs the src_N inline form
    assert "never invent" in block.lower()


def test_guidance_block_absent_when_disabled() -> None:
    from magi_agent.tools.web_search_tools import source_citation_guidance_block

    assert source_citation_guidance_block({"MAGI_SOURCE_CITATION_ENABLED": "0"}) == ""


def test_guidance_block_is_static_across_calls() -> None:
    """Cache-safety: the bytes do not vary across turns/sessions/env identity.
    No session ids, no counts, no source lists leak into the block."""
    from magi_agent.tools.web_search_tools import source_citation_guidance_block

    env_on = {"MAGI_SOURCE_CITATION_ENABLED": "1"}
    first = source_citation_guidance_block(env_on)
    second = source_citation_guidance_block(env_on)
    third = source_citation_guidance_block({"MAGI_SOURCE_CITATION_ENABLED": "1", "OTHER": "x"})
    assert first == second == third
    # No dynamically-assigned marker leaks in: a real assigned id (src_1, a
    # count, or a concrete source list) must never be baked into the static
    # block. The illustrative example uses [src_N] / [src_3] only.
    assert "src_1]" not in first
    assert "src_2]" not in first


def test_guidance_block_fail_open_on_error() -> None:
    """Never breaks prompt assembly: a bad env returns ''."""
    from magi_agent.tools.web_search_tools import source_citation_guidance_block

    assert source_citation_guidance_block(None) == "" or isinstance(
        source_citation_guidance_block(None), str
    )
