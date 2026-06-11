from __future__ import annotations

import asyncio
import inspect
import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "asyncio: run an async test function in an asyncio event loop",
    )
    # The durable evidence ledger is ON by default (writes <cwd>/.magi/evidence)
    # — keep the suite from littering the repo. Tests exercising the default-ON
    # behavior delenv this and chdir to a tmp_path.
    os.environ.setdefault("MAGI_EVIDENCE_LEDGER_DIR", "off")
    # Same hygiene for the (full-profile default-ON) research audit: ambient
    # env leaked by profile-applying tests must not add audit frames to
    # unrelated projection tests. Audit tests opt in via monkeypatch.setenv.
    os.environ.setdefault("MAGI_RESEARCH_GOVERNANCE_MODE", "off")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    if "asyncio" not in pyfuncitem.keywords:
        return None
    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(test_function(**kwargs))
    return True
