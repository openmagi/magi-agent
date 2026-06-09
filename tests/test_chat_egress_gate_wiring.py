"""Tests for the PR3 egress critic gate wiring into chat.py.

Covers:
  - flag OFF (and unset) -> ``_python_ready_response`` body is byte-identical to
    a body with no verifierEvidenceStatus (no gate side effects).
  - the live ledger reachability: ``_build_egress_evidence_view`` projects
    files_read from the live host's ReadLedger and tool_calls from its receipts.
  - flag ON + a constructed live bundle + fake model -> status set on payload
    body.

Fake-model only (NO real LLM).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

import magi_agent.transport.chat as chat
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.gates.gate5b_full_toolhost import build_gate5b_full_toolhost_bundle
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


# ---------------------------------------------------------------------------
# Fake ADK model
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _llm(json_text: str):
    def _factory() -> object:
        class _FakeLlm:
            model = "fake-model"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                yield _FakeLlmResponse(json_text)

        return _FakeLlm()

    return _factory


# ---------------------------------------------------------------------------
# Live bundle helpers
# ---------------------------------------------------------------------------


def _ready_bundle(tmp_path):
    return build_gate5b_full_toolhost_bundle(
        config={},
        scope={},
        workspace_root=str(tmp_path),
        read_ledger_enabled=True,
    )


def _seed_live_evidence(bundle) -> None:
    """Populate a real ReadLedger entry + a real tool receipt on the host."""
    host = bundle.host
    host.read_ledger.record_read(
        session_id="live-session",
        workspace_ref=host._read_ledger_workspace_ref,
        path="notes.md",
        digest="sha256:" + "a" * 64,
        size_bytes=42,
        mtime_ns=123,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tu-1",
    )
    host.counter.finish_call(
        request_digest="d" * 64,
        tool_call_digest="e" * 64,
        argument_digest="f" * 64,
        tool_name="Grep",
        status="ok",
        output_preview="hit",
        output_byte_count=3,
    )


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-egress",
            user_id="user-egress",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        )
    )


# ---------------------------------------------------------------------------
# Live ledger reachability — the key integration
# ---------------------------------------------------------------------------


def test_build_egress_view_reads_live_readledger_and_receipts(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path)
    _seed_live_evidence(bundle)

    view = chat._build_egress_evidence_view(bundle)

    assert [f.path for f in view.files_read] == ["notes.md"]
    assert view.files_read[0].bytes == 42
    assert [t.name for t in view.tool_calls] == ["Grep"]
    assert view.tool_calls[0].status == "ok"
    assert view.scope.session_id == "live-session"


def test_build_egress_view_empty_when_no_activity(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path)
    view = chat._build_egress_evidence_view(bundle)
    assert view.files_read == ()
    assert view.tool_calls == ()


# ---------------------------------------------------------------------------
# Gate wiring helper — fact-critical + grounded -> passed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_helper_missing_fact_critical_key_fails_open(tmp_path) -> None:
    """A fake model that omits ``fact_critical`` -> classifier_error -> None."""
    bundle = _ready_bundle(tmp_path)
    _seed_live_evidence(bundle)
    payload = {
        "messages": [{"role": "user", "content": "what did you find in notes.md?"}],
        "_egressCriticModelFactory": _llm('{"grounded": true, "relevant": true}'),
    }
    status = await chat._maybe_run_egress_critic_gate(
        payload=payload,
        draft_text="notes.md says hello",
        gate1a_bundle=bundle,
    )
    assert status is None  # fail-open: classifier could not parse fact_critical


@pytest.mark.asyncio
async def test_gate_helper_full_path_passed(tmp_path, monkeypatch) -> None:
    bundle = _ready_bundle(tmp_path)
    _seed_live_evidence(bundle)

    # Make the fact-critical classifier say true, and the critic say grounded.
    # The wiring passes ONE factory to run_egress_critic_check (used for both
    # fact-critical and critic). A single fake returning both keys satisfies
    # both parsers.
    combo = _llm('{"fact_critical": true, "grounded": true, "relevant": true}')
    payload = {
        "messages": [{"role": "user", "content": "what did you find?"}],
        "_egressCriticModelFactory": combo,
    }
    status = await chat._maybe_run_egress_critic_gate(
        payload=payload,
        draft_text="grounded answer",
        gate1a_bundle=bundle,
    )
    assert status == "passed"


@pytest.mark.asyncio
async def test_gate_helper_no_evidence_returns_none(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path)  # no seeded evidence
    payload = {
        "messages": [{"role": "user", "content": "verify"}],
        "_egressCriticModelFactory": _llm('{"fact_critical": true, "grounded": true, "relevant": true}'),
    }
    status = await chat._maybe_run_egress_critic_gate(
        payload=payload,
        draft_text="answer",
        gate1a_bundle=bundle,
    )
    assert status is None  # no evidence activity -> not fact-critical


@pytest.mark.asyncio
async def test_gate_helper_never_raises_on_bad_bundle() -> None:
    status = await chat._maybe_run_egress_critic_gate(
        payload={"messages": []},
        draft_text="x",
        gate1a_bundle=object(),  # no host attr -> empty view -> not fact-critical
    )
    assert status is None


# ---------------------------------------------------------------------------
# OFF-state body identity + ON-state body emission
# ---------------------------------------------------------------------------


def _body(resp) -> dict:
    return json.loads(bytes(resp.body))


def test_response_body_identical_when_status_none(tmp_path) -> None:
    runtime = _runtime()
    off = chat._python_ready_response(
        runtime=runtime,
        content="hi",
        event_count=1,
        adk_invoked=True,
        runner_attempted=True,
        model_call_attempted=True,
        mocked_runner_invoked=False,
    )
    # Explicit None must produce the exact same body as the default.
    explicit_none = chat._python_ready_response(
        runtime=runtime,
        content="hi",
        event_count=1,
        adk_invoked=True,
        runner_attempted=True,
        model_call_attempted=True,
        mocked_runner_invoked=False,
        verifier_evidence_status=None,
    )
    assert bytes(off.body) == bytes(explicit_none.body)
    assert "verifierEvidenceStatus" not in _body(off)


def test_response_body_includes_status_when_set(tmp_path) -> None:
    runtime = _runtime()
    resp = chat._python_ready_response(
        runtime=runtime,
        content="hi",
        event_count=1,
        adk_invoked=True,
        runner_attempted=True,
        model_call_attempted=True,
        mocked_runner_invoked=False,
        verifier_evidence_status="missing_evidence",
    )
    assert _body(resp)["verifierEvidenceStatus"] == "missing_evidence"


def test_flag_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_EGRESS_GATE_ENABLED", raising=False)
    from magi_agent.config.env import is_egress_gate_enabled

    assert is_egress_gate_enabled() is False
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "1")
    assert is_egress_gate_enabled() is True
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "off")
    assert is_egress_gate_enabled() is False
