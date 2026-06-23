"""J-2 (docstring half) — ``cli/wiring.py`` ``model`` param docstring tells the truth.

Pre-J-2 the ``build_headless_runtime`` / ``build_tui_app`` docstrings
said the ``model`` parameter was "Reserved for future model-selection
wiring; accepted but not yet forwarded." That was already false: the
parameter is wired through ``_build_default_runner`` /
``_build_runner_policy_assembly`` into
``resolve_provider_config(model_override=model)``.

This test locks the docstring against re-staleness.
"""

from __future__ import annotations

import inspect


def _get_docstring(name: str) -> str:
    from magi_agent.cli import wiring

    obj = getattr(wiring, name)
    return inspect.getdoc(obj) or ""


def test_build_headless_runtime_docstring_does_not_claim_model_is_reserved() -> None:
    doc = _get_docstring("build_headless_runtime")
    lowered = doc.lower()
    assert "reserved for future model-selection wiring" not in lowered, (
        "wiring.build_headless_runtime docstring still says ``model`` is "
        "reserved-for-future. J-2 closed that: ``model`` is forwarded into "
        "the provider config used to build the default runner. Update the "
        "docstring to tell the truth."
    )


def test_build_headless_runtime_docstring_names_the_actual_wiring() -> None:
    """The truthful docstring must name the concrete call site so a future
    reader can trust + verify the claim by grep."""

    doc = _get_docstring("build_headless_runtime")
    assert "resolve_provider_config" in doc, (
        f"build_headless_runtime docstring should reference "
        f"resolve_provider_config (the actual call site). Got: {doc!r}"
    )
    assert "model_override" in doc, (
        f"docstring should mention the model_override parameter name. "
        f"Got: {doc!r}"
    )


def test_build_tui_app_docstring_does_not_claim_model_is_reserved() -> None:
    doc = _get_docstring("build_tui_app")
    lowered = doc.lower()
    assert "reserved for future model-selection wiring" not in lowered, (
        "wiring.build_tui_app docstring still says ``model`` is "
        "reserved-for-future. J-2 closed that: ``model`` is forwarded the "
        "same way as in build_headless_runtime."
    )
