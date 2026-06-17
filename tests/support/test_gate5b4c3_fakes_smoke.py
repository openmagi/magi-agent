from __future__ import annotations

from tests.support.gate5b4c3_fakes import make_primitives, _FakeRunner, text_event


def test_make_primitives_returns_loader_with_fake_runner() -> None:
    primitives = make_primitives(_FakeRunner([text_event("hi")]))
    assert primitives.Runner is not None
    assert callable(primitives.Agent)
