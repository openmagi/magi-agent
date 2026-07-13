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
    # WS2 PR2a: the memory MASTER (MAGI_MEMORY_ENABLED) is now setdefault-ON by
    # the full-profile overlay + the CLI memory bootstrap. A test that runs that
    # bootstrap against os.environ leaks the master ON, and because the master
    # CASCADES the memory sub-flags (recall / projection / qmd) ON, the many
    # "gate off by default" tests (which only clear their own sub-flag) would
    # then see the subsystem live. Pin the master OFF here at configure time so
    # every leaker's setdefault is a no-op; tests that exercise memory-ON
    # behavior opt in explicitly via monkeypatch.setenv (which still wins).
    os.environ.setdefault("MAGI_MEMORY_ENABLED", "0")
    # PR-3: the hosted session-reuse lease + durable SQLite substrate flip to
    # profile-aware default-ON. The large boundary/serving suites were written
    # against the default-OFF (fresh-service-per-turn) behavior and share the
    # process-global lease registry, so leaving these ON by default would leak
    # session state across tests. Pin them OFF at configure time (same hygiene
    # as MAGI_MEMORY_ENABLED above); tests exercising reuse / the durable
    # substrate opt in explicitly via monkeypatch.setenv (which still wins).
    os.environ.setdefault("MAGI_HOSTED_SESSION_REUSE", "0")
    os.environ.setdefault("MAGI_HOSTED_SESSION_DB", "0")
    # WS-A (full first-party activation): the full-profile overlay now seeds
    # every first-party accuracy/verification/guard gate ON. A test that runs
    # the profile bootstrap against os.environ leaks these ON to every later
    # test in the same xdist worker, so the many "gate off by default" contract
    # tests (which assert the byte-identical OFF path without forcing it) fail
    # non-deterministically per shard. Pin each OFF at configure time so every
    # leaker's setdefault is a no-op; tests exercising the ON behavior opt in
    # explicitly via monkeypatch.setenv (which still wins over setdefault).
    for _ws_a_gate in (
        "MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED",
        "MAGI_FINAL_OUTPUT_GATE_LOCAL_ENABLED",
        "MAGI_GROUNDED_ANSWER_GUARD_ENABLED",
        "MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED",
        "MAGI_CHILD_BASH_SANDBOX_ENABLED",
        "MAGI_SERVE_EVIDENCE_ENABLED",
        "MAGI_FORMAT_ADHERENCE_ENABLED",
        "MAGI_COMPUTE_VIA_CODE_ENABLED",
        "MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED",
        "MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED",
        "MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED",
        "MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED",
        "MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED",
        "MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED",
        "MAGI_GATE5B_GOVERNANCE_ENABLED",
    ):
        os.environ.setdefault(_ws_a_gate, "0")


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
