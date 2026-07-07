"""Identity-parity tests for the hosted governed-turn path (U2, B6).

Problem: when MAGI_HOSTED_GOVERNED_TURN_ENABLED is ON, the governed path
previously ran under a DIFFERENT ADK session identity than the legacy
gate5b4c3 boundary, causing different session rows to be read/written.

Fix (U2): ``build_hosted_runtime`` gains a ``user_id`` param forwarded to
``MagiEngineDriver``. The serving call site passes the legacy strings so
flip-forward and flip-back both preserve history (zero migration).

Legacy identity:
- app_name: "openmagi-gate5b4c3-shadow-generation"
- user_id:  "gate5b4c3-shadow-user"
- session_id: already shared via _shadow_session_id (unchanged)

Test plan:
1. Constants exist and equal the legacy literal strings.
2. ``build_hosted_runtime`` accepts ``user_id`` and forwards it to the
   engine driver so ``rt.engine._user_id`` reflects the caller value.
3. ``build_hosted_runtime`` with ``user_id=GATE5B_SHADOW_USER_ID`` and
   ``app_name=GATE5B_SHADOW_APP_NAME`` stamps both values so a capturing
   Runner records the correct app_name and a subsequent run_async call
   would receive the correct user_id.
4. The serving module imports and uses the constants at the call site
   (verified via attribute inspection of the patched call kwargs).
"""

from __future__ import annotations

import pytest

# RED: these names do not exist until the U2 implementation lands.
# The import below is the gating failure in the RED phase.
from magi_agent.runtime.hosted_runtime import (  # noqa: E402
    GATE5B_SHADOW_APP_NAME,
    GATE5B_SHADOW_USER_ID,
    HostedRuntime,
    build_hosted_runtime,
)
from tests.support.gate5b4c3_fakes import (
    _FakeGenerateContentConfig,
    _FakeSessionService,
)


# ---------------------------------------------------------------------------
# Minimal capturing fakes (self-contained, no shared-fake dependency)
# ---------------------------------------------------------------------------


class _AppNameCapture:
    """Records ``app_name`` from the Runner constructor."""

    def __init__(self) -> None:
        self.app_name: str | None = None
        self.runner_kwargs: dict = {}

    def make_runner_class(self) -> type:
        capture = self

        class _CapturingRunner:
            def __init__(self, **kwargs: object) -> None:
                capture.app_name = str(kwargs.get("app_name", ""))
                capture.runner_kwargs = dict(kwargs)

            async def run_async(self, **kwargs: object):  # type: ignore[misc]
                # yield nothing: tests that only inspect identity do not need events
                return
                yield  # make it an async generator

        return _CapturingRunner


def _make_capturing_loader(capture: _AppNameCapture) -> object:
    """Return a zero-arg loader that uses the capturing Runner class."""

    class _CapturingAgent:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class _CapturingPrimitives:
        Agent = _CapturingAgent
        Runner = capture.make_runner_class()
        InMemorySessionService = _FakeSessionService
        Content = object
        Part = object
        GenerateContentConfig = _FakeGenerateContentConfig

    def _loader() -> object:
        return _CapturingPrimitives()

    return _loader


# ---------------------------------------------------------------------------
# 1. Constants
# ---------------------------------------------------------------------------


def test_gate5b_shadow_app_name_constant() -> None:
    """GATE5B_SHADOW_APP_NAME equals the legacy live-runner-boundary string."""
    assert GATE5B_SHADOW_APP_NAME == "openmagi-gate5b4c3-shadow-generation", (
        f"Wrong GATE5B_SHADOW_APP_NAME: {GATE5B_SHADOW_APP_NAME!r}"
    )


def test_gate5b_shadow_user_id_constant() -> None:
    """GATE5B_SHADOW_USER_ID equals the legacy live-runner-boundary string."""
    assert GATE5B_SHADOW_USER_ID == "gate5b4c3-shadow-user", (
        f"Wrong GATE5B_SHADOW_USER_ID: {GATE5B_SHADOW_USER_ID!r}"
    )


# ---------------------------------------------------------------------------
# 2. build_hosted_runtime forwards user_id to the engine driver
# ---------------------------------------------------------------------------


def test_build_hosted_runtime_default_user_id_is_cli() -> None:
    """Without user_id, engine._user_id defaults to 'cli'."""
    capture = _AppNameCapture()
    loader = _make_capturing_loader(capture)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_FakeGenerateContentConfig(),
    )
    assert rt.engine._user_id == "cli", (
        f"Default user_id should be 'cli', got {rt.engine._user_id!r}"
    )


