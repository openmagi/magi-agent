"""Test-pollution shield for the observability integration test.

``observability.runtime_sink._active_sink`` is a module global. Any
test in the wider suite that boots ``create_app(runtime)`` with
``MAGI_OBSERVABILITY_ENABLED`` happens to be ON in its environment
will publish a sink and leak it into the global, breaking the
``test_register_observability_disabled_when_env_unset`` invariant
("disabled-env ⇒ no sink") on subsequent test files.

The leak surfaces specifically when the customize HTTP e2e suite
(``tests/e2e/customize/test_http_*.py``) runs before this folder —
those files call ``create_app(runtime)`` per fixture and inherit
whatever ``MAGI_OBSERVABILITY_ENABLED`` value the operator's shell
set, leaving a real sink behind.

This autouse fixture is scoped to ``tests/observability/`` so it
does not interfere with tests that *intentionally* leave a sink
mounted (e.g., a future cross-test fixture that boots one shared
observability core). Per-test reset is the minimal correct shield
for the integration-test invariants.
"""

from __future__ import annotations

import pytest

from magi_agent.observability.runtime_sink import (
    get_active_sink,
    set_active_sink,
)


@pytest.fixture(autouse=True)
def _reset_observability_sink_between_tests() -> None:
    """Ensure each test in this folder starts and ends with no sink."""
    set_active_sink(None)
    yield
    # Defense-in-depth: a test that mounted a sink and forgot to
    # tear it down should not leak into the next test even within
    # this folder.
    if get_active_sink() is not None:
        set_active_sink(None)
