"""Tests for PromptSectionCache — section memoization across turns.

TDD: written before memoizer.py exists.

Cases:
1. Cache hit — second call returns cached value without invoking compute_fn.
2. Cache miss — first call invokes compute_fn.
3. Cache break key — key in cache_break_keys always recomputes.
4. Invalidate specific key — after invalidate(), next get_or_compute recomputes.
5. Invalidate all — clears entire cache.
6. Multiple keys — independent caching per key.
7. Stats — reports correct cached_keys count.
8. Compute_fn side effects — verify compute_fn called correct number of times.
"""

from __future__ import annotations

import importlib
from types import ModuleType


def _memoizer_module() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.prompt.memoizer")
    except ModuleNotFoundError as exc:
        import pytest
        pytest.fail(f"magi_agent.prompt.memoizer module is missing: {exc}")


def _prompt_module() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.prompt")
    except ModuleNotFoundError as exc:
        import pytest
        pytest.fail(f"magi_agent.prompt module is missing: {exc}")


# ---------------------------------------------------------------------------
# Case 1: Cache hit — second call skips compute_fn
# ---------------------------------------------------------------------------


def test_cache_hit_skips_compute_fn_on_second_call() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    call_count = 0

    def compute() -> str:
        nonlocal call_count
        call_count += 1
        return "computed_value"

    first = cache.get_or_compute("soul", compute)
    second = cache.get_or_compute("soul", compute)

    assert first == "computed_value"
    assert second == "computed_value"
    assert call_count == 1  # compute_fn called only once


# ---------------------------------------------------------------------------
# Case 2: Cache miss — first call invokes compute_fn
# ---------------------------------------------------------------------------


def test_cache_miss_invokes_compute_fn_on_first_call() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    called = False

    def compute() -> str:
        nonlocal called
        called = True
        return "fresh_value"

    result = cache.get_or_compute("tools", compute)

    assert called is True
    assert result == "fresh_value"


# ---------------------------------------------------------------------------
# Case 3: Cache break key — always recomputes regardless of cache state
# ---------------------------------------------------------------------------


def test_cache_break_key_always_recomputes() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache(cache_break_keys=frozenset({"temporal_context"}))

    call_count = 0

    def compute() -> str:
        nonlocal call_count
        call_count += 1
        return f"turn_{call_count}"

    first = cache.get_or_compute("temporal_context", compute)
    second = cache.get_or_compute("temporal_context", compute)
    third = cache.get_or_compute("temporal_context", compute)

    assert first == "turn_1"
    assert second == "turn_2"
    assert third == "turn_3"
    assert call_count == 3


def test_non_break_key_is_cached_alongside_break_key() -> None:
    """Break keys only affect their own key; other keys still cache normally."""
    mod = _memoizer_module()
    cache = mod.PromptSectionCache(cache_break_keys=frozenset({"temporal_context"}))

    break_calls = 0
    static_calls = 0

    def compute_temporal() -> str:
        nonlocal break_calls
        break_calls += 1
        return "temporal"

    def compute_soul() -> str:
        nonlocal static_calls
        static_calls += 1
        return "soul_content"

    cache.get_or_compute("temporal_context", compute_temporal)
    cache.get_or_compute("temporal_context", compute_temporal)
    cache.get_or_compute("soul", compute_soul)
    cache.get_or_compute("soul", compute_soul)

    assert break_calls == 2   # temporal always recomputes
    assert static_calls == 1  # soul is cached after first call


# ---------------------------------------------------------------------------
# Case 4: Invalidate specific key — next call recomputes
# ---------------------------------------------------------------------------


def test_invalidate_specific_key_causes_recompute() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    call_count = 0

    def compute() -> str:
        nonlocal call_count
        call_count += 1
        return f"value_{call_count}"

    first = cache.get_or_compute("memory_mode", compute)
    assert first == "value_1"
    assert call_count == 1

    cache.invalidate("memory_mode")

    second = cache.get_or_compute("memory_mode", compute)
    assert second == "value_2"
    assert call_count == 2


def test_invalidate_nonexistent_key_is_a_noop() -> None:
    """Invalidating a key that was never cached must not raise."""
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()
    cache.invalidate("nonexistent_key")  # must not raise


def test_invalidate_key_does_not_affect_other_keys() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    soul_calls = 0
    tools_calls = 0

    def compute_soul() -> str:
        nonlocal soul_calls
        soul_calls += 1
        return "soul"

    def compute_tools() -> str:
        nonlocal tools_calls
        tools_calls += 1
        return "tools"

    cache.get_or_compute("soul", compute_soul)
    cache.get_or_compute("tools", compute_tools)

    cache.invalidate("soul")  # only invalidates "soul"

    cache.get_or_compute("soul", compute_soul)
    cache.get_or_compute("tools", compute_tools)  # should still be cached

    assert soul_calls == 2   # recomputed after invalidation
    assert tools_calls == 1  # still cached


