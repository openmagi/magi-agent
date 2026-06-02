"""Tests for the prompt/ package: types and split_system_prompt logic.

TDD approach: these tests are written BEFORE the implementation.
"""

from __future__ import annotations

import importlib
from types import ModuleType


def _types_module() -> ModuleType:
    try:
        return importlib.import_module("openmagi_core_agent.prompt.types")
    except ModuleNotFoundError as exc:
        import pytest
        pytest.fail(f"openmagi_core_agent.prompt.types module is missing: {exc}")


def _splitter_module() -> ModuleType:
    try:
        return importlib.import_module("openmagi_core_agent.prompt.splitter")
    except ModuleNotFoundError as exc:
        import pytest
        pytest.fail(f"openmagi_core_agent.prompt.splitter module is missing: {exc}")


def _prompt_module() -> ModuleType:
    try:
        return importlib.import_module("openmagi_core_agent.prompt")
    except ModuleNotFoundError as exc:
        import pytest
        pytest.fail(f"openmagi_core_agent.prompt module is missing: {exc}")


# ---------------------------------------------------------------------------
# Type model tests
# ---------------------------------------------------------------------------


def test_prompt_block_is_frozen_dataclass_with_text_and_cache_scope() -> None:
    types = _types_module()
    block = types.PromptBlock(text="hello", cache_scope="global")
    assert block.text == "hello"
    assert block.cache_scope == "global"

    block_no_scope = types.PromptBlock(text="dynamic part", cache_scope=None)
    assert block_no_scope.cache_scope is None

    # frozen: mutation must raise
    import pytest
    with pytest.raises((AttributeError, TypeError)):
        block.text = "changed"  # type: ignore[misc]


def test_prompt_cache_config_defaults_disabled() -> None:
    types = _types_module()
    config = types.PromptCacheConfig()
    assert config.enabled is False
    assert config.provider == "auto"
    assert config.static_section_keys == ()


def test_prompt_cache_config_custom_values() -> None:
    types = _types_module()
    config = types.PromptCacheConfig(
        enabled=True,
        provider="anthropic",
        static_section_keys=("bootstrap", "soul"),
    )
    assert config.enabled is True
    assert config.provider == "anthropic"
    assert config.static_section_keys == ("bootstrap", "soul")


def test_prompt_cache_config_is_frozen() -> None:
    import pytest
    types = _types_module()
    config = types.PromptCacheConfig()
    with pytest.raises((AttributeError, TypeError)):
        config.enabled = True  # type: ignore[misc]


def test_prompt_split_result_holds_tuple_of_blocks() -> None:
    types = _types_module()
    b1 = types.PromptBlock(text="static", cache_scope="global")
    b2 = types.PromptBlock(text="dynamic", cache_scope=None)
    result = types.PromptSplitResult(blocks=(b1, b2))
    assert len(result.blocks) == 2
    assert result.blocks[0] is b1
    assert result.blocks[1] is b2


def test_prompt_split_result_is_frozen() -> None:
    import pytest
    types = _types_module()
    result = types.PromptSplitResult(blocks=())
    with pytest.raises((AttributeError, TypeError)):
        result.blocks = (types.PromptBlock(text="x", cache_scope=None),)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Public __init__ re-exports
# ---------------------------------------------------------------------------


def test_prompt_package_exports_all_types_and_splitter() -> None:
    prompt = _prompt_module()
    assert hasattr(prompt, "PromptBlock")
    assert hasattr(prompt, "PromptCacheConfig")
    assert hasattr(prompt, "PromptSplitResult")
    assert hasattr(prompt, "split_system_prompt")


# ---------------------------------------------------------------------------
# split_system_prompt behaviour tests
# ---------------------------------------------------------------------------


def test_split_correct_static_dynamic_boundary() -> None:
    """static_indices={0, 1} marks first 2 parts static, rest dynamic."""
    splitter = _splitter_module()
    parts = ["static-A", "static-B", "dynamic-C", "dynamic-D"]
    result = splitter.split_system_prompt(parts, static_indices=frozenset({0, 1}))
    assert len(result.blocks) == 4
    assert result.blocks[0].text == "static-A"
    assert result.blocks[0].cache_scope == "global"
    assert result.blocks[1].text == "static-B"
    assert result.blocks[1].cache_scope == "global"
    assert result.blocks[2].text == "dynamic-C"
    assert result.blocks[2].cache_scope is None
    assert result.blocks[3].text == "dynamic-D"
    assert result.blocks[3].cache_scope is None


def test_split_all_static_when_all_indices_specified() -> None:
    splitter = _splitter_module()
    parts = ["a", "b", "c"]
    result = splitter.split_system_prompt(parts, static_indices=frozenset({0, 1, 2}))
    assert all(b.cache_scope == "global" for b in result.blocks)
    assert len(result.blocks) == 3


def test_split_all_dynamic_when_static_indices_empty() -> None:
    splitter = _splitter_module()
    parts = ["x", "y"]
    result = splitter.split_system_prompt(parts, static_indices=frozenset())
    assert all(b.cache_scope is None for b in result.blocks)
    assert len(result.blocks) == 2


def test_split_empty_parts_returns_empty_blocks() -> None:
    splitter = _splitter_module()
    result = splitter.split_system_prompt([], static_indices=frozenset())
    assert result.blocks == ()


