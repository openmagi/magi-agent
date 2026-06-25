"""PR-F-UX6 — end-to-end interview-mode flow through the HTTP route.

Walks the full architect loop:
  - underspecified input → ``mode="interview"`` + questions[]
  - subsequent turn with priorTurns → ``mode="proposal"`` (composed primitives)
  - flag OFF → legacy one-shot ``compile_with_review`` path unchanged

ZERO network. Reuses the FakeModel sequencing pattern from
``tests/test_rule_compile_route.py``.

PR-F-MUT3 (2026-06-24): adds mutator-shaped tests at the bottom — the
interview can now propose ``prompt_injection`` / ``output_rewrite``
primitives with ``trustClass: 'mutator'`` and a one-line ``description``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.customize.rule_compiler import (
    PROPOSAL_KINDS,
    PROPOSAL_TRUST_CLASSES,
    compile_interview_step,
    propose_primitive_or_hybrid,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


_TOKEN = "test-gateway-token"


def _runtime() -> OpenMagiRuntime:
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


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _factory_seq(*responses: str):
    call_idx = [0]

    def _factory() -> object:
        idx = call_idx[0]
        call_idx[0] += 1
        text = responses[idx] if idx < len(responses) else responses[-1]

        class _Model:
            model = "fake-interview-model"

            async def generate_content_async(
                self, req: Any, stream: bool = False
            ) -> AsyncGenerator:
                yield _FakeLlmResponse(text)

        return _Model()

    return _factory


# ---------------------------------------------------------------------------
# Canned responses — one interview turn, one resolved turn
# ---------------------------------------------------------------------------


_INTERVIEW_TURN_RESPONSE = json.dumps(
    {
        "whatToCheck": "audit AWS keys",
        "whereInLifecycle": "unknown",
        "whatToDoOnFail": "unknown",
        "openQuestions": [
            {
                "question": "Which tool's output should we scan?",
                "expects": "tool_name",
                "inventory": ["FileRead", "shell_exec"],
            }
        ],
        "confidence": 0.4,
    }
)


_RESOLVED_INTENT_RESPONSE = json.dumps(
    {
        "whatToCheck": "audit AWS keys in FileRead output",
        "whereInLifecycle": "after_tool_use",
        "whatToDoOnFail": "block",
        "openQuestions": [],
        "confidence": 0.9,
    }
)


_HYBRID_PROPOSAL_RESPONSE = json.dumps(
    {
        "mode": "hybrid",
        "primitives": [
            {
                "kind": "llm_criterion",
                "payload": {
                    "scope": "always",
                    "enabled": True,
                    "firesAt": "after_tool_use",
                    "action": "override",
                    "what": {
                        "kind": "llm_criterion",
                        "payload": {
                            "toolMatch": ["FileRead"],
                            "contentMatch": {
                                "pattern": "AKIA[0-9A-Z]{16}",
                                "isRegex": True,
                            },
                            "criterion": "Is this a real AWS key?",
                        },
                    },
                },
                "trustClass": "advisory",
                "rationale": "Regex pre-filter narrows critic invocation.",
            },
            {
                "kind": "custom_check",
                "payload": {
                    "id": "aws-audit",
                    "label": "AWS key audit",
                    "scope": "always",
                    "enabled": True,
                    "trigger": {
                        "tool": "FileRead",
                        "match": {
                            "pattern": "AKIA[0-9A-Z]{16}",
                            "isRegex": True,
                        },
                    },
                    "action": "audit",
                },
                "trustClass": "deterministic",
                "rationale": "Cheap pre-filter records an audit row.",
            },
        ],
        "summary": "Audit AWS keys: regex + LLM critic composed",
        "explanation": "Hybrid lets the deterministic filter narrow the critic.",
    }
)


_VALID_TOOL_PERM_JSON = json.dumps(
    {
        "routedKind": "tool_perm",
        "draft": {
            "scope": "always",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "block",
            "what": {
                "kind": "tool_perm",
                "payload": {"match": {"tool": "shell_exec"}, "decision": "deny"},
            },
        },
        "explanation": "Deny shell_exec before invocation.",
    }
)


_VALID_REVIEW_RESPONSE = (
    '{"verdict": "aligned", "issues": [], "confidence": 0.9}'
)


# ---------------------------------------------------------------------------
# compile_interview_step unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_step_returns_questions_for_underspecified_input() -> None:
    factory = _factory_seq(_INTERVIEW_TURN_RESPONSE)
    result = await compile_interview_step(
        "audit AWS keys",
        compiler_model_factory=factory,
        reviewer_model_factory=lambda: factory(),
    )
    assert result["ok"] is True
    assert result["mode"] == "interview"
    assert len(result["questions"]) == 1
    assert result["questions"][0]["expects"] == "tool_name"


@pytest.mark.asyncio
async def test_interview_step_returns_proposal_when_intent_resolved() -> None:
    # First call (discover_intent) returns resolved intent; second call
    # (propose_primitive_or_hybrid) returns the hybrid proposal.
    compile_factory = _factory_seq(_RESOLVED_INTENT_RESPONSE)
    propose_factory = _factory_seq(_HYBRID_PROPOSAL_RESPONSE)
    result = await compile_interview_step(
        "audit AWS keys",
        compiler_model_factory=compile_factory,
        reviewer_model_factory=propose_factory,
        force_interview=True,
    )
    assert result["ok"] is True, result
    assert result["mode"] == "proposal"
    assert result["proposal"]["mode"] == "hybrid"
    assert len(result["proposal"]["primitives"]) == 2


@pytest.mark.asyncio
async def test_interview_step_routes_well_formed_input_to_legacy() -> None:
    # Long enough that ``_looks_underspecified`` returns False → legacy path.
    well_formed = (
        "Deny the shell_exec tool whenever the agent attempts to invoke it "
        "without first emitting evidence:test-run on this coding turn."
    )
    compile_factory = _factory_seq(_VALID_TOOL_PERM_JSON)
    review_factory = _factory_seq(_VALID_REVIEW_RESPONSE)
    result = await compile_interview_step(
        well_formed,
        compiler_model_factory=compile_factory,
        reviewer_model_factory=review_factory,
    )
    assert result["mode"] == "compile"
    assert result.get("ok") is True
    assert result["routedKind"] == "tool_perm"


# ---------------------------------------------------------------------------
# HTTP route — flag OFF preserves legacy contract
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    runtime = _runtime()
    client = TestClient(create_app(runtime))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def test_route_flag_off_preserves_legacy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    monkeypatch.delenv("MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED", raising=False)

    factory = _factory_seq(_VALID_TOOL_PERM_JSON, _VALID_REVIEW_RESPONSE)
    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_nl_rule_compile_factory",
        lambda body: factory,
    )

    resp = _client().post(
        "/v1/app/customize/rules/compile",
        json={"nlText": "deny shell_exec"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Legacy success shape — no ``mode`` key.
    assert body["ok"] is True
    assert "mode" not in body
    assert body["routedKind"] == "tool_perm"


# ---------------------------------------------------------------------------
# HTTP route — flag ON, underspecified input → interview turn
# ---------------------------------------------------------------------------


def test_route_flag_on_returns_interview_questions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED", "1")

    factory = _factory_seq(_INTERVIEW_TURN_RESPONSE)
    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_nl_rule_compile_factory",
        lambda body: factory,
    )

    resp = _client().post(
        "/v1/app/customize/rules/compile",
        json={"nlText": "audit AWS keys"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "interview"
    assert isinstance(body["questions"], list) and body["questions"]
    assert body["questions"][0]["expects"] == "tool_name"
    assert body["intent"]["whatToCheck"] == "audit AWS keys"


# ---------------------------------------------------------------------------
# HTTP route — flag ON, resolved → proposal
# ---------------------------------------------------------------------------


def test_route_flag_on_returns_hybrid_proposal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED", "1")

    # First call → resolved intent; second call → hybrid proposal.
    factory = _factory_seq(_RESOLVED_INTENT_RESPONSE, _HYBRID_PROPOSAL_RESPONSE)
    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_nl_rule_compile_factory",
        lambda body: factory,
    )

    # Use ``mode=interview`` to force the interview path even for a
    # well-formed input — the test isolates the proposal-emission branch.
    resp = _client().post(
        "/v1/app/customize/rules/compile",
        json={
            "nlText": "audit AWS keys after FileRead",
            "mode": "interview",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "proposal"
    assert body["proposal"]["mode"] == "hybrid"
    assert len(body["proposal"]["primitives"]) == 2
    trust = {p["trustClass"] for p in body["proposal"]["primitives"]}
    assert trust == {"deterministic", "advisory"}


# ---------------------------------------------------------------------------
# PR-F-MUT3 — Mutator vocabulary, intent recognition, and proposal kinds
# ---------------------------------------------------------------------------


def test_proposal_trust_classes_includes_mutator() -> None:
    """The trust-class vocab MUST include ``mutator`` so the parser accepts
    prompt_injection / output_rewrite proposals — without this widening the
    architect could only express deterministic / advisory and would have to
    downgrade mutators to a lie."""

    assert "mutator" in PROPOSAL_TRUST_CLASSES
    # Existing buckets remain — additive only.
    assert "deterministic" in PROPOSAL_TRUST_CLASSES
    assert "advisory" in PROPOSAL_TRUST_CLASSES


def test_proposal_kinds_widens_routed_kinds_with_two_mutator_kinds() -> None:
    """The proposal-kind set widens the legacy ROUTED_KINDS set with the
    two mutator kinds (prompt_injection + output_rewrite) so the parser
    accepts mutator-shaped primitives end-to-end."""

    assert "prompt_injection" in PROPOSAL_KINDS
    assert "output_rewrite" in PROPOSAL_KINDS
    # The legacy 8 ROUTED_KINDS are still members — additive only.
    for kind in (
        "deterministic_ref",
        "tool_perm",
        "llm_criterion",
        "shacl_constraint",
        "seam_spec",
        "custom_check",
        "field_constraint",
        "capability_scope",
    ):
        assert kind in PROPOSAL_KINDS


def test_discover_intent_prompt_carries_mutator_recognition_vocab() -> None:
    """The Stage A system prompt MUST teach the model the 3 mutator verb
    families (redact/scrub/mask, inject/append/always-add, remind/tell) so
    it routes the right lifecycle + whatToDoOnFail. Asserts the prompt text
    directly so a future refactor cannot silently drop the vocab."""

    from magi_agent.customize.rule_compiler import (
        _DISCOVER_INTENT_SYSTEM_INSTRUCTION_TMPL,
    )

    prompt = _DISCOVER_INTENT_SYSTEM_INSTRUCTION_TMPL
    # The 3 mutator verb families.
    assert "redact" in prompt
    assert "scrub" in prompt
    assert "mask" in prompt
    assert "inject" in prompt
    assert "append" in prompt
    assert "always add" in prompt
    assert "remind" in prompt
    assert "tell the model" in prompt
    assert "add to context" in prompt
    # The three lifecycle hooks the architect should map them to.
    assert "after_tool_use" in prompt
    assert "before_tool_use" in prompt
    assert "on_user_prompt_submit" in prompt
    # The whatToDoOnFail vocabulary additions.
    assert "inject" in prompt
    assert "rewrite" in prompt
    assert "redact" in prompt
    # The honest-degrade warning — the architect must NOT silently downgrade
    # a mutator intent to audit / llm_criterion.
    assert "do not downgrade" in prompt.lower() or "not downgrade" in prompt.lower()


def test_propose_primitive_prompt_carries_the_two_mutator_kind_payload_shapes() -> None:
    """The Stage B proposal prompt MUST teach the model both mutator payload
    shapes (output_rewrite + prompt_injection tool-args + prompt_injection
    system-prompt) AND ship the two canonical examples from the spec so the
    proposed primitives are pre-flight valid against the backend validators."""

    from magi_agent.customize.rule_compiler import (
        _PROPOSE_PRIMITIVE_SYSTEM_INSTRUCTION_TMPL,
    )

    prompt = _PROPOSE_PRIMITIVE_SYSTEM_INSTRUCTION_TMPL
    # Mutator kind names appear in the kind enum.
    assert "prompt_injection" in prompt
    assert "output_rewrite" in prompt
    # Trust class enum carries the mutator bucket.
    assert "mutator" in prompt
    # output_rewrite canonical example (the spec sentence).
    assert "redact AKIA" in prompt
    assert "mode: 'redact'" in prompt
    assert "scope:" in prompt
    # prompt_injection canonical example (the spec sentence).
    assert "--dry-run" in prompt
    assert "shell_exec" in prompt
    assert "target_arg_key" in prompt
    assert "target: 'system_prompt'" in prompt
    # The "modifies traffic" honesty hint.
    assert "modifies traffic" in prompt.lower() or "Mutator badge" in prompt


# ---------------------------------------------------------------------------
# PR-F-MUT3 — propose_primitive_or_hybrid round-trip with mutator primitives
# ---------------------------------------------------------------------------


_OUTPUT_REWRITE_PROPOSAL_RESPONSE = json.dumps(
    {
        "mode": "single",
        "primitives": [
            {
                "kind": "output_rewrite",
                "payload": {
                    "mode": "redact",
                    "pattern": "AKIA[0-9A-Z]{16}",
                    "replacement": "***",
                    "scope": "match_only",
                    "isRegex": True,
                },
                "trustClass": "mutator",
                "rationale": "Redact AKIA-shaped keys in tool output before the model reads them.",
                "description": "Redacts AWS access-key-shaped patterns in tool output (match_only).",
            }
        ],
        "summary": "Redact AWS keys in tool output.",
        "explanation": "Mutator-only — no advisory critic needed; the pattern is unambiguous.",
    }
)


_PROMPT_INJECTION_PROPOSAL_RESPONSE = json.dumps(
    {
        "mode": "single",
        "primitives": [
            {
                "kind": "prompt_injection",
                "payload": {
                    "mode": "append",
                    "target_arg_key": "command",
                    "value": "--dry-run",
                    "condition": {"tool": "shell_exec"},
                    "toolMatch": {"include": ["shell_exec"]},
                },
                "trustClass": "mutator",
                "rationale": "Append the --dry-run flag to every shell_exec command.",
                "description": "Injects --dry-run into shell_exec command args.",
            }
        ],
        "summary": "Always inject --dry-run on shell_exec.",
        "explanation": "Mutator-only — the operator wants every shell call gated to dry-run.",
    }
)


@pytest.mark.asyncio
async def test_propose_emits_output_rewrite_mutator_for_redact_intent() -> None:
    """Resolved intent 'redact AKIA keys from tool output' → Stage B emits an
    output_rewrite primitive with trustClass=mutator and the spec-sentence
    payload shape (pattern + replacement + scope + isRegex)."""

    factory = _factory_seq(_OUTPUT_REWRITE_PROPOSAL_RESPONSE)
    intent = {
        "whatToCheck": "redact AKIA keys from tool output",
        "whereInLifecycle": "after_tool_use",
        "whatToDoOnFail": "redact",
        "openQuestions": [],
        "confidence": 0.95,
    }
    result = await propose_primitive_or_hybrid(intent, model_factory=factory)
    assert result["ok"] is True, result
    proposal = result["proposal"]
    assert proposal["mode"] == "single"
    assert len(proposal["primitives"]) == 1
    prim = proposal["primitives"][0]
    assert prim["kind"] == "output_rewrite"
    assert prim["trustClass"] == "mutator"
    # Payload shape must match the F-MUT2 validator contract.
    payload = prim["payload"]
    assert payload["mode"] == "redact"
    assert payload["pattern"] == "AKIA[0-9A-Z]{16}"
    assert payload["replacement"] == "***"
    assert payload["scope"] == "match_only"
    assert payload["isRegex"] is True
    # The one-line description rides through the parser.
    assert isinstance(prim["description"], str)
    assert "Redacts" in prim["description"]


@pytest.mark.asyncio
async def test_propose_emits_prompt_injection_mutator_for_inject_intent() -> None:
    """Resolved intent 'inject --dry-run on shell_exec' → Stage B emits a
    prompt_injection primitive with trustClass=mutator and the spec-sentence
    payload shape (mode=append + target_arg_key + value + condition.tool)."""

    factory = _factory_seq(_PROMPT_INJECTION_PROPOSAL_RESPONSE)
    intent = {
        "whatToCheck": "always inject --dry-run flag on shell_exec commands",
        "whereInLifecycle": "before_tool_use",
        "whatToDoOnFail": "inject",
        "openQuestions": [],
        "confidence": 0.95,
    }
    result = await propose_primitive_or_hybrid(intent, model_factory=factory)
    assert result["ok"] is True, result
    proposal = result["proposal"]
    assert proposal["mode"] == "single"
    assert len(proposal["primitives"]) == 1
    prim = proposal["primitives"][0]
    assert prim["kind"] == "prompt_injection"
    assert prim["trustClass"] == "mutator"
    payload = prim["payload"]
    assert payload["mode"] == "append"
    assert payload["target_arg_key"] == "command"
    assert payload["value"] == "--dry-run"
    assert payload["condition"]["tool"] == "shell_exec"
    # toolMatch.include filter (auto-derived from the tool name) rides
    # through the parser too.
    assert payload["toolMatch"]["include"] == ["shell_exec"]
    assert "shell_exec" in prim["description"]


@pytest.mark.asyncio
async def test_propose_rejects_invalid_trust_class() -> None:
    """Honest-degrade: if the model emits a trustClass outside the vocab
    (e.g. 'inject' as a trust class), the parser returns ``None`` and the
    orchestrator surfaces ok=False rather than silently activating a
    mis-typed mutator."""

    bad_response = json.dumps(
        {
            "mode": "single",
            "primitives": [
                {
                    "kind": "output_rewrite",
                    "payload": {
                        "mode": "redact",
                        "pattern": "x",
                        "replacement": "*",
                        "scope": "match_only",
                        "isRegex": False,
                    },
                    "trustClass": "inject",  # invalid — not in PROPOSAL_TRUST_CLASSES
                    "rationale": "bad",
                }
            ],
            "summary": "",
            "explanation": "",
        }
    )
    factory = _factory_seq(bad_response)
    intent = {
        "whatToCheck": "redact",
        "whereInLifecycle": "after_tool_use",
        "whatToDoOnFail": "redact",
        "openQuestions": [],
        "confidence": 0.9,
    }
    result = await propose_primitive_or_hybrid(intent, model_factory=factory)
    assert result["ok"] is False
    assert "unparseable" in result["error"]


# ---------------------------------------------------------------------------
# PR-F-EXEC3 — Operator-defined vocabulary, intent recognition, and proposal
# kinds for shell_command + shell_check.
# ---------------------------------------------------------------------------


def test_proposal_trust_classes_includes_operator_defined() -> None:
    """The trust-class vocab MUST include ``operator_defined`` so the parser
    accepts shell_command / shell_check proposals — without this widening the
    architect could only express deterministic / advisory / mutator and would
    have to downgrade operator-authored subprocess hooks to a lie."""

    assert "operator_defined" in PROPOSAL_TRUST_CLASSES
    # Existing buckets remain — additive only.
    assert "deterministic" in PROPOSAL_TRUST_CLASSES
    assert "advisory" in PROPOSAL_TRUST_CLASSES
    assert "mutator" in PROPOSAL_TRUST_CLASSES


def test_proposal_kinds_widens_with_two_shell_kinds() -> None:
    """The proposal-kind set widens with the two operator-defined shell kinds
    (shell_command + shell_check) so the parser accepts shell-shaped
    primitives end-to-end."""

    assert "shell_command" in PROPOSAL_KINDS
    assert "shell_check" in PROPOSAL_KINDS
    # The mutator + legacy ROUTED_KINDS remain members — additive only.
    assert "prompt_injection" in PROPOSAL_KINDS
    assert "output_rewrite" in PROPOSAL_KINDS
    for kind in (
        "deterministic_ref",
        "tool_perm",
        "llm_criterion",
        "shacl_constraint",
        "seam_spec",
        "custom_check",
        "field_constraint",
        "capability_scope",
    ):
        assert kind in PROPOSAL_KINDS


def test_discover_intent_prompt_carries_shell_recognition_vocab() -> None:
    """The Stage A system prompt MUST teach the model the operator-defined
    verb families ('run script' / 'execute command' / 'shell out' /
    'verify via shell' / 'check via exit code') and the honest-degrade
    warning that mutator / shell signals must not be downgraded to
    llm_criterion / audit. Asserts the prompt text directly so a future
    refactor cannot silently drop the vocab."""

    from magi_agent.customize.rule_compiler import (
        _DISCOVER_INTENT_SYSTEM_INSTRUCTION_TMPL,
    )

    prompt = _DISCOVER_INTENT_SYSTEM_INSTRUCTION_TMPL
    # Shell-command verb family (side-effect script).
    assert "run script" in prompt
    assert "execute command" in prompt
    assert "shell out" in prompt
    assert "shell hook" in prompt
    # Shell-check verb family (verifier verdict).
    assert "verify via shell" in prompt
    assert "check via exit code" in prompt or "exit code" in prompt
    # The whatToDoOnFail enum additions.
    assert "shell_run" in prompt
    assert "shell_verify" in prompt
    # Verifier slots called out for shell_check intent.
    assert "pre_final" in prompt
    assert "before_tool_use" in prompt
    # The honest-degrade warning — the architect must NOT silently
    # downgrade an operator-defined intent to llm_criterion / audit.
    # Both the mutator stanza ("do not downgrade") and the shell stanza
    # ("do not downgrade") fire the same phrase, so the assertion holds
    # for either source.
    assert (
        "do not downgrade" in prompt.lower() or "not downgrade" in prompt.lower()
    )
    # The trustClass framing the architect must use for shell primitives.
    assert "operator_defined" in prompt


def test_propose_primitive_prompt_carries_the_two_shell_kind_payload_shapes() -> None:
    """The Stage B proposal prompt MUST teach the model both shell payload
    shapes (shell_command file source + shell_check inline verifier) AND
    ship both canonical examples from the spec so the proposed primitives
    are pre-flight valid against the backend ShellPayload validator."""

    from magi_agent.customize.rule_compiler import (
        _PROPOSE_PRIMITIVE_SYSTEM_INSTRUCTION_TMPL,
    )

    prompt = _PROPOSE_PRIMITIVE_SYSTEM_INSTRUCTION_TMPL
    # Shell kind names appear in the kind enum.
    assert "shell_command" in prompt
    assert "shell_check" in prompt
    # Trust class enum carries the operator-defined bucket.
    assert "operator_defined" in prompt
    # ShellPayload shape — required fields the backend validator enforces.
    assert "timeout_seconds" in prompt
    assert "env_vars" in prompt
    assert "shell:" in prompt or "'bash'|'sh'" in prompt
    # shell_command canonical example (Notify Slack on tool error).
    assert "notify-slack" in prompt
    assert "after_tool_use" in prompt
    # shell_check canonical example (pytest gate at pre_final).
    assert "pytest" in prompt
    assert "passed" in prompt
    # The "magi does NOT verify the script" honesty hint.
    assert (
        "magi does NOT verify the script" in prompt
        or "Operator-defined badge" in prompt
    )


_SHELL_COMMAND_PROPOSAL_RESPONSE = json.dumps(
    {
        "mode": "single",
        "primitives": [
            {
                "kind": "shell_command",
                "payload": {
                    "source": "file",
                    "path": "/usr/local/bin/notify-slack.sh",
                    "timeout_seconds": 30,
                    "env_vars": ["SLACK_TOKEN"],
                    "shell": "bash",
                },
                "trustClass": "operator_defined",
                "rationale": "Notify the on-call Slack channel when any tool returns a non-zero exit code.",
                "description": "Runs notify-slack.sh on tool error (audit-only side effect).",
            }
        ],
        "summary": "Notify Slack on tool error.",
        "explanation": "Operator-defined side effect — no built-in primitive can dispatch to Slack without operator credentials, so a shell hook is the honest fit.",
    }
)


_SHELL_CHECK_PROPOSAL_RESPONSE = json.dumps(
    {
        "mode": "single",
        "primitives": [
            {
                "kind": "shell_check",
                "payload": {
                    "source": "inline",
                    "inline": 'pytest -q && echo "{\\"passed\\": true}" || echo "{\\"passed\\": false}"',
                    "timeout_seconds": 300,
                    "env_vars": [],
                    "shell": "bash",
                },
                "trustClass": "operator_defined",
                "rationale": "Gate the final answer on a real pytest run so the agent cannot finalize while tests fail.",
                "description": "Runs pytest at pre_final; exit 0 ⇒ allow final answer.",
            }
        ],
        "summary": "Run pytest before committing.",
        "explanation": "Operator-defined verifier — the operator wants the verdict to come from their own test runner, not from an LLM critic's judgment.",
    }
)


@pytest.mark.asyncio
async def test_propose_emits_shell_command_operator_defined_for_shell_run_intent() -> None:
    """Resolved intent 'run notify-slack.sh on tool error' → Stage B emits a
    shell_command primitive with trustClass=operator_defined and the
    spec-sentence payload shape (file source + script path + timeout +
    env-var allowlist + shell)."""

    factory = _factory_seq(_SHELL_COMMAND_PROPOSAL_RESPONSE)
    intent = {
        "whatToCheck": "run notify-slack.sh on tool error",
        "whereInLifecycle": "after_tool_use",
        "whatToDoOnFail": "shell_run",
        "openQuestions": [],
        "confidence": 0.95,
    }
    result = await propose_primitive_or_hybrid(intent, model_factory=factory)
    assert result["ok"] is True, result
    proposal = result["proposal"]
    assert proposal["mode"] == "single"
    assert len(proposal["primitives"]) == 1
    prim = proposal["primitives"][0]
    assert prim["kind"] == "shell_command"
    assert prim["trustClass"] == "operator_defined"
    payload = prim["payload"]
    assert payload["source"] == "file"
    assert payload["path"] == "/usr/local/bin/notify-slack.sh"
    assert payload["timeout_seconds"] == 30
    assert payload["env_vars"] == ["SLACK_TOKEN"]
    assert payload["shell"] == "bash"
    # The one-line description rides through the parser.
    assert isinstance(prim["description"], str)
    assert "notify-slack" in prim["description"]


@pytest.mark.asyncio
async def test_propose_emits_shell_check_operator_defined_for_shell_verify_intent() -> None:
    """Resolved intent 'exit 0 if tests pass' → Stage B emits a shell_check
    primitive with trustClass=operator_defined and the spec-sentence
    payload shape (inline body + bounded timeout + bash interpreter).
    Block honored at pre_final per the v1 _LEGAL matrix."""

    factory = _factory_seq(_SHELL_CHECK_PROPOSAL_RESPONSE)
    intent = {
        "whatToCheck": "exit 0 if pytest passes",
        "whereInLifecycle": "pre_final",
        "whatToDoOnFail": "shell_verify",
        "openQuestions": [],
        "confidence": 0.95,
    }
    result = await propose_primitive_or_hybrid(intent, model_factory=factory)
    assert result["ok"] is True, result
    proposal = result["proposal"]
    assert proposal["mode"] == "single"
    assert len(proposal["primitives"]) == 1
    prim = proposal["primitives"][0]
    assert prim["kind"] == "shell_check"
    assert prim["trustClass"] == "operator_defined"
    payload = prim["payload"]
    assert payload["source"] == "inline"
    assert "pytest" in payload["inline"]
    assert "passed" in payload["inline"]
    assert payload["timeout_seconds"] == 300
    assert payload["shell"] == "bash"
    assert "pytest" in prim["description"]


@pytest.mark.asyncio
async def test_propose_rejects_shell_primitive_with_invalid_trust_class() -> None:
    """Honest-degrade: a shell primitive labelled with the wrong trustClass
    (e.g. 'deterministic' to hide the external-script story) MUST be
    rejected by the parser. The architect cannot launder an operator-
    defined subprocess behind a built-in trust label."""

    bad_response = json.dumps(
        {
            "mode": "single",
            "primitives": [
                {
                    "kind": "shell_command",
                    "payload": {
                        "source": "inline",
                        "inline": "echo hi",
                        "timeout_seconds": 30,
                        "env_vars": [],
                        "shell": "bash",
                    },
                    "trustClass": "shell",  # invalid — not in PROPOSAL_TRUST_CLASSES
                    "rationale": "bad",
                }
            ],
            "summary": "",
            "explanation": "",
        }
    )
    factory = _factory_seq(bad_response)
    intent = {
        "whatToCheck": "run echo",
        "whereInLifecycle": "after_tool_use",
        "whatToDoOnFail": "shell_run",
        "openQuestions": [],
        "confidence": 0.9,
    }
    result = await propose_primitive_or_hybrid(intent, model_factory=factory)
    assert result["ok"] is False
    assert "unparseable" in result["error"]
