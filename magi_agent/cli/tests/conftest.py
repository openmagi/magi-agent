"""conftest.py for magi_agent/cli/tests.

Test-isolation fixtures
-----------------------

Two sources of inter-test pollution exist in this test directory:

1. **Environment pollution** — the installed/local CLI applies full runtime
   defaults into ``os.environ`` before wiring the runner. Tests must not leak
   those defaults into later module-level checks.

2. **Event-loop pollution** — tests that call ``asyncio.run(...)`` (directly or
   via Typer's ``CliRunner`` executing an async callback) close the event loop
   and do NOT reinstall a current loop.  On Python 3.10+ ``asyncio.run()``
   leaves ``asyncio.get_event_loop()`` raising ``RuntimeError``.  Textual's
   ``App.run_test()`` requires a usable current event loop, so any TUI test that
   runs after such a test fails with a ``MountError`` / "controller not ready".

3. **sys.modules pollution** — ``test_no_textual_imported`` deliberately removes
   all ``textual.*`` entries from ``sys.modules`` to verify cold-start import
   discipline.  If textual was already imported in the process (e.g. because an
   earlier TUI-related import occurred), deleting those entries forces a full
   re-import the next time textual is used.  After re-import, class objects are
   *different* from the ones that were already stored in widget instances
   created by a prior test.  Textual's ``compose`` machinery checks
   ``isinstance(widget, Widget)`` using the *newly-imported* ``Widget`` class,
   which does not match instances of the *old* class → ``MountError:
   Can't mount ...; expected a Widget instance``.

The ``restore_process_state`` fixture below is ``autouse=True`` so it applies
to every test in this directory.  Before each test it snapshots the set of
``sys.modules`` keys that belong to textual and the current event loop policy.
In teardown it:
  a. Restores ``os.environ`` to the pre-test snapshot.
  b. Reinstates any textual module entries that were present before the test
     (preventing class-identity breakage for subsequent tests).
  c. Installs a fresh, unclosed event loop via
     ``asyncio.set_event_loop(asyncio.new_event_loop())`` so no test leaks a
     closed/None loop to the next test.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest


def pytest_configure(config: pytest.Config) -> None:
    # Mirror tests/conftest.py: the durable evidence ledger is ON by default
    # (writes <cwd>/.magi/evidence). Now that first-party activity capture is
    # wired into the live CLI dispatcher, CLI tests that dispatch real tools
    # would litter the repo. Default the sink OFF here; tests exercising the
    # durable behavior opt in via monkeypatch.setenv to a tmp dir.
    _ = config
    os.environ.setdefault("MAGI_EVIDENCE_LEDGER_DIR", "off")


@pytest.fixture(autouse=True)
def restore_process_state():
    """Snapshot and restore env, textual sys.modules entries, and event loop."""
    environ_snapshot = dict(os.environ)

    # --- setup: capture textual module snapshot before the test runs ----------
    textual_snapshot: dict[str, object] = {
        k: v for k, v in sys.modules.items()
        if k == "textual" or k.startswith("textual.")
    }

    yield  # run the test

    # --- teardown ------------------------------------------------------------
    # 1. Restore environment variables mutated by CLI runtime-default wiring.
    os.environ.clear()
    os.environ.update(environ_snapshot)

    # 2. Restore any textual modules that were present before this test but are
    #    now missing (e.g. deleted by test_no_textual_imported).  This keeps
    #    class-identity consistent for subsequent tests.
    for key, module in textual_snapshot.items():
        if key not in sys.modules:
            sys.modules[key] = module  # type: ignore[assignment]

    # 3. Install a fresh event loop so the next test (especially Textual
    #    run_test()) sees a clean, unclosed loop regardless of what this test
    #    did to the loop (e.g. asyncio.run() leaving loop=None/closed).
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