def test_split_single_static_part() -> None:
    splitter = _splitter_module()
    result = splitter.split_system_prompt(["only"], static_indices=frozenset({0}))
    assert len(result.blocks) == 1
    assert result.blocks[0].cache_scope == "global"


def test_split_single_dynamic_part() -> None:
    splitter = _splitter_module()
    result = splitter.split_system_prompt(["only"], static_indices=frozenset())
    assert len(result.blocks) == 1
    assert result.blocks[0].cache_scope is None


def test_split_out_of_bounds_index_ignored() -> None:
    """Indices outside [0, len(parts)) must not raise and are silently ignored."""
    splitter = _splitter_module()
    result = splitter.split_system_prompt(["a", "b"], static_indices=frozenset({0, 99}))
    assert len(result.blocks) == 2
    # index 0 is in-bounds → static
    assert result.blocks[0].cache_scope == "global"
    # index 1 not in static_indices → dynamic
    assert result.blocks[1].cache_scope is None


def test_split_returns_prompt_split_result_instance() -> None:
    types = _types_module()
    splitter = _splitter_module()
    result = splitter.split_system_prompt(["p1", "p2"], static_indices=frozenset({0}))
    assert isinstance(result, types.PromptSplitResult)
    assert isinstance(result.blocks[0], types.PromptBlock)


def test_split_preserves_original_text_verbatim() -> None:
    splitter = _splitter_module()
    long_text = "line one\n\nline two\n\n---\n\nline three"
    result = splitter.split_system_prompt(
        [long_text, "short"], static_indices=frozenset({0})
    )
    assert result.blocks[0].text == long_text
    assert result.blocks[1].text == "short"


def test_split_negative_index_ignored() -> None:
    """Negative indices are out of range and must not crash or mark any block static."""
    splitter = _splitter_module()
    result = splitter.split_system_prompt(["a", "b"], static_indices=frozenset({-1}))
    assert all(b.cache_scope is None for b in result.blocks)


# ---------------------------------------------------------------------------
# Interleaved static/dynamic tests — reflecting actual build_system_prompt()
# ---------------------------------------------------------------------------


def test_split_interleaved_actual_layout_full_7_parts() -> None:
    """Mirrors the real 7-part build_system_prompt() layout.

    Positions: 0=session_header(D), 1=temporal_context(D),
               2=rendered_identity(S), 3=memory_mode_block(D),
               4=addendum(D), 5=DEFERRAL_PREVENTION_BLOCK(S),
               6=OUTPUT_RULES_BLOCK(S).
    """
    splitter = _splitter_module()
    parts = [
        "session_header",
        "temporal_context",
        "rendered_identity",
        "memory_mode_block",
        "addendum",
        "DEFERRAL_PREVENTION_BLOCK",
        "OUTPUT_RULES_BLOCK",
    ]
    result = splitter.split_system_prompt(
        parts, static_indices=frozenset({2, 5, 6})
    )
    assert len(result.blocks) == 7

    expected_scopes = [None, None, "global", None, None, "global", "global"]
    for i, expected in enumerate(expected_scopes):
        assert result.blocks[i].cache_scope == expected, (
            f"index {i}: expected {expected!r}, got {result.blocks[i].cache_scope!r}"
        )


def test_split_interleaved_4_parts_optional_absent() -> None:
    """4-part variant: memory_mode_block and addendum both absent.

    Positions: 0=session_header(D), 1=temporal_context(D),
               2=rendered_identity(S), 3=DEFERRAL_PREVENTION_BLOCK(S).
    Note: OUTPUT_RULES_BLOCK is at index 4 if present; absent here,
    so caller passes static_indices={2, 3}.
    """
    splitter = _splitter_module()
    parts = [
        "session_header",
        "temporal_context",
        "rendered_identity",
        "DEFERRAL_PREVENTION_BLOCK",
    ]
    result = splitter.split_system_prompt(
        parts, static_indices=frozenset({2, 3})
    )
    assert len(result.blocks) == 4
    assert result.blocks[0].cache_scope is None   # session_header
    assert result.blocks[1].cache_scope is None   # temporal_context
    assert result.blocks[2].cache_scope == "global"  # rendered_identity
    assert result.blocks[3].cache_scope == "global"  # DEFERRAL_PREVENTION_BLOCK


def test_split_interleaved_only_middle_index_is_static() -> None:
    """Static block sandwiched between two dynamic blocks."""
    splitter = _splitter_module()
    parts = ["dynamic-before", "static-middle", "dynamic-after"]
    result = splitter.split_system_prompt(parts, static_indices=frozenset({1}))
    assert result.blocks[0].cache_scope is None
    assert result.blocks[1].cache_scope == "global"
    assert result.blocks[2].cache_scope is None


def test_split_interleaved_last_two_indices_static() -> None:
    """Static blocks at the tail — like DEFERRAL + OUTPUT_RULES at end."""
    splitter = _splitter_module()
    parts = ["d0", "d1", "d2", "s3", "s4"]
    result = splitter.split_system_prompt(parts, static_indices=frozenset({3, 4}))
    assert result.blocks[0].cache_scope is None
    assert result.blocks[1].cache_scope is None
    assert result.blocks[2].cache_scope is None
    assert result.blocks[3].cache_scope == "global"
    assert result.blocks[4].cache_scope == "global"
