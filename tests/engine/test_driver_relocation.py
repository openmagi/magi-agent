"""rem2/F5 (deep-review N-08 + N-26): engine driver + model runner move.

Moves the runtime-neutral engine driver core and the LiteLlm model runner
out of ``cli/`` into the ``magi_agent.engine`` kernel:

    cli/engine.py               -> engine/driver.py
    cli/event_projection.py     -> engine/event_projection.py
    cli/real_runner.py          -> engine/model_runner.py
    cli/litellm_empty_observer.py -> engine/litellm_empty_observer.py

Each old path is a ``sys.modules`` self-alias shim (same module object).
N-26: ``build_litellm_model`` is the public home for the single canonical
LiteLlm builder; ``memory/summarizer_runtime`` imports THAT instead of the
underscore-private symbol.
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_engine_driver_module_exists() -> None:
    import magi_agent.engine.driver as new

    assert new is not None


def test_old_and_new_paths_are_same_module() -> None:
    import magi_agent.cli.engine as old_engine
    import magi_agent.cli.event_projection as old_ep
    import magi_agent.cli.litellm_empty_observer as old_obs
    import magi_agent.cli.real_runner as old_rr
    import magi_agent.engine.driver as new_driver
    import magi_agent.engine.event_projection as new_ep
    import magi_agent.engine.litellm_empty_observer as new_obs
    import magi_agent.engine.model_runner as new_mr

    assert old_engine is new_driver
    assert old_rr is new_mr
    assert old_ep is new_ep
    assert old_obs is new_obs


def test_private_seam_names_survive_old_path() -> None:
    import magi_agent.cli.engine as old_engine
    import magi_agent.cli.real_runner as old_rr
    import magi_agent.engine.driver as new_driver
    import magi_agent.engine.model_runner as new_mr

    for name in (
        "_fold_usage",
        "_adk_usage_metadata",
        "_resolve_document_coverage_mode_with_preset",
    ):
        assert getattr(old_engine, name) is getattr(new_driver, name)
    for name in (
        "_build_litellm_model",
        "_model_api_base_kwargs",
        "set_per_turn_reasoning_effort",
    ):
        assert getattr(old_rr, name) is getattr(new_mr, name)


def test_public_build_litellm_model_alias() -> None:
    from magi_agent.engine.model_runner import (
        _build_litellm_model,
        build_litellm_model,
    )

    assert build_litellm_model is _build_litellm_model


def test_summarizer_uses_public_home() -> None:
    src = (
        Path(__file__).resolve().parents[2]
        / "magi_agent"
        / "memory"
        / "summarizer_runtime.py"
    ).read_text(encoding="utf-8")
    assert "from magi_agent.cli.real_runner import" not in src
    tree = ast.parse(src)
    offenders = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "magi_agent.cli.real_runner"
    ]
    assert offenders == []


def test_monkeypatch_on_old_path_reaches_canonical(monkeypatch) -> None:
    import magi_agent.engine.model_runner as new_mr

    monkeypatch.setattr(
        "magi_agent.cli.real_runner._model_api_base_kwargs",
        lambda env=None: {"api_base": "sentinel"},
    )
    assert new_mr._model_api_base_kwargs() == {"api_base": "sentinel"}
