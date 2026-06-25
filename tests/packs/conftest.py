"""Import isolation for pack tests.

Pack tests scaffold temp packs and load them via ``lazy_import_symbol``, which
imports a top-level module keyed by the pack DIR NAME and APPENDS the pack root
to ``sys.path`` (and leaves it). Across a full-suite run that leaks: a later
test's pack can resolve to a stale cached module imported by an earlier test
under the same top-level name (the documented unique-dir-name requirement in
``magi_agent/packs/loader.py``).

This autouse fixture purges ONLY the temp pack modules a test imported (those
whose ``__file__`` lives outside the repo tree and outside the interpreter
prefix) and restores ``sys.path``. It deliberately does NOT evict first-party
``magi_agent.*`` / site-packages / stdlib modules a test imported for the first
time, since evicting those would reset module-level singletons and break
identity (``isinstance``) across tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import magi_agent

_REPO_ROOT = Path(magi_agent.__file__).resolve().parent.parent
_PREFIXES = tuple(
    str(Path(p).resolve())
    for p in {sys.prefix, sys.base_prefix, str(_REPO_ROOT)}
)


def _is_temp_pack_module(name: str) -> bool:
    mod = sys.modules.get(name)
    file = getattr(mod, "__file__", None)
    if not file:
        return False
    try:
        resolved = str(Path(file).resolve())
    except OSError:
        return False
    return not resolved.startswith(_PREFIXES)


@pytest.fixture(autouse=True)
def _isolate_pack_imports():
    modules_before = set(sys.modules)
    path_before = list(sys.path)
    try:
        yield
    finally:
        for name in set(sys.modules) - modules_before:
            if _is_temp_pack_module(name):
                del sys.modules[name]
        sys.path[:] = path_before
