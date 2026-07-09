"""Self-tests for the authoring QA harness itself (its own TDD).

Part 1 (U1): ScriptedLlm, the ``use_scripted_llm`` injection helper covering
both conversational routes, and the two magi-agent turn-API adapters.

Part 2 (U2): the invariant engine (I1..I9) and the persisted-state oracles.

Everything here is ZERO-network: a scripted fake model stands in for the LLM
and the adapters run against an in-process ``TestClient``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Part 1: ScriptedLlm
# ---------------------------------------------------------------------------


def test_scripted_llm_yields_in_order() -> None:
    from benchmarks.authoring.fakes import ScriptedLlm

    scripted = ScriptedLlm(["first", "second"])
    factory = scripted.as_factory()

    import asyncio

    async def _drive(text_marker: str) -> str:
        from magi_agent.customize.shacl_compiler import _invoke_llm

        model = factory()
        return await _invoke_llm(
            model, text_marker, system_instruction="sys", prior_turns=()
        )

    assert asyncio.run(_drive("p1")) == "first"
    assert asyncio.run(_drive("p2")) == "second"


def test_scripted_llm_strict_exhaustion_raises() -> None:
    from benchmarks.authoring.fakes import ScriptedLlm, ScriptExhaustedError

    scripted = ScriptedLlm(["only"])
    factory = scripted.as_factory()

    import asyncio

    async def _drive() -> str:
        from magi_agent.customize.shacl_compiler import _invoke_llm

        model = factory()
        return await _invoke_llm(model, "p", system_instruction="s", prior_turns=())

    assert asyncio.run(_drive()) == "only"
    with pytest.raises(ScriptExhaustedError):
        asyncio.run(_drive())


def test_scripted_llm_captures_prompt_and_system() -> None:
    from benchmarks.authoring.fakes import ScriptedLlm

    scripted = ScriptedLlm(["r"])
    factory = scripted.as_factory()

    import asyncio

    async def _drive() -> None:
        from magi_agent.customize.shacl_compiler import _invoke_llm

        model = factory()
        await _invoke_llm(
            model,
            "the user prompt",
            system_instruction="the system persona",
            prior_turns=({"role": "user", "content": "earlier"},),
        )

    asyncio.run(_drive())
    assert len(scripted.capture_log) == 1
    cap = scripted.capture_log[0]
    assert cap.system_instruction == "the system persona"
    assert cap.prompt == "the user prompt"
    # prior turns are captured for golden assertions (e.g. answers reflected)
    assert any("earlier" in c for c in cap.contents)
    assert "the user prompt" in cap.contents[-1]


# ---------------------------------------------------------------------------
# Part 1: use_scripted_llm covers BOTH routes
# ---------------------------------------------------------------------------

_TOKEN = "test-gateway-token"


def _runtime():
    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from magi_agent.app import create_app

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def test_use_scripted_llm_patches_route_a_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route-A injection seam is wired to our factory.

    DRIFT (magi-agent bug at HEAD 60bc91f8a): route A's live LLM path is dead
    because ``_INTERACTIVE_SYSTEM_INSTRUCTION_TMPL.format(...)`` raises
    ``KeyError`` on the literal JSON-example braces in the template BEFORE the
    model factory is ever called; ``step_compile`` catches it and falls back to
    the deterministic "can't reach the AI compiler" path. So the scripted
    envelope cannot land in the draft over route A today. We therefore pin the
    SEAM (our factory replaced the production one) rather than end-to-end draft
    mutation. This test flips to end-to-end automatically once the engine
    template bug is fixed upstream.
    """
    import magi_agent.cli.wiring as wiring

    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.injection import use_scripted_llm

    envelope = json.dumps(
        {
            "assistant_message": "Which tool?",
            "draft_updates": {"what": {"kind": "tool_perm"}},
            "questions": [],
        }
    )
    scripted = ScriptedLlm([envelope])
    use_scripted_llm(monkeypatch, scripted)
    # The seam is our factory now.
    assert wiring._build_criterion_model_factory() is scripted

    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile-interactive",
        json={
            "history": [{"role": "user", "content": "block the Bash tool"}],
            "draft_so_far": {},
            "answers": {},
        },
    )
    # Route stays honest: 200 with a deterministic-fallback envelope.
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready_to_save"] is False


def test_use_scripted_llm_drives_route_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.injection import use_scripted_llm

    envelope = json.dumps(
        {
            "assistant_message": "Which label?",
            "param_updates": {"gatedTool": "execute_trade"},
        }
    )
    scripted = ScriptedLlm([envelope])
    use_scripted_llm(monkeypatch, scripted)

    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/v1/app/policies/compile/interactive",
        json={
            "history": [{"role": "user", "content": "gate execute_trade on a source"}],
            "paramsSoFar": {},
            "answers": {},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["params"]["gatedTool"] == "execute_trade"
    assert len(scripted.capture_log) == 1


# ---------------------------------------------------------------------------
# Part 1: adapters
# ---------------------------------------------------------------------------


def test_rule_flow_adapter_roundtrips_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.adapter import MagiRuleFlowAdapter
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.injection import use_scripted_llm

    envelope = json.dumps(
        {
            "assistant_message": "ok",
            "draft_updates": {"what": {"kind": "tool_perm"}},
            "questions": [],
        }
    )
    scripted = ScriptedLlm([envelope])
    use_scripted_llm(monkeypatch, scripted)
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")

    adapter = MagiRuleFlowAdapter(_runtime(), _TOKEN)
    assert adapter.flow == "single_rule"
    state = adapter.start(scenario=None)
    result = adapter.step(state, say="block the Bash tool", answers={})

    # 200 + normalized shape is what the adapter must guarantee. (Route A's live
    # LLM path is dead at this HEAD — see the seam test above — so the working
    # draft is the deterministic fallback, not the scripted envelope.)
    assert result.http_status == 200
    assert isinstance(result.working, dict)
    assert result.plan is None
    assert result.ready_to_save is False
    assert isinstance(result.missing, list)
    assert isinstance(result.questions, list)
    # The history echo contract: the user turn was appended.
    assert state.history[-1] == {"role": "user", "content": "block the Bash tool"}


def test_policy_flow_adapter_roundtrips_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.adapter import MagiPolicyFlowAdapter
    from benchmarks.authoring.fakes import ScriptedLlm
    from benchmarks.authoring.injection import use_scripted_llm

    envelope = json.dumps(
        {
            "assistant_message": "ok",
            "param_updates": {"gatedTool": "execute_trade"},
        }
    )
    scripted = ScriptedLlm([envelope])
    use_scripted_llm(monkeypatch, scripted)
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))

    adapter = MagiPolicyFlowAdapter(_runtime(), _TOKEN)
    assert adapter.flow == "linked_policy"
    state = adapter.start(scenario=None)
    result = adapter.step(state, say="gate execute_trade on a source", answers={})

    assert result.http_status == 200
    assert result.working["gatedTool"] == "execute_trade"
    assert result.ready_to_save is False


def test_adapter_threads_auth_and_isolates_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.adapter import MagiRuleFlowAdapter

    # No token -> the adapter must still send the header it was constructed
    # with; a wrong token yields 401.
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    adapter = MagiRuleFlowAdapter(_runtime(), "wrong-token")
    state = adapter.start(scenario=None)
    result = adapter.step(state, say="hello", answers={})
    assert result.http_status == 401
