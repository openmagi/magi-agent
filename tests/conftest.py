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
    # any test that invokes the CLI / `serve` dispatch seeds the experimental
    # flat-flag set into os.environ via setdefault, and monkeypatch cannot
    # restore keys it did not itself set. Pin a conservative baseline (the same
    # neutralize-by-prior-setdefault trick as the two flags above) so a leaked
    # lab flag can never flip an unrelated test that asserts it OFF.
    #
    # IMPORTANT: baseline ONLY the lab-exclusive flags (in lab but NOT already in
    # the local-full overlay). The overlap flags (the learning loop,
    # observability, browser, etc.) already leak under the PRIOR `full` default,
    # so existing tests legitimately rely on them being ON ambiently; pinning
    # those OFF would regress them. The lab-exclusive flags were OFF in the
    # pre-lab ambient env, so this baseline just preserves that prior state while
    # neutralizing the new leak surface. A test exercising a lab feature opts in
    # via a local env dict or monkeypatch.setenv (both win over this baseline).
    from magi_agent.runtime.local_defaults import (  # noqa: PLC0415
        LAB_EXPERIMENTAL_FLAGS,
        LAB_EXPERIMENTAL_MODE_FLAGS,
        LOCAL_FULL_RUNTIME_ENV_DEFAULTS,
    )

    _full_overlay_keys = set(LOCAL_FULL_RUNTIME_ENV_DEFAULTS)
    for _flag in LAB_EXPERIMENTAL_FLAGS:
        if _flag not in _full_overlay_keys:
            os.environ.setdefault(_flag, "0")
    for _flag in LAB_EXPERIMENTAL_MODE_FLAGS:
        if _flag not in _full_overlay_keys:
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