# ---------------------------------------------------------------------------
# Case 5: Invalidate all — clears entire cache
# ---------------------------------------------------------------------------


def test_invalidate_all_clears_entire_cache() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    soul_calls = 0
    tools_calls = 0

    def compute_soul() -> str:
        nonlocal soul_calls
        soul_calls += 1
        return "soul"

    def compute_tools() -> str:
        nonlocal tools_calls
        tools_calls += 1
        return "tools"

    cache.get_or_compute("soul", compute_soul)
    cache.get_or_compute("tools", compute_tools)

    cache.invalidate_all()

    cache.get_or_compute("soul", compute_soul)
    cache.get_or_compute("tools", compute_tools)

    assert soul_calls == 2
    assert tools_calls == 2


def test_invalidate_all_on_empty_cache_is_a_noop() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()
    cache.invalidate_all()  # must not raise


# ---------------------------------------------------------------------------
# Case 6: Multiple keys — independent caching per key
# ---------------------------------------------------------------------------


def test_multiple_keys_cached_independently() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    results: dict[str, int] = {}

    def make_compute(key: str) -> object:
        def compute() -> str:
            results[key] = results.get(key, 0) + 1
            return f"{key}_content"
        return compute

    keys = ["soul", "tools", "memory_mode", "deferral_block", "output_rules"]
    for key in keys:
        cache.get_or_compute(key, make_compute(key))  # type: ignore[arg-type]
        cache.get_or_compute(key, make_compute(key))  # second call — should hit

    # Each key's compute_fn called exactly once (first call only)
    for key in keys:
        assert results.get(key, 0) == 1, f"Expected 1 call for '{key}', got {results.get(key)}"


# ---------------------------------------------------------------------------
# Case 7: Stats — reports correct cached_keys count
# ---------------------------------------------------------------------------


def test_stats_reports_zero_on_empty_cache() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()
    assert cache.stats == {"cached_keys": 0}


def test_stats_reports_count_after_caching() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    cache.get_or_compute("soul", lambda: "soul_content")
    cache.get_or_compute("tools", lambda: "tools_content")
    cache.get_or_compute("soul", lambda: "should_not_be_called")  # cache hit

    assert cache.stats == {"cached_keys": 2}


def test_stats_decrements_after_invalidate() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    cache.get_or_compute("soul", lambda: "soul_content")
    cache.get_or_compute("tools", lambda: "tools_content")
    assert cache.stats["cached_keys"] == 2

    cache.invalidate("soul")
    assert cache.stats["cached_keys"] == 1


def test_stats_zero_after_invalidate_all() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    cache.get_or_compute("soul", lambda: "s")
    cache.get_or_compute("tools", lambda: "t")
    cache.invalidate_all()
    assert cache.stats == {"cached_keys": 0}


def test_stats_break_keys_not_counted_when_never_cached() -> None:
    """Break keys that are never stored must not inflate the count."""
    mod = _memoizer_module()
    cache = mod.PromptSectionCache(cache_break_keys=frozenset({"temporal_context"}))

    cache.get_or_compute("temporal_context", lambda: "t1")
    cache.get_or_compute("temporal_context", lambda: "t2")

    # temporal_context is a break key — never stored → cached_keys must be 0
    assert cache.stats == {"cached_keys": 0}


# ---------------------------------------------------------------------------
# Case 8: Compute_fn side effects — verify call count precisely
# ---------------------------------------------------------------------------


def test_compute_fn_called_exactly_once_across_many_hits() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    call_count = 0

    def expensive_compute() -> str:
        nonlocal call_count
        call_count += 1
        return "expensive_result"

    for _ in range(100):
        result = cache.get_or_compute("identity", expensive_compute)
        assert result == "expensive_result"

    assert call_count == 1


def test_compute_fn_called_again_after_invalidate_and_then_cached() -> None:
    mod = _memoizer_module()
    cache = mod.PromptSectionCache()

    call_count = 0

    def compute() -> str:
        nonlocal call_count
        call_count += 1
        return "val"

    cache.get_or_compute("k", compute)  # miss → call 1
    cache.get_or_compute("k", compute)  # hit
    cache.invalidate("k")
    cache.get_or_compute("k", compute)  # miss again → call 2
    cache.get_or_compute("k", compute)  # hit again
    cache.get_or_compute("k", compute)  # hit again

    assert call_count == 2


# ---------------------------------------------------------------------------
# Public __init__ re-exports
# ---------------------------------------------------------------------------


def test_prompt_package_exports_prompt_section_cache() -> None:
    prompt = _prompt_module()
    assert hasattr(prompt, "PromptSectionCache"), (
        "PromptSectionCache must be exported from magi_agent.prompt"
    )


def test_prompt_section_cache_is_importable_from_package() -> None:
    prompt = _prompt_module()
    cls = getattr(prompt, "PromptSectionCache")
    cache = cls()
    assert cache.stats == {"cached_keys": 0}
