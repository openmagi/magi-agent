"""Task 3: ``pinned_recipe_pack_ids`` threaded from ``build_headless_runtime``
down to ``build_cli_model_runner``.

Strategy
--------
``build_headless_runtime`` falls back to a model-free stub when no provider
config resolves, leaving ``runner_policy_assembly`` as ``None``.  Rather than
forcing a live provider in CI (key-dependent, fragile), we assert the threading
at the ``build_cli_model_runner`` boundary — which Task 2 already proved
reflects pins in the assembly.

We patch:
- ``magi_agent.cli.providers.resolve_provider_config`` → dummy config
  (so ``_build_default_runner`` takes the real-runner branch).
- ``magi_agent.cli.real_runner.build_cli_model_runner`` → capturing stub
  (so no ADK/model dependency is needed).

This is a pure threading test: we assert the kwarg flows from
``build_headless_runtime`` → ``_build_default_runner`` → ``build_cli_model_runner``
unchanged.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_provider_config() -> MagicMock:
    """Minimal stand-in for a resolved ProviderConfig."""
    cfg = MagicMock()
    cfg.model = "fake-model"
    return cfg


def _fake_runner() -> MagicMock:
    """Stub runner returned in place of the real ADK runner."""
    runner = MagicMock()
    runner.agent = MagicMock()
    return runner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_headless_runtime_threads_pins_to_runner_builder(
    tmp_path, monkeypatch
) -> None:
    """``pinned_recipe_pack_ids`` flows through ``build_headless_runtime`` →
    ``_build_default_runner`` → ``build_cli_model_runner``.

    Assert the kwarg arrives unchanged at the deepest seam.
    """
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "cfg.toml"))

    captured: dict[str, object] = {}
    stub_runner = _fake_runner()

    def _capturing_build_cli_model_runner(config, **kwargs):
        captured.update(kwargs)
        return stub_runner

    with (
        patch(
            "magi_agent.cli.providers.resolve_provider_config",
            return_value=_fake_provider_config(),
        ),
        patch(
            "magi_agent.cli.real_runner.build_cli_model_runner",
            side_effect=_capturing_build_cli_model_runner,
        ),
        # Stub out heavyweight ADK build helpers that depend on provider setup.
        patch(
            "magi_agent.cli.wiring._build_first_party_adk_tools",
            return_value=[],
        ),
        patch(
            "magi_agent.harness.general_automation.live_gate"
            ".GeneralAutomationReceiptLedgerStore",
            return_value=MagicMock(),
        ),
        patch(
            "magi_agent.evidence.local_tool_collector.LocalToolEvidenceCollector",
            return_value=MagicMock(),
        ),
    ):
        from magi_agent.cli.wiring import build_headless_runtime

        build_headless_runtime(
            cwd=str(tmp_path),
            session_id="t3-pin-threading",
            pinned_recipe_pack_ids=["openmagi.dev-coding"],
        )

    assert "pinned_recipe_pack_ids" in captured, (
        "build_cli_model_runner was not called with pinned_recipe_pack_ids; "
        f"captured kwargs: {list(captured)!r}"
    )
    assert list(captured["pinned_recipe_pack_ids"]) == ["openmagi.dev-coding"], (
        f"expected ['openmagi.dev-coding'], got {captured['pinned_recipe_pack_ids']!r}"
    )


def test_empty_pins_preserved_through_threading(tmp_path, monkeypatch) -> None:
    """Empty pin list → ``build_cli_model_runner`` receives an empty sequence.

    Ensures the param is not accidentally dropped when no pins are supplied
    (byte-identical to pre-patch callers).
    """
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "cfg.toml"))

    captured: dict[str, object] = {}
    stub_runner = _fake_runner()

    def _capturing_build_cli_model_runner(config, **kwargs):
        captured.update(kwargs)
        return stub_runner

    with (
        patch(
            "magi_agent.cli.providers.resolve_provider_config",
            return_value=_fake_provider_config(),
        ),
        patch(
            "magi_agent.cli.real_runner.build_cli_model_runner",
            side_effect=_capturing_build_cli_model_runner,
        ),
        patch(
            "magi_agent.cli.wiring._build_first_party_adk_tools",
            return_value=[],
        ),
        patch(
            "magi_agent.harness.general_automation.live_gate"
            ".GeneralAutomationReceiptLedgerStore",
            return_value=MagicMock(),
        ),
        patch(
            "magi_agent.evidence.local_tool_collector.LocalToolEvidenceCollector",
            return_value=MagicMock(),
        ),
    ):
        from magi_agent.cli.wiring import build_headless_runtime

        build_headless_runtime(
            cwd=str(tmp_path),
            session_id="t3-empty-pins",
        )

    pins = captured.get("pinned_recipe_pack_ids", "MISSING")
    assert list(pins) == [], (
        f"expected empty pins, got {pins!r}"
    )
