"""Process-scope search backend cache (PR-D1 / N-12, N-13).

All fakes; the qmd binary is never required. Verifies:
  * the backend is constructed ONCE per (root, config knobs) across N calls,
  * distinct roots / distinct knobs get distinct backends,
  * clear_search_backend_cache() forces reconstruction,
  * bind_or_reindex binds qmd-like backends WITHOUT running an update, and
    falls back to a single reindex when bind fails / on plain backends.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.memory.search.backend_cache import (
    bind_or_reindex,
    cached_search_backend,
    clear_search_backend_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache() -> object:
    clear_search_backend_cache()
    yield
    clear_search_backend_cache()


def _config(**knobs: object) -> object:
    base = {
        "prefer_qmd": False,
        "prefer_qmd_auto_register": False,
        "vector_search": False,
        "qmd_endpoint": "",
    }
    base.update(knobs)
    return SimpleNamespace(**base)


class _CountingFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return object()


def test_factory_called_once_across_n_calls(tmp_path: Path) -> None:
    factory = _CountingFactory()
    config = _config()
    first = cached_search_backend(config, tmp_path, factory=factory)
    for _ in range(4):
        again = cached_search_backend(config, tmp_path, factory=factory)
        assert again is first
    assert factory.calls == 1


def test_distinct_roots_get_distinct_backends(tmp_path: Path) -> None:
    factory = _CountingFactory()
    config = _config()
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    a = cached_search_backend(config, root_a, factory=factory)
    b = cached_search_backend(config, root_b, factory=factory)
    assert a is not b
    assert factory.calls == 2


def test_config_knob_change_gets_new_backend(tmp_path: Path) -> None:
    factory = _CountingFactory()
    first = cached_search_backend(_config(prefer_qmd=False), tmp_path, factory=factory)
    second = cached_search_backend(_config(prefer_qmd=True), tmp_path, factory=factory)
    assert first is not second
    assert factory.calls == 2


def test_clear_cache_forces_reconstruction(tmp_path: Path) -> None:
    factory = _CountingFactory()
    config = _config()
    first = cached_search_backend(config, tmp_path, factory=factory)
    clear_search_backend_cache()
    second = cached_search_backend(config, tmp_path, factory=factory)
    assert first is not second
    assert factory.calls == 2


class _FakeQmdBackend:
    def __init__(self, *, bind_result: bool) -> None:
        self._bind_result = bind_result
        self._bound = False
        self.bind_calls = 0
        self.reindex_calls = 0

    @property
    def bound(self) -> bool:
        return self._bound

    def bind(self, root: Path) -> bool:
        self.bind_calls += 1
        if self._bind_result:
            self._bound = True
        return self._bind_result

    def reindex(self, root: Path) -> None:
        self.reindex_calls += 1


class _FakePlainBackend:
    def __init__(self) -> None:
        self.reindex_calls = 0

    def reindex(self, root: Path) -> None:
        self.reindex_calls += 1


def test_bind_or_reindex_qmd_like_binds_without_update(tmp_path: Path) -> None:
    backend = _FakeQmdBackend(bind_result=True)
    bind_or_reindex(backend, tmp_path)
    assert backend.bind_calls == 1
    assert backend.reindex_calls == 0
    # Second turn: bound short-circuit, no bind, no reindex (never a qmd update).
    bind_or_reindex(backend, tmp_path)
    assert backend.bind_calls == 1
    assert backend.reindex_calls == 0


def test_bind_or_reindex_falls_back_to_reindex_when_unbound(tmp_path: Path) -> None:
    backend = _FakeQmdBackend(bind_result=False)
    bind_or_reindex(backend, tmp_path)
    assert backend.bind_calls == 1
    assert backend.reindex_calls == 1


def test_bind_or_reindex_plain_backend_reindexes(tmp_path: Path) -> None:
    backend = _FakePlainBackend()
    bind_or_reindex(backend, tmp_path)
    assert backend.reindex_calls == 1
