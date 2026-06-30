"""Tests for the ``CITATION_CONVENTION_BLOCK`` system-prompt block.

Why
---
For the dashboard "source-cited claims" feature, the model needs to mark
facts it's pulling from web/KB tool results with a citation the UI can
resolve back to ``inspectedSources``.  The simplest convention is standard
markdown link syntax: ``[short label](https://example.com/article)``.  When
the UI's reducer sees an ``<a href>`` whose URL matches an entry in
``channelState.inspectedSources``, it renders an inline citation chip with
the source title / trust tier / snippet on hover.

This block teaches the model to cite that way and to ONLY cite real URLs
from tool results (never invent one).  No tool-side changes — the model
already sees the URL in the WebSearch/WebFetch result.
"""
from __future__ import annotations

import importlib

from magi_agent.runtime import message_builder
from magi_agent.runtime.message_builder import (
    CITATION_CONVENTION_BLOCK,
    _assemble_prompt_sections,
)


def _utc(value: str):
    from datetime import datetime, timezone

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _builder():
    return importlib.import_module("magi_agent.runtime.message_builder")


# ---------------------------------------------------------------------------
# Block content + shape
# ---------------------------------------------------------------------------


def test_block_uses_markdown_link_convention() -> None:
    # The convention is standard markdown links so the model writes naturally
    # and the UI can match by href.
    assert "[" in CITATION_CONVENTION_BLOCK
    assert "](" in CITATION_CONVENTION_BLOCK


def test_block_tells_model_to_cite_only_real_urls() -> None:
    text = CITATION_CONVENTION_BLOCK.lower()
    assert "invent" in text or "fabricat" in text or "make up" in text


def test_block_is_wrapped_in_named_section_tags() -> None:
    assert CITATION_CONVENTION_BLOCK.startswith("<citation-convention>")
    assert CITATION_CONVENTION_BLOCK.rstrip().endswith("</citation-convention>")


def test_block_exported_as_module_constant() -> None:
    builder = _builder()
    assert hasattr(builder, "CITATION_CONVENTION_BLOCK")
    assert "<citation-convention>" in builder.CITATION_CONVENTION_BLOCK


# ---------------------------------------------------------------------------
# Inclusion in the assembled prompt
# ---------------------------------------------------------------------------


def test_citation_block_appears_in_assembled_static_sections() -> None:
    static_parts, _dynamic = _assemble_prompt_sections(
        session_key="s1",
        turn_id="t1",
        identity={},
        channel=None,
        user_message=None,
        runtime_now=_utc("2026-06-27T00:00:00Z"),
        timezone=None,
        coding_agent=False,
        model="claude-opus-4-8",
        model_aware_prompts_enabled=False,
    )
    joined = "\n".join(static_parts)
    assert "<citation-convention>" in joined


def test_citation_block_appears_after_output_rules_for_priming() -> None:
    static_parts, _dynamic = _assemble_prompt_sections(
        session_key="s1",
        turn_id="t1",
        identity={},
        channel=None,
        user_message=None,
        runtime_now=_utc("2026-06-27T00:00:00Z"),
        timezone=None,
        coding_agent=False,
        model="claude-opus-4-8",
        model_aware_prompts_enabled=False,
    )
    joined = "\n".join(static_parts)
    citation_pos = joined.index("<citation-convention>")
    output_rules_pos = joined.index("<output-rules>")
    # Output rules first (general output discipline), then citation convention
    # (specific guidance for sourced facts).
    assert output_rules_pos < citation_pos
