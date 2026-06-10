"""Tests for doc 05 PR-3 — bind emit-only recipe intents to runner effects.

The four intent families (``channel_intents`` / ``scheduler_intents`` /
``artifact_intents`` / ``provider_intents``) were materialized and emitted as
public payload metadata but had no consumer driving an actual runner effect.
PR-3 binds them at *hint* level behind the default-OFF
``MAGI_RECIPE_INTENT_BINDING_ENABLED`` stage gate, so that:

* gate OFF  -> intents stay payload-only (route selection byte-identical to main)
* gate ON   -> each intent family produces a corresponding hint/requirement
"""

from __future__ import annotations

from magi_agent.cli.engine import (
    RunnerPolicyAssembly,
    compile_intent_bindings,
)
from magi_agent.config.env import parse_recipe_intent_binding_enabled


def _assembly() -> RunnerPolicyAssembly:
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="claude-sonnet",
        providerIntents=("provider:web.search", "provider:memory.recall"),
        channelIntents=("channel:dispatcher.push", "channel:telegram.send_message"),
        artifactIntents=("artifact:prepare-delivery", "artifact:file-deliver"),
        schedulerIntents=("scheduler:cron.create", "scheduler:notify-user"),
    )


# --- gate parser -----------------------------------------------------------


def test_intent_binding_gate_default_off() -> None:
    assert parse_recipe_intent_binding_enabled({}) is False


def test_intent_binding_gate_explicit_on() -> None:
    assert parse_recipe_intent_binding_enabled(
        {"MAGI_RECIPE_INTENT_BINDING_ENABLED": "1"}
    ) is True
    assert parse_recipe_intent_binding_enabled(
        {"MAGI_RECIPE_INTENT_BINDING_ENABLED": "true"}
    ) is True


def test_intent_binding_gate_explicit_off() -> None:
    assert parse_recipe_intent_binding_enabled(
        {"MAGI_RECIPE_INTENT_BINDING_ENABLED": "0"}
    ) is False


# --- binding: OFF == no effect --------------------------------------------


def test_bindings_disabled_returns_empty() -> None:
    bindings = compile_intent_bindings(_assembly(), enabled=False)
    assert bindings == {}


def test_bindings_disabled_even_with_intents() -> None:
    # Even with all four intent families populated, OFF emits nothing.
    bindings = compile_intent_bindings(_assembly(), enabled=False)
    assert bindings == {}


# --- binding: ON == hint per family ---------------------------------------


def test_provider_intents_bind_to_model_hint() -> None:
    bindings = compile_intent_bindings(_assembly(), enabled=True)
    provider = bindings["providerPreferenceHints"]
    assert "provider:web.search" in provider
    assert "provider:memory.recall" in provider


def test_channel_intents_bind_to_delivery_hint() -> None:
    bindings = compile_intent_bindings(_assembly(), enabled=True)
    channel = bindings["channelDeliveryHints"]
    assert "channel:dispatcher.push" in channel
    assert "channel:telegram.send_message" in channel


def test_artifact_intents_bind_to_requirements() -> None:
    bindings = compile_intent_bindings(_assembly(), enabled=True)
    artifacts = bindings["artifactDeliveryRequirements"]
    assert "artifact:prepare-delivery" in artifacts
    assert "artifact:file-deliver" in artifacts


def test_scheduler_intents_bind_to_passthrough_hint() -> None:
    bindings = compile_intent_bindings(_assembly(), enabled=True)
    scheduler = bindings["schedulerReadinessHints"]
    assert "scheduler:cron.create" in scheduler
    assert "scheduler:notify-user" in scheduler


def test_bindings_are_hint_level_not_force() -> None:
    # Contract: hint-level binding never asserts production-write authority.
    bindings = compile_intent_bindings(_assembly(), enabled=True)
    assert bindings["enforcement"] == "hint"
    assert bindings["productionWriteAllowed"] is False


def test_empty_intents_yield_no_families() -> None:
    empty = RunnerPolicyAssembly(modelProvider="anthropic", modelLabel="m")
    bindings = compile_intent_bindings(empty, enabled=True)
    # Enforcement marker present, but no per-family keys when no intents exist.
    assert bindings["enforcement"] == "hint"
    assert "providerPreferenceHints" not in bindings
    assert "channelDeliveryHints" not in bindings
    assert "artifactDeliveryRequirements" not in bindings
    assert "schedulerReadinessHints" not in bindings


# --- route selection integration ------------------------------------------


def _phase_routing() -> dict[str, object]:
    return {
        "phaseRoutes": {
            "final_answer_drafting": {
                "provider": "anthropic",
                "model": "claude-sonnet",
            }
        }
    }


class _FakeAgent:
    tools: list[object] = []


class _FakeRunner:
    agent = _FakeAgent()


def _route_assembly() -> RunnerPolicyAssembly:
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="claude-sonnet",
        phaseRouting=_phase_routing(),
        providerIntents=("provider:web.search",),
        channelIntents=("channel:dispatcher.push",),
        artifactIntents=("artifact:file-deliver",),
        schedulerIntents=("scheduler:cron.create",),
    )


def _route_selection(monkeypatch, *, routing: str, binding: str | None):
    from magi_agent.cli import engine as engine_mod

    monkeypatch.setenv("MAGI_RUNNER_POLICY_ROUTING_ENABLED", routing)
    if binding is None:
        monkeypatch.delenv("MAGI_RECIPE_INTENT_BINDING_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MAGI_RECIPE_INTENT_BINDING_ENABLED", binding)

    driver = engine_mod.MagiEngineDriver.__new__(engine_mod.MagiEngineDriver)
    driver._runner_policy_assembly = _route_assembly()
    driver._runner_policy_routing_enabled = None
    return driver._runner_policy_route_selection(
        runner=_FakeRunner(),
        prompt="please summarize",
        harness_state=None,
    )


def test_route_selection_omits_bindings_when_gate_off(monkeypatch) -> None:
    sel = _route_selection(monkeypatch, routing="1", binding=None)
    assert sel is not None
    assert "intentBindings" not in sel


def test_route_selection_includes_bindings_when_gate_on(monkeypatch) -> None:
    sel = _route_selection(monkeypatch, routing="1", binding="1")
    assert sel is not None
    bindings = sel["intentBindings"]
    assert bindings["enforcement"] == "hint"
    assert "provider:web.search" in bindings["providerPreferenceHints"]
    assert "channel:dispatcher.push" in bindings["channelDeliveryHints"]
    assert "artifact:file-deliver" in bindings["artifactDeliveryRequirements"]
    assert "scheduler:cron.create" in bindings["schedulerReadinessHints"]
