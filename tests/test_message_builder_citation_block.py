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


_FLAG_OFF = {"MAGI_SOURCE_CITATION_ENABLED": "0"}
_FLAG_ON = {"MAGI_SOURCE_CITATION_ENABLED": "1"}


def _assemble(env):
    return _assemble_prompt_sections(
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
        env=env,
    )


def test_citation_block_appears_in_assembled_static_sections() -> None:
    # Flag OFF: the markdown-link convention ships (byte-identical to pre-change).
    static_parts, _dynamic = _assemble(_FLAG_OFF)
    joined = "\n".join(static_parts)
    assert "<citation-convention>" in joined
    assert "<source_citation>" not in joined


def test_citation_block_appears_after_output_rules_for_priming() -> None:
    static_parts, _dynamic = _assemble(_FLAG_OFF)
    joined = "\n".join(static_parts)
    citation_pos = joined.index("<citation-convention>")
    output_rules_pos = joined.index("<output-rules>")
    # Output rules first (general output discipline), then citation convention
    # (specific guidance for sourced facts).
    assert output_rules_pos < citation_pos


def test_flag_on_swaps_markdown_convention_for_src_n_block() -> None:
    # Flag ON: exactly one convention -- the [src_N] block replaces the
    # markdown-link block IN PLACE (never both, never neither).
    static_parts, _dynamic = _assemble(_FLAG_ON)
    joined = "\n".join(static_parts)
    assert "<source_citation>" in joined
    assert "<citation-convention>" not in joined


def test_flag_on_off_preserve_static_section_count() -> None:
    # The swap keeps the same number of static sections in both states, so the
    # cache-prefix layout (and downstream splitter indices) is unchanged.
    off_parts, _ = _assemble(_FLAG_OFF)
    on_parts, _ = _assemble(_FLAG_ON)
    assert len(off_parts) == len(on_parts)


def test_source_citation_block_bytes_match_web_search_tools_copy() -> None:
    # Drift guard: message_builder owns the canonical <source_citation> bytes
    # (its layer may not import magi_agent.tools), while web_search_tools keeps a
    # copy for the CLI-era guidance helper. They MUST stay byte-identical so the
    # model never sees two slightly different instructions for the same feature.
    from magi_agent.runtime.message_builder import SOURCE_CITATION_GUIDANCE_BLOCK
    from magi_agent.tools.web_search_tools import _SOURCE_CITATION_GUIDANCE

    assert SOURCE_CITATION_GUIDANCE_BLOCK == _SOURCE_CITATION_GUIDANCE