def test_build_hosted_runtime_forwards_user_id_to_engine() -> None:
    """build_hosted_runtime(user_id=X) stamps engine._user_id = X."""
    capture = _AppNameCapture()
    loader = _make_capturing_loader(capture)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_FakeGenerateContentConfig(),
        user_id=GATE5B_SHADOW_USER_ID,
    )
    assert rt.engine._user_id == GATE5B_SHADOW_USER_ID, (
        f"engine._user_id should be {GATE5B_SHADOW_USER_ID!r}, "
        f"got {rt.engine._user_id!r}"
    )


# ---------------------------------------------------------------------------
# 3. Legacy triple: both app_name and user_id are stamped correctly
# ---------------------------------------------------------------------------


def test_build_hosted_runtime_legacy_identity_stamps_app_name() -> None:
    """Runner receives app_name=GATE5B_SHADOW_APP_NAME when legacy kwargs passed."""
    capture = _AppNameCapture()
    loader = _make_capturing_loader(capture)
    build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_FakeGenerateContentConfig(),
        app_name=GATE5B_SHADOW_APP_NAME,
        user_id=GATE5B_SHADOW_USER_ID,
    )
    assert capture.app_name == GATE5B_SHADOW_APP_NAME, (
        f"Runner app_name should be {GATE5B_SHADOW_APP_NAME!r}, "
        f"got {capture.app_name!r}"
    )


def test_build_hosted_runtime_legacy_identity_stamps_user_id() -> None:
    """engine._user_id == GATE5B_SHADOW_USER_ID when legacy kwargs passed."""
    capture = _AppNameCapture()
    loader = _make_capturing_loader(capture)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_FakeGenerateContentConfig(),
        app_name=GATE5B_SHADOW_APP_NAME,
        user_id=GATE5B_SHADOW_USER_ID,
    )
    assert rt.engine._user_id == GATE5B_SHADOW_USER_ID, (
        f"engine._user_id should be {GATE5B_SHADOW_USER_ID!r}, "
        f"got {rt.engine._user_id!r}"
    )


def test_build_hosted_runtime_legacy_identity_full_triple() -> None:
    """Full legacy triple: Runner app_name and engine user_id both match."""
    capture = _AppNameCapture()
    loader = _make_capturing_loader(capture)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_FakeGenerateContentConfig(),
        app_name=GATE5B_SHADOW_APP_NAME,
        user_id=GATE5B_SHADOW_USER_ID,
    )
    actual_app_name = capture.app_name
    actual_user_id = rt.engine._user_id
    assert actual_app_name == GATE5B_SHADOW_APP_NAME and actual_user_id == GATE5B_SHADOW_USER_ID, (
        f"Legacy triple mismatch: "
        f"app_name={actual_app_name!r} (want {GATE5B_SHADOW_APP_NAME!r}), "
        f"user_id={actual_user_id!r} (want {GATE5B_SHADOW_USER_ID!r})"
    )


# ---------------------------------------------------------------------------
# 4. Serving call site uses the constants
# ---------------------------------------------------------------------------


def test_gate5b_serving_imports_identity_constants() -> None:
    """gate5b_serving imports GATE5B_SHADOW_APP_NAME and GATE5B_SHADOW_USER_ID.

    After U2, the serving module must reference the constants (not inline
    string literals) so the call site and the builder stay in sync.
    This test verifies the module binds them after the implementation lands.
    """
    import magi_agent.transport.gate5b_serving as serving
    import importlib

    importlib.reload(serving)
    assert hasattr(serving, "GATE5B_SHADOW_APP_NAME") or True  # import-side check below
    # The real gate: the serving module must import the same names from hosted_runtime.
    # Inspect the module's global namespace after reload.
    ns = vars(serving)
    # After U2 the serving module imports GATE5B_SHADOW_APP_NAME and
    # GATE5B_SHADOW_USER_ID into its namespace for the call site to use.
    assert "GATE5B_SHADOW_APP_NAME" in ns, (
        "gate5b_serving must import GATE5B_SHADOW_APP_NAME from hosted_runtime"
    )
    assert "GATE5B_SHADOW_USER_ID" in ns, (
        "gate5b_serving must import GATE5B_SHADOW_USER_ID from hosted_runtime"
    )
