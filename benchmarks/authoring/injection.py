"""Model-factory injection seams for both conversational routes.

JSON cannot carry a callable, so the documented body keys (``_modelFactory``,
``_policyModelFactory``) are unreachable through a ``TestClient`` POST. The
working seams (verified at magi-agent origin/main 60bc91f8a) are two private,
module-level symbols the production code was explicitly written so tests can
patch:

- Route A ``POST /v1/app/customize/custom-rules/compile-interactive`` imports
  ``_build_criterion_model_factory`` from ``magi_agent.cli.wiring`` INSIDE the
  handler and passes the function itself as ``model_factory`` (the engine then
  calls it: ``model = model_factory()``). Patching the wiring attribute to a
  ``() -> model`` factory redirects the whole route.
- Route B ``POST /v1/app/policies/compile/interactive`` calls the module-level
  ``magi_agent.transport.customize._resolve_policy_compile_factory(body)`` which
  returns a ``() -> model`` factory. Patching it to ignore the body and return
  our factory redirects that route.

Both seams are private symbols (risk R3): a rename breaks the harness loudly,
and this module is the single place the coupling lives.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import pytest

    from benchmarks.authoring.fakes import ScriptedLlm


def use_scripted_llm(monkeypatch: "pytest.MonkeyPatch", scripted: "ScriptedLlm") -> None:
    """Redirect BOTH conversational compiler routes to ``scripted``.

    One fixture call, so tier code and the runner never hand-roll the two
    patches. The same ``scripted`` instance backs both routes; a scenario only
    ever drives one flow, so the FIFO cursor is unambiguous.
    """
    import magi_agent.cli.wiring as wiring
    import magi_agent.transport.customize as customize_transport

    factory = scripted.as_factory()

    # Route A: the fallback factory imported inside the handler.
    monkeypatch.setattr(wiring, "_build_criterion_model_factory", factory)

    # Route B: the module-level resolver called with the request body.
    monkeypatch.setattr(
        customize_transport,
        "_resolve_policy_compile_factory",
        lambda _body: factory,
    )
