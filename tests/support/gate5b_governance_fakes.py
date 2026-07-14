"""Fake ADK primitives that record ``Runner`` construction kwargs (P5-M1b).

Used by the governance-wiring tests to assert that a ``control_plane_plugins``
sequence is (or is not) forwarded into the ADK Runner by ``build_hosted_runtime``
-- the governed serving path's Runner-construction seam, which replaced the
retired ``Gate5B4C3LiveRunnerBoundary`` Runner construction. The assertion is
identical to the legacy one (``plugins`` kwarg present iff plugins supplied); only
the construction seam moved from the boundary class to ``build_hosted_runtime``.
"""
from __future__ import annotations

from magi_agent.shadow.gate5b4c3_live_runner_boundary import Gate5B4C3LiveAdkPrimitives


class _RecordingRunner:
    """Records the kwargs it was constructed with on a class-level attribute."""

    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = dict(kwargs)


class _FakeAgent:
    def __init__(self, **kwargs: object) -> None:
        pass


class _FakeSessionService:
    pass


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def from_text(cls, *, text: str) -> "_FakePart":
        return cls(text)


class _FakeContent:
    def __init__(self, *, parts: list, role: str | None = None) -> None:
        self.parts = parts
        self.role = role


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def build_plugin_recording_primitives() -> tuple[Gate5B4C3LiveAdkPrimitives, type[_RecordingRunner]]:
    """Return ``(primitives, RunnerClass)``.

    ``RunnerClass.created_kwargs`` holds the kwargs the last-built Runner
    received, so a test can assert ``"plugins" in RunnerClass.created_kwargs``.
    A fresh Runner subclass is returned each call so the recorded kwargs are
    isolated per test.
    """

    class _Runner(_RecordingRunner):
        created_kwargs: dict[str, object] = {}

    primitives = Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_Runner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )
    return primitives, _Runner
