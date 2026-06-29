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
    # The default runtime tier is now `lab` (apply_runtime_profile_defaults), so
    # any test that invokes the CLI / `serve` dispatch seeds the whole
    # experimental flat-flag set into os.environ via setdefault — and monkeypatch
    # cannot restore keys it did not itself set. Pin a conservative baseline for
    # the entire lab set (same neutralize-by-prior-setdefault trick as the two
    # flags above) so a leaked lab flag can never flip an unrelated test that
    # asserts it OFF. This matches the registry default-OFF import behavior; a
    # test exercising a lab feature opts in via a local env dict or
    # monkeypatch.setenv (both win over this baseline).
    from magi_agent.runtime.local_defaults import (  # noqa: PLC0415
        LAB_EXPERIMENTAL_FLAGS,
        LAB_EXPERIMENTAL_MODE_FLAGS,
    )

    for _flag in LAB_EXPERIMENTAL_FLAGS:
        os.environ.setdefault(_flag, "0")
    for _flag in LAB_EXPERIMENTAL_MODE_FLAGS:
        os.environ.setdefault(_flag, "off")


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
