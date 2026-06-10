from __future__ import annotations

import errno
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.env import parse_runtime_env
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.shadow.gate2_recipe_profile_resolver import (
    resolve_gate2_recipe_profile,
)
from magi_agent.transport.chat import (
    build_gate2_sandbox_workspace_canary_config_from_env,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json_digest(value: object) -> str:
    return _digest(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _gate2_js_compatible_request_digest(body: dict[str, object]) -> str:
    canary = body.get("gate2Canary") if isinstance(body.get("gate2Canary"), dict) else {}
    content = canary.get("content") if isinstance(canary.get("content"), str) else ""
    relative_path = str(canary.get("relativePath") or "").strip()
    idempotency_key = str(canary.get("idempotencyKey") or "").strip()
    summary = {
        "gate": "gate2_sandbox_workspace_canary",
        "botId": "bot-gate2",
        "ownerUserId": "owner-gate2",
        "environment": "staging",
        "messages": [],
        "action": str(canary.get("action") or "").strip(),
        "relativePathDigest": _digest(relative_path) if relative_path else None,
        "contentDigest": _digest(content) if content else None,
        "idempotencyKeyDigest": _digest(idempotency_key) if idempotency_key else None,
        "patchDigest": None,
        "bodyDigest": _stable_json_digest(body),
    }
    return _stable_json_digest(summary)


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_ID": "bot-gate2",
        "USER_ID": "owner-gate2",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def _gate2_readiness_env() -> dict[str, str]:
    profile = resolve_gate2_recipe_profile("openmagi.gate2.workspace-canary.v1")
    return {
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS": "1",
        "CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST": _digest("bot-gate2"),
        "CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST": _digest(
            "owner-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT": "staging",
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST": "staging",
        "CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF": profile.profile_ref,
        "CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST": profile.profile_digest,
    }


def _gate2_activation_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_BOT_DIGEST": _digest(
            "bot-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": _digest(
            "owner-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENVIRONMENT": "staging",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENV_ALLOWLIST": "staging",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT": str(
            tmp_path / "gate2-sandboxes" / "gate2-sandbox"
        ),
    }
    env.update(overrides)
    return env


def _runtime(env: dict[str, str]) -> OpenMagiRuntime:
    runtime = OpenMagiRuntime(config=parse_runtime_env(env))
    runtime.gate2_sandbox_workspace_canary_config = (
        build_gate2_sandbox_workspace_canary_config_from_env(env, runtime.config)
    )
    return runtime


def _post_gate2(
    runtime: OpenMagiRuntime,
    body: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
    auto_digest_header: bool = True,
) -> tuple[int, dict[str, object]]:
    request_headers = dict(headers or {})
    if (
        auto_digest_header
        and body.get("gate") == "gate2_sandbox_workspace_canary"
        and "gate2Canary" in body
    ):
        request_headers.setdefault(
            "x-gate2-canary-request-digest",
            _gate2_js_compatible_request_digest(body),
        )
        request_headers.setdefault("x-gate2-canary-body-digest", _stable_json_digest(body))
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer gateway-token", **request_headers},
        json=body,
    )
    return response.status_code, response.json()


_PARENT_CREATE_DIAGNOSTIC_FIELDS = {
    "sandboxRootShapeKind",
    "rootSegmentCount",
    "approvedParentMatched",
    "safeNamespaceSegmentCount",
    "finalRootNameMatched",
    "parentCreateStage",
    "parentCreateDeniedReason",
    "componentRole",
    "componentIndex",
    "mkdirAttempted",
    "mkdirFailed",
    "openNoFollowFailed",
}


def _parent_create_diagnostics(result: object) -> dict[str, object]:
    diagnostics = getattr(result, "parent_create_diagnostics", None)
    assert diagnostics is not None
    data = diagnostics.model_dump(by_alias=True, mode="json")
    assert set(data) == _PARENT_CREATE_DIAGNOSTIC_FIELDS
    return data


def _assert_no_parent_create_raw_leak(encoded: str, tmp_path: Path) -> None:
    assert str(tmp_path) not in encoded
    assert "gate2-sandboxes" not in encoded
    assert "openmagi-gate2-sandboxes" not in encoded
    assert "gate2-sandbox" not in encoded
    assert "bot-gate2" not in encoded
    assert "run-a" not in encoded
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded


def test_gate2_sandbox_canary_is_default_off_even_when_chat_route_is_on(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = _runtime(_base_env(**_gate2_readiness_env()))

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/a.txt",
            },
        },
    )

    assert status == 503
    assert body["responseAuthority"] == "typescript"
    assert body["reason"] == "canary_gate_disabled"
    assert not (tmp_path / "gate2-sandboxes").exists()


def test_gate2_enabled_does_not_intercept_non_gate2_chat_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert status == 503
    assert body["responseAuthority"] == "typescript"
    assert body["reason"] == "canary_gate_disabled"
    assert "malformed_gate2_canary_request" not in json.dumps(body, sort_keys=True)


def test_gate2_selected_sandbox_canary_writes_readbacks_and_rolls_back_digest_only(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/gate2-note.txt",
                "content": "hello raw secret-looking token must not leak",
                "idempotencyKey": "gate2-loop-a",
            },
        },
    )

    encoded = json.dumps(body, sort_keys=True)
    assert status == 200
    assert body["gate"] == "gate2_sandbox_workspace_canary"
    assert body["status"] == "gate2_sandbox_workspace_canary_completed"
    assert body["responseAuthority"] == "typescript"
    assert body["routeDecision"] == "python_diagnostic_only"
    assert body["diagnosticOnly"] is True
    assert body["choices"][0]["message"]["content"] == "Sandbox workspace check completed."
    assert body["authority"]["userVisibleOutputAllowed"] is False
    assert body["authority"]["workspaceMutationAllowed"] is False
    assert body["authority"]["memoryWriteAllowed"] is False
    assert body["authority"]["channelWritesAllowed"] is False
    assert body["authority"]["dbWritesAllowed"] is False
    assert body["workspaceMutationReceipt"]["status"] == "simulated"
    assert body["workspaceMutationReceipt"]["receiptDigest"].startswith("sha256:")
    assert body["rollbackReceipt"]["rollbackDigest"].startswith("sha256:")
    assert body["rollbackReceipt"]["rollbackAction"] == "delete"
    assert body["rollbackReceipt"]["rollbackVerified"] is True
    assert body["rollbackReceipt"]["postRollbackDigest"] == body["beforeDigest"]
    assert body["beforeDigest"].startswith("sha256:")
    assert body["afterDigest"].startswith("sha256:")
    assert body["readbackDigest"] == body["afterDigest"]
    assert "gate2-note.txt" not in encoded
    assert "hello raw secret" not in encoded
    assert "token" not in encoded
    assert not (
        tmp_path
        / "gate2-sandboxes"
        / "gate2-sandbox"
        / "gate2-loop-a"
        / "src"
        / "gate2-note.txt"
    ).exists()


def test_gate2_selected_blocked_path_exposes_digest_safe_failure_chain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "../escape.txt",
                "content": "raw blocked content must not leak",
                "idempotencyKey": "blocked-path",
            },
        },
    )

    assert status == 409
    assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
    assert body["reason"] == "path_policy_denied"
    chain = body["gate2FailureChain"]
    assert chain == {
        "selectedProviderEnabled": True,
        "gateError": None,
        "requestDigestMatch": True,
        "bodyDigestMatch": True,
        "scopeMatch": {"bot": True, "owner": True, "environment": True},
        "sandboxRequestValidation": "passed",
        "sandboxResultStatus": "blocked",
        "sandboxResultReason": "path_policy_denied",
        "durableEvidenceStorePresent": True,
        "durableEvidenceRecordAttempted": False,
    }
    encoded_chain = json.dumps(chain, sort_keys=True)
    assert "../escape.txt" not in encoded_chain
    assert "raw blocked content" not in encoded_chain
    assert str(tmp_path) not in encoded_chain


def test_gate2_selected_parent_create_failure_chain_exposes_safe_root_diagnostics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)
    original_mkdir = gate2_module.os.mkdir

    def _raise_parent_create_error(
        path: str,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if path == "src":
            raise OSError("raw /Users/private token=secret must not leak")
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gate2_module.os, "mkdir", _raise_parent_create_error)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/env-wiring.txt",
                "content": "safe synthetic env wiring canary",
                "idempotencyKey": "parent-create-chain",
            },
        },
    )

    assert status == 409
    assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
    assert body["reason"] == "sandbox_write_parent_create_failed"
    assert body["responseAuthority"] == "typescript"
    assert body["routeDecision"] == "python_blocked"
    chain = body["gate2FailureChain"]
    assert _PARENT_CREATE_DIAGNOSTIC_FIELDS.issubset(chain)
    assert chain["sandboxRootShapeKind"] == "approved_direct_root"
    assert chain["approvedParentMatched"] is True
    assert chain["safeNamespaceSegmentCount"] == 0
    assert chain["finalRootNameMatched"] is True
    assert chain["parentCreateStage"] == "relative_parent_mkdir"
    assert chain["parentCreateDeniedReason"] == "mkdir_oserror"
    assert chain["componentRole"] == "loop_src"
    assert chain["componentIndex"] == 1
    assert chain["mkdirAttempted"] is True
    assert chain["mkdirFailed"] is True
    assert chain["openNoFollowFailed"] is False
    assert body["parentCreateDiagnostics"] == {
        key: chain[key] for key in _PARENT_CREATE_DIAGNOSTIC_FIELDS
    }
    encoded = json.dumps(body, sort_keys=True)
    _assert_no_parent_create_raw_leak(encoded, tmp_path)


def test_gate2_selected_digest_mismatch_exposes_digest_safe_failure_chain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)
    body = {
        "gate": "gate2_sandbox_workspace_canary",
        "gate2Canary": {
            "action": "FileCreate",
            "relativePath": "gate2-loop-a/src/digest-mismatch.txt",
            "content": "digest mismatch content must not leak",
            "idempotencyKey": "digest-mismatch",
        },
    }

    status, response_body = _post_gate2(
        runtime,
        body,
        headers={"x-gate2-canary-body-digest": _digest("wrong-body")},
    )

    assert status == 400
    assert response_body["status"] == "python_error"
    assert response_body["reason"] == "gate2_request_digest_mismatch"
    chain = response_body["gate2FailureChain"]
    assert chain == {
        "selectedProviderEnabled": True,
        "gateError": None,
        "requestDigestMatch": True,
        "bodyDigestMatch": False,
        "scopeMatch": {"bot": True, "owner": True, "environment": True},
        "sandboxRequestValidation": "not_attempted",
        "sandboxResultStatus": None,
        "sandboxResultReason": None,
        "durableEvidenceStorePresent": True,
        "durableEvidenceRecordAttempted": False,
    }
    encoded_chain = json.dumps(chain, sort_keys=True)
    assert "digest-mismatch.txt" not in encoded_chain
    assert "digest mismatch content" not in encoded_chain
    assert str(tmp_path) not in encoded_chain


def test_gate2_selected_python_exception_exposes_digest_safe_failure_chain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.transport import gate2_sandbox_canary as gate2_canary_mod

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    def _raise_private_exception(*args: object, **kwargs: object) -> object:
        raise RuntimeError("raw /Users/private token=secret must not leak")

    monkeypatch.setattr(
        gate2_canary_mod,
        "run_gate2_sandbox_workspace_canary",
        _raise_private_exception,
    )

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/env-wiring.txt",
                "content": "raw exception content must not leak",
                "idempotencyKey": "exception-path",
            },
        },
    )

    assert status == 503
    assert body["status"] == "gate2_sandbox_workspace_canary_failed"
    assert body["reason"] == "python_exception"
    assert body["responseAuthority"] == "typescript"
    assert body["routeDecision"] == "typescript_fallback"
    chain = body["gate2FailureChain"]
    request_digest = chain.pop("requestDigest")
    body_digest = chain.pop("bodyDigest")
    assert request_digest.startswith("sha256:")
    assert body_digest.startswith("sha256:")
    assert chain == {
        "selectedProviderEnabled": True,
        "gateError": "python_exception",
        "requestDigestMatch": True,
        "bodyDigestMatch": True,
        "scopeMatch": {"bot": True, "owner": True, "environment": True},
        "sandboxRequestValidation": "passed",
        "sandboxResultStatus": None,
        "sandboxResultReason": None,
        "durableEvidenceStorePresent": True,
        "durableEvidenceRecordAttempted": False,
        "exceptionStage": "sandbox_canary_execution",
        "exceptionClass": "RuntimeError",
    }
    encoded = json.dumps(body, sort_keys=True)
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded
    assert "raw exception content" not in encoded
    assert "env-wiring.txt" not in encoded
    assert str(tmp_path) not in encoded
    assert not (tmp_path / "gate2-sandboxes" / "gate2-sandbox").exists()


def test_gate2_selected_exception_chain_build_failure_returns_minimal_chain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.transport import gate2_sandbox_canary as gate2_canary_mod

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    def _raise_before_chain(*args: object, **kwargs: object) -> object:
        raise RuntimeError("raw /Users/private token=secret must not leak")

    monkeypatch.setattr(
        gate2_canary_mod,
        "_gate2_request_digest_status",
        _raise_before_chain,
    )
    monkeypatch.setattr(
        gate2_canary_mod,
        "_gate2_scope_match",
        _raise_before_chain,
    )

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/minimal-chain.txt",
                "content": "raw minimal chain content must not leak",
                "idempotencyKey": "minimal-chain",
            },
        },
    )

    assert status == 503
    assert body["status"] == "gate2_sandbox_workspace_canary_failed"
    assert body["reason"] == "python_exception"
    chain = body["gate2FailureChain"]
    assert chain["source"] == "python"
    assert chain["stage"] == "exception_handler"
    assert chain["exceptionStage"] == "gate2_handler"
    assert chain["chainBuildFailed"] is True
    assert chain["gateError"] == "python_exception"
    assert chain["exceptionClass"] == "RuntimeError"
    assert chain["requestDigest"].startswith("sha256:")
    assert chain["bodyDigest"].startswith("sha256:")
    encoded = json.dumps(body, sort_keys=True)
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded
    assert "minimal-chain.txt" not in encoded
    assert "raw minimal chain content" not in encoded
    assert str(tmp_path) not in encoded


def test_gate2_scope_mismatch_exposes_gate_error_failure_chain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_BOT_DIGEST=_digest(
                "other-bot"
            ),
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/scope-mismatch.txt",
                "content": "scope mismatch content must not leak",
                "idempotencyKey": "scope-mismatch",
            },
        },
    )

    assert status == 503
    assert body["status"] == "python_disabled"
    assert body["reason"] == "canary_gate_disabled"
    chain = body["gate2FailureChain"]
    assert chain == {
        "selectedProviderEnabled": True,
        "gateError": "python_disabled",
        "requestDigestMatch": None,
        "bodyDigestMatch": None,
        "scopeMatch": {"bot": False, "owner": True, "environment": True},
        "sandboxRequestValidation": "not_attempted",
        "sandboxResultStatus": None,
        "sandboxResultReason": None,
        "durableEvidenceStorePresent": True,
        "durableEvidenceRecordAttempted": False,
    }
    encoded_chain = json.dumps(chain, sort_keys=True)
    assert "scope-mismatch.txt" not in encoded_chain
    assert "scope mismatch content" not in encoded_chain
    assert "other-bot" not in encoded_chain
    assert str(tmp_path) not in encoded_chain


def test_gate2_sandbox_canary_requires_chat_proxy_digest_headers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/no-header.txt",
                "content": "must not write without relay digest",
                "idempotencyKey": "missing-digest-header",
            },
        },
        auto_digest_header=False,
    )

    encoded = json.dumps(body, sort_keys=True)
    assert status == 400
    assert body["responseAuthority"] == "typescript"
    assert body["reason"] == "gate2_request_digest_mismatch"
    assert "no-header.txt" not in encoded
    assert "must not write" not in encoded
    assert not (tmp_path / "gate2-sandboxes").exists()


def test_gate2_sandbox_canary_rejects_forged_request_digest_header(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/forged-digest.txt",
                "content": "must not write",
                "idempotencyKey": "forged-digest",
            },
        },
        headers={"x-gate2-canary-request-digest": _digest("forged")},
    )

    encoded = json.dumps(body, sort_keys=True)
    assert status == 400
    assert body["responseAuthority"] == "typescript"
    assert body["reason"] == "gate2_request_digest_mismatch"
    assert "forged-digest.txt" not in encoded
    assert "must not write" not in encoded
    assert not (tmp_path / "gate2-sandboxes").exists()


def test_gate2_sandbox_canary_accepts_js_digest_for_localized_body(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)
    body = {
        "gate": "gate2_sandbox_workspace_canary",
        "gate2Canary": {
            "action": "FileCreate",
            "relativePath": "gate2-loop-a/src/localized.txt",
            "content": "아까 말한 그거",
            "idempotencyKey": "localized-body",
        },
    }

    status, response_body = _post_gate2(
        runtime,
        body,
        headers={
            "x-gate2-canary-request-digest": _gate2_js_compatible_request_digest(body),
        },
    )

    encoded = json.dumps(response_body, sort_keys=True, ensure_ascii=False)
    assert status == 200
    assert response_body["status"] == "gate2_sandbox_workspace_canary_completed"
    assert "아까 말한 그거" not in encoded
    assert "localized.txt" not in encoded
    assert not (
        tmp_path
        / "gate2-sandboxes"
        / "gate2-sandbox"
        / "gate2-loop-a"
        / "src"
        / "localized.txt"
    ).exists()


def test_gate2_sandbox_canary_denies_forbidden_path_before_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = _runtime(_base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path)))

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileEdit",
                "relativePath": "../production.txt",
                "content": "must not write",
                "idempotencyKey": "bad-path",
            },
        },
    )

    encoded = json.dumps(body, sort_keys=True)
    assert status == 409
    assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
    assert body["responseAuthority"] == "typescript"
    assert body["workspaceMutationReceipt"]["status"] == "denied"
    assert body["workspaceMutationReceipt"]["deniedReason"] == "path_policy_denied"
    assert "production.txt" not in encoded
    assert "must not write" not in encoded
    assert not list((tmp_path / "gate2-sandboxes" / "gate2-sandbox").glob("**/*"))


def test_gate2_sandbox_canary_fails_closed_without_rollback_proof(tmp_path: Path) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-request"),
            action="PatchApply",
            relativePath="gate2-loop-a/src/example.py",
            content="safe",
            idempotencyKey="gate2-no-rollback",
        ),
        sandbox_root=tmp_path / "gate2-sandboxes" / "gate2-sandbox",
        require_rollback_proof=True,
        simulate_rollback_failure=True,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "rollback_not_proven"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert "src/example.py" not in encoded
    assert "safe" not in encoded


def test_gate2_loop_a_accepts_env_wiring_synthetic_path_without_broadening(
    tmp_path: Path,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-env-wiring-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-env-wiring",
        ),
        sandbox_root=tmp_path / "gate2-sandboxes" / "gate2-sandbox",
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "completed"
    assert result.reason == "sandbox_rollback_simulated"
    assert result.production_workspace_mutation_allowed is False
    assert result.write_mutation_authority_allowed is False
    assert result.tool_host_dispatch_allowed is False
    assert result.rollback_receipt is not None
    assert not (
        tmp_path
        / "gate2-sandboxes"
        / "gate2-sandbox"
        / "gate2-loop-a"
        / "src"
        / "env-wiring.txt"
    ).exists()
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded


def test_gate2_loop_a_creates_missing_safe_root_namespace_and_loop_dirs(
    tmp_path: Path,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = (
        tmp_path
        / "openmagi-gate2-sandboxes"
        / "bot-gate2"
        / "gate2-sandbox"
    )

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-approved-chain-missing-namespace"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-env-wiring",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "completed"
    assert result.reason == "sandbox_rollback_simulated"
    assert result.rollback_receipt is not None
    assert result.production_workspace_mutation_allowed is False
    assert result.write_mutation_authority_allowed is False
    assert result.tool_host_dispatch_allowed is False
    assert not (root / "gate2-loop-a" / "src" / "env-wiring.txt").exists()
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded


@pytest.mark.parametrize(
    "root_parts",
    [
        ("gate2-sandboxes", "gate2-sandbox"),
        ("gate2-sandboxes", "bot-gate2", "gate2-sandbox"),
        ("openmagi-gate2-sandboxes", "run-a", "gate2-sandbox"),
    ],
)
def test_gate2_loop_a_creates_missing_live_like_root_variants(
    tmp_path: Path,
    root_parts: tuple[str, ...],
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path.joinpath(*root_parts)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-live-like-root-" + "-".join(root_parts)),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-live-like-root",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "completed"
    assert result.reason == "sandbox_rollback_simulated"
    assert result.parent_create_diagnostics is None
    assert result.rollback_receipt is not None
    assert result.production_workspace_mutation_allowed is False
    assert result.write_mutation_authority_allowed is False
    assert result.tool_host_dispatch_allowed is False
    assert not (root / "gate2-loop-a" / "src" / "env-wiring.txt").exists()
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded


def test_gate2_loop_a_root_readiness_creates_approved_namespaced_root(
    tmp_path: Path,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        check_gate2_sandbox_root_readiness,
    )

    root = tmp_path / "gate2-sandboxes" / "bot-gate2" / "gate2-sandbox"

    readiness = check_gate2_sandbox_root_readiness(root)

    encoded = json.dumps(
        readiness.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
    )
    assert readiness.ready is True
    assert readiness.status == "ready"
    assert readiness.reason_codes == ("sandbox_root_ready",)
    assert readiness.parent_create_diagnostics is None
    assert root.is_dir()
    assert str(tmp_path) not in encoded
    assert "gate2-sandboxes" not in encoded
    assert "bot-gate2" not in encoded
    assert "gate2-sandbox" not in encoded


@pytest.mark.parametrize(
    ("error", "expected_denied_reason"),
    [
        (
            PermissionError(
                errno.EACCES,
                "raw /Users/private token=secret must not leak",
            ),
            "mkdir_permission_denied",
        ),
        (
            OSError(
                errno.EROFS,
                "raw /Users/private token=secret must not leak",
            ),
            "mkdir_read_only_filesystem",
        ),
    ],
)
def test_gate2_loop_a_root_readiness_fails_before_gate_on_sandbox_parent_mkdir(
    monkeypatch,
    tmp_path: Path,
    error: OSError,
    expected_denied_reason: str,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        check_gate2_sandbox_root_readiness,
    )

    original_mkdir = gate2_module.os.mkdir

    def _raise_sandbox_parent_mkdir_error(
        path: str,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if path == "gate2-sandboxes":
            raise error
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gate2_module.os, "mkdir", _raise_sandbox_parent_mkdir_error)
    root = tmp_path / "gate2-sandboxes" / "bot-gate2" / "gate2-sandbox"

    readiness = check_gate2_sandbox_root_readiness(root)

    encoded = json.dumps(
        readiness.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
    )
    assert readiness.ready is False
    assert readiness.status == "blocked"
    assert "sandbox_root_unavailable" in readiness.reason_codes
    assert "sandbox_write_parent_create_failed" in readiness.reason_codes
    assert expected_denied_reason in readiness.reason_codes
    diagnostics = readiness.parent_create_diagnostics
    assert diagnostics is not None
    data = diagnostics.model_dump(by_alias=True, mode="json")
    assert data["sandboxRootShapeKind"] == "approved_namespaced_root"
    assert data["componentRole"] == "sandbox_parent"
    assert data["parentCreateStage"] == "root_component_mkdir"
    assert data["parentCreateDeniedReason"] == expected_denied_reason
    assert data["mkdirAttempted"] is True
    assert data["mkdirFailed"] is True
    assert data["openNoFollowFailed"] is False
    assert not root.exists()
    _assert_no_parent_create_raw_leak(encoded, tmp_path)


def test_gate2_loop_a_rejects_broad_missing_root_namespace_chain(
    tmp_path: Path,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    anchor = tmp_path / "openmagi-gate2-sandboxes"
    root = anchor / "bot-gate2" / "run-a" / "gate2-sandbox"

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-broad-missing-root-chain"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-broad-root-chain",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_parent_create_failed"
    diagnostics = _parent_create_diagnostics(result)
    assert diagnostics["sandboxRootShapeKind"] == "broad_namespace_chain"
    assert diagnostics["approvedParentMatched"] is True
    assert diagnostics["safeNamespaceSegmentCount"] == 2
    assert diagnostics["finalRootNameMatched"] is True
    assert diagnostics["parentCreateStage"] == "root_shape_validation"
    assert diagnostics["parentCreateDeniedReason"] == "namespace_chain_too_broad"
    assert diagnostics["componentRole"] == "root_shape"
    assert diagnostics["componentIndex"] == 0
    assert diagnostics["mkdirAttempted"] is False
    assert diagnostics["mkdirFailed"] is False
    assert diagnostics["openNoFollowFailed"] is False
    assert result.rollback_receipt is None
    assert not (anchor / "bot-gate2").exists()
    _assert_no_parent_create_raw_leak(encoded, tmp_path)


def test_gate2_loop_a_rejects_long_namespace_chain_with_safe_diagnostics(
    tmp_path: Path,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path / "gate2-sandboxes"
    for index in range(17):
        root /= f"run-{index}"
    root /= "gate2-sandbox"

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-long-root-chain"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-long-root-chain",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_parent_create_failed"
    diagnostics = _parent_create_diagnostics(result)
    assert diagnostics["sandboxRootShapeKind"] == "broad_namespace_chain"
    assert diagnostics["safeNamespaceSegmentCount"] == 17
    assert diagnostics["parentCreateDeniedReason"] == "namespace_chain_too_broad"
    assert diagnostics["componentIndex"] == 0
    assert diagnostics["mkdirAttempted"] is False
    assert diagnostics["openNoFollowFailed"] is False
    _assert_no_parent_create_raw_leak(encoded, tmp_path)


def test_gate2_loop_a_rejects_nested_sandbox_parent_root_chain(
    tmp_path: Path,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    anchor = tmp_path / "openmagi-gate2-sandboxes"
    root = anchor / "bot-gate2" / "gate2-sandboxes" / "run-a" / "gate2-sandbox"

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-nested-sandbox-parent-root-chain"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-nested-parent-root-chain",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_parent_create_failed"
    diagnostics = _parent_create_diagnostics(result)
    assert diagnostics["sandboxRootShapeKind"] == "nested_sandbox_parent"
    assert diagnostics["approvedParentMatched"] is False
    assert diagnostics["finalRootNameMatched"] is True
    assert diagnostics["parentCreateStage"] == "root_shape_validation"
    assert diagnostics["parentCreateDeniedReason"] == "nested_sandbox_parent"
    assert diagnostics["componentRole"] == "root_shape"
    assert diagnostics["componentIndex"] == 0
    assert diagnostics["mkdirAttempted"] is False
    assert diagnostics["mkdirFailed"] is False
    assert diagnostics["openNoFollowFailed"] is False
    assert result.rollback_receipt is None
    assert not (anchor / "bot-gate2").exists()
    _assert_no_parent_create_raw_leak(encoded, tmp_path)


def test_gate2_loop_a_root_file_fails_closed_without_oserror(
    tmp_path: Path,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    root.parent.mkdir(parents=True)
    root.write_text("not a directory", encoding="utf-8")

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-root-file-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-root-file",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "path_policy_denied"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded


def test_gate2_loop_a_write_oserror_fails_closed_with_safe_reason(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    def _raise_write_error(fd: int, content: bytes) -> int:
        del fd, content
        raise OSError("raw /Users/private token=secret must not leak")

    monkeypatch.setattr(gate2_module.os, "write", _raise_write_error)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-write-oserror-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-write-error",
        ),
        sandbox_root=tmp_path / "gate2-sandboxes" / "gate2-sandbox",
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_flush_failed"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded


def test_gate2_loop_a_parent_create_oserror_reports_specific_safe_reason(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    original_mkdir = gate2_module.os.mkdir

    def _raise_parent_create_error(
        path: str,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if path == "src":
            raise OSError("raw /Users/private token=secret must not leak")
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gate2_module.os, "mkdir", _raise_parent_create_error)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-parent-create-oserror-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-parent-create-error",
        ),
        sandbox_root=tmp_path / "gate2-sandboxes" / "gate2-sandbox",
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_parent_create_failed"
    diagnostics = _parent_create_diagnostics(result)
    assert diagnostics["sandboxRootShapeKind"] == "approved_direct_root"
    assert diagnostics["rootSegmentCount"] == len(
        (tmp_path / "gate2-sandboxes" / "gate2-sandbox").parts
    )
    assert diagnostics["approvedParentMatched"] is True
    assert diagnostics["safeNamespaceSegmentCount"] == 0
    assert diagnostics["finalRootNameMatched"] is True
    assert diagnostics["parentCreateStage"] == "relative_parent_mkdir"
    assert diagnostics["parentCreateDeniedReason"] == "mkdir_oserror"
    assert diagnostics["componentRole"] == "loop_src"
    assert diagnostics["componentIndex"] == 1
    assert diagnostics["mkdirAttempted"] is True
    assert diagnostics["mkdirFailed"] is True
    assert diagnostics["openNoFollowFailed"] is False
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    _assert_no_parent_create_raw_leak(encoded, tmp_path)


def test_gate2_loop_a_open_oserror_reports_specific_safe_reason(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    original_open = gate2_module.os.open

    def _raise_open_error(path: str | Path, flags: int, *args: object, **kwargs: object):
        if path == "env-wiring.txt" and flags & gate2_module.os.O_CREAT:
            raise OSError("raw /Users/private token=secret must not leak")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(gate2_module.os, "open", _raise_open_error)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-open-oserror-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-open-error",
        ),
        sandbox_root=tmp_path / "gate2-sandboxes" / "gate2-sandbox",
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_open_failed"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded


def test_gate2_loop_a_flush_oserror_reports_specific_safe_reason(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    def _raise_fsync_error(fd: int) -> None:
        del fd
        raise OSError("raw /Users/private token=secret must not leak")

    monkeypatch.setattr(gate2_module.os, "fsync", _raise_fsync_error)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-flush-oserror-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-flush-error",
        ),
        sandbox_root=tmp_path / "gate2-sandboxes" / "gate2-sandbox",
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_flush_failed"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert not (
        tmp_path
        / "gate2-sandboxes"
        / "gate2-sandbox"
        / "gate2-loop-a"
        / "src"
        / "env-wiring.txt"
    ).exists()
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded


def test_gate2_loop_a_intermediate_file_reports_parent_create_failure(
    tmp_path: Path,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    root.mkdir(parents=True)
    (root / "gate2-loop-a").write_text("stale synthetic obstacle", encoding="utf-8")

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-intermediate-file-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-intermediate-file",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_parent_create_failed"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert (root / "gate2-loop-a").read_text(encoding="utf-8") == (
        "stale synthetic obstacle"
    )
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded
    assert "stale synthetic obstacle" not in encoded


@pytest.mark.parametrize(
    ("obstacle_role", "expected_component_role"),
    [
        ("sandbox_parent", "sandbox_parent"),
        ("safe_namespace", "safe_namespace"),
    ],
)
def test_gate2_loop_a_root_parent_regular_file_reports_safe_component_role(
    tmp_path: Path,
    obstacle_role: str,
    expected_component_role: str,
) -> None:
    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    anchor = tmp_path / "gate2-sandboxes"
    root = anchor / "bot-gate2" / "gate2-sandbox"
    if obstacle_role == "sandbox_parent":
        anchor.write_text(
            "raw /Users/private token=secret must not leak",
            encoding="utf-8",
        )
    else:
        anchor.mkdir()
        (anchor / "bot-gate2").write_text(
            "raw /Users/private token=secret must not leak",
            encoding="utf-8",
        )

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-root-file-obstacle-" + obstacle_role),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-root-file-obstacle",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_read_failed"
    diagnostics = _parent_create_diagnostics(result)
    assert diagnostics["sandboxRootShapeKind"] == "approved_namespaced_root"
    assert diagnostics["approvedParentMatched"] is True
    assert diagnostics["safeNamespaceSegmentCount"] == 1
    assert diagnostics["finalRootNameMatched"] is True
    assert diagnostics["parentCreateStage"] == "root_component_open"
    assert diagnostics["parentCreateDeniedReason"] == "open_nofollow_failed"
    assert diagnostics["componentRole"] == expected_component_role
    assert isinstance(diagnostics["componentIndex"], int)
    assert diagnostics["mkdirAttempted"] is False
    assert diagnostics["mkdirFailed"] is False
    assert diagnostics["openNoFollowFailed"] is True
    _assert_no_parent_create_raw_leak(encoded, tmp_path)


def test_gate2_loop_a_parent_symlink_swap_before_open_fails_closed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_open = gate2_module.os.open
    swapped = False

    def _swap_src_to_symlink_before_open(
        path: str | Path,
        flags: int,
        *args: object,
        **kwargs: object,
    ):
        nonlocal swapped
        if path == "src" and not swapped:
            src = root / "gate2-loop-a" / "src"
            if src.is_dir():
                src.rmdir()
            src.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(gate2_module.os, "open", _swap_src_to_symlink_before_open)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-parent-symlink-swap-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-parent-symlink-swap",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert swapped is True
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_parent_create_failed"
    diagnostics = _parent_create_diagnostics(result)
    assert diagnostics["sandboxRootShapeKind"] == "approved_direct_root"
    assert diagnostics["parentCreateStage"] == "relative_parent_open"
    assert diagnostics["parentCreateDeniedReason"] == "open_nofollow_failed"
    assert diagnostics["componentRole"] == "loop_src"
    assert diagnostics["componentIndex"] == 1
    assert diagnostics["mkdirAttempted"] is True
    assert diagnostics["mkdirFailed"] is False
    assert diagnostics["openNoFollowFailed"] is True
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert not (outside / "env-wiring.txt").exists()
    assert not (outside / "src" / "env-wiring.txt").exists()
    _assert_no_parent_create_raw_leak(encoded, tmp_path)


def test_gate2_loop_a_readback_oserror_reports_specific_reason_without_path_content(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    def _raise_readback_error(fd: int, size: int) -> bytes:
        del fd, size
        raise OSError("raw /Users/private token=secret must not leak")

    monkeypatch.setattr(gate2_module.os, "read", _raise_readback_error)
    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    target = root / "gate2-loop-a" / "src" / "env-wiring.txt"

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-readback-oserror-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="replacement synthetic content",
            idempotencyKey="gate2-loop-a-readback-error",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_readback_failed"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert not target.exists()
    assert "env-wiring.txt" not in encoded
    assert "replacement synthetic content" not in encoded
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded


def test_gate2_loop_a_unlink_before_write_oserror_fails_closed_without_path_content(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    original_unlink = gate2_module.os.unlink
    target = (
        tmp_path
        / "gate2-sandboxes"
        / "gate2-sandbox"
        / "gate2-loop-a"
        / "src"
        / "env-wiring.txt"
    )

    def _raise_unlink_error(path: str | Path, *args: object, **kwargs: object) -> None:
        if path == "env-wiring.txt":
            raise OSError("raw /Users/private token=secret must not leak")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(gate2_module.os, "unlink", _raise_unlink_error)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-rollback-oserror-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-rollback-error",
        ),
        sandbox_root=tmp_path / "gate2-sandboxes" / "gate2-sandbox",
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_open_failed"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert target.exists()
    assert target.read_bytes() == b""
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded


def test_gate2_loop_a_hardlink_race_before_unlink_fails_before_content_write(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_link = outside / "env-wiring-hardlink.txt"
    original_unlink = gate2_module.os.unlink

    def _link_before_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        if path == "env-wiring.txt" and not outside_link.exists():
            outside_link.hardlink_to(
                root / "gate2-loop-a" / "src" / "env-wiring.txt"
            )
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(gate2_module.os, "unlink", _link_before_unlink)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-rollback-hardlink-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-rollback-hardlink",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_open_failed"
    assert result.rollback_receipt is None
    assert result.production_workspace_mutation_allowed is False
    assert outside_link.exists()
    assert outside_link.read_bytes() == b""
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded
    assert str(outside) not in encoded


def test_gate2_loop_a_open_fd_race_observes_public_payload_only(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    original_open = gate2_module.os.open
    original_unlink = gate2_module.os.unlink
    leaked_fd: int | None = None

    def _open_fd_before_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        nonlocal leaked_fd
        if path == "env-wiring.txt" and leaked_fd is None:
            leaked_fd = original_open(
                path,
                gate2_module.os.O_RDONLY,
                dir_fd=kwargs.get("dir_fd"),
            )
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(gate2_module.os, "unlink", _open_fd_before_unlink)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-open-fd-race-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary token=secret",
            idempotencyKey="gate2-loop-a-open-fd-race",
        ),
        sandbox_root=root,
    )

    assert leaked_fd is not None
    try:
        gate2_module.os.lseek(leaked_fd, 0, gate2_module.os.SEEK_SET)
        leaked_bytes = gate2_module.os.read(leaked_fd, 1024)
    finally:
        gate2_module.os.close(leaked_fd)
    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert result.status == "completed"
    assert result.rollback_receipt is not None
    assert leaked_bytes == b"gate2.sandboxWorkspaceCanary.v1\n"
    assert b"safe synthetic env wiring" not in leaked_bytes
    assert b"token=secret" not in leaked_bytes
    assert not (root / "gate2-loop-a" / "src" / "env-wiring.txt").exists()
    assert "safe synthetic env wiring" not in encoded
    assert "token=secret" not in encoded


def test_gate2_loop_a_sandbox_root_parent_symlink_swap_fails_closed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import shutil

    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_mkdir = gate2_module.os.mkdir
    swapped = False

    def _swap_parent_after_create(
        path: str,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        result = original_mkdir(path, mode, dir_fd=dir_fd)
        if path == "gate2-sandboxes" and not swapped:
            shutil.rmtree(tmp_path / "gate2-sandboxes")
            (tmp_path / "gate2-sandboxes").symlink_to(
                outside,
                target_is_directory=True,
            )
            swapped = True
        return result

    monkeypatch.setattr(gate2_module.os, "mkdir", _swap_parent_after_create)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-root-parent-symlink-swap-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-root-parent-symlink-swap",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert swapped is True
    assert result.status == "blocked"
    assert result.reason == "sandbox_write_parent_create_failed"
    assert result.rollback_receipt is None
    assert not (outside / "gate2-sandbox").exists()
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded


def test_gate2_loop_a_sandbox_root_parent_symlink_after_validation_fails_closed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import magi_agent.shadow.gate2_activation_loop_a as gate2_module

    from magi_agent.shadow.gate2_activation_loop_a import (
        Gate2SandboxCanaryRequest,
        run_gate2_sandbox_workspace_canary,
    )

    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_safe_root = gate2_module._safe_sandbox_root
    swapped = False

    def _swap_parent_after_validation(path: Path) -> Path | None:
        nonlocal swapped
        resolved = original_safe_root(path)
        if resolved is not None and not swapped:
            root.parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return resolved

    monkeypatch.setattr(gate2_module, "_safe_sandbox_root", _swap_parent_after_validation)

    result = run_gate2_sandbox_workspace_canary(
        Gate2SandboxCanaryRequest(
            requestDigest=_digest("gate2-root-parent-post-validation-swap-request"),
            action="FileCreate",
            relativePath="gate2-loop-a/src/env-wiring.txt",
            content="safe synthetic env wiring canary",
            idempotencyKey="gate2-loop-a-root-parent-post-validation-swap",
        ),
        sandbox_root=root,
    )

    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert swapped is True
    assert result.status == "blocked"
    assert result.reason == "sandbox_read_failed"
    assert result.rollback_receipt is None
    assert not (outside / "gate2-sandbox").exists()
    assert "env-wiring.txt" not in encoded
    assert "safe synthetic env wiring" not in encoded


def test_gate2_sandbox_canary_rejects_unsafe_root_before_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT=str(tmp_path / "workspace"),
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/unsafe-root.txt",
                "content": "must not write",
            },
        },
    )

    assert status == 409
    assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
    assert body["workspaceMutationReceipt"]["status"] == "denied"
    assert body["workspaceMutationReceipt"]["deniedReason"] == "path_policy_denied"
    assert not (tmp_path / "workspace").exists()


def test_gate2_sandbox_canary_rejects_protected_parent_root_before_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT=str(
                tmp_path / "memory" / "gate2-sandbox"
            ),
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/protected-root.txt",
                "content": "must not write",
            },
        },
    )

    encoded = json.dumps(body, sort_keys=True)
    assert status == 409
    assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
    assert body["workspaceMutationReceipt"]["status"] == "denied"
    assert body["workspaceMutationReceipt"]["deniedReason"] == "path_policy_denied"
    assert "protected-root.txt" not in encoded
    assert "must not write" not in encoded
    assert not (tmp_path / "memory" / "gate2-sandbox").exists()


def test_gate2_sandbox_canary_rejects_workspace_parent_root_before_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT=str(
                tmp_path / "workspace" / "gate2-sandboxes" / "gate2-sandbox"
            ),
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/workspace-root.txt",
                "content": "must not write",
            },
        },
    )

    encoded = json.dumps(body, sort_keys=True)
    assert status == 409
    assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
    assert body["workspaceMutationReceipt"]["status"] == "denied"
    assert body["workspaceMutationReceipt"]["deniedReason"] == "path_policy_denied"
    assert "workspace-root.txt" not in encoded
    assert "must not write" not in encoded
    assert not (tmp_path / "workspace" / "gate2-sandboxes" / "gate2-sandbox").exists()


def test_gate2_sandbox_canary_rejects_workspace_named_and_private_roots(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    for root in (
        tmp_path / "prod-workspace" / "gate2-sandboxes" / "gate2-sandbox",
        tmp_path / "private" / "gate2-sandboxes" / "gate2-sandbox",
    ):
        env = _base_env(
            **_gate2_readiness_env(),
            **_gate2_activation_env(
                tmp_path,
                CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT=str(root),
            ),
        )
        runtime = _runtime(env)

        status, body = _post_gate2(
            runtime,
            {
                "gate": "gate2_sandbox_workspace_canary",
                "gate2Canary": {
                    "action": "FileCreate",
                    "relativePath": "gate2-loop-a/src/rejected-root.txt",
                    "content": "must not write",
                },
            },
        )

        encoded = json.dumps(body, sort_keys=True)
        assert status == 409
        assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
        assert body["workspaceMutationReceipt"]["status"] == "denied"
        assert body["workspaceMutationReceipt"]["deniedReason"] == "path_policy_denied"
        assert "rejected-root.txt" not in encoded
        assert "must not write" not in encoded
        assert not root.exists()


def test_gate2_sandbox_canary_rejects_symlink_escape_before_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)
    root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    outside = tmp_path / "outside-target"
    outside.mkdir()
    root.mkdir(parents=True)
    (root / "gate2-loop-a").symlink_to(outside, target_is_directory=True)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/symlink-escape.txt",
                "content": "must not write",
            },
        },
    )

    encoded = json.dumps(body, sort_keys=True)
    assert status == 409
    assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
    assert body["workspaceMutationReceipt"]["status"] == "denied"
    assert body["workspaceMutationReceipt"]["deniedReason"] == "path_policy_denied"
    assert "symlink-escape.txt" not in encoded
    assert "must not write" not in encoded
    assert not (outside / "src" / "symlink-escape.txt").exists()


def test_gate2_sandbox_canary_rejects_sealed_and_private_paths_before_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = _runtime(_base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path)))

    for relative_path in (
        "gate2-loop-a/AGENTS.md",
        "gate2-loop-a/tools.md",
        "gate2-loop-a/memory/private.md",
        "gate2-loop-a/private/file.txt",
        "src/missing-prefix.txt",
    ):
        status, body = _post_gate2(
            runtime,
            {
                "gate": "gate2_sandbox_workspace_canary",
                "gate2Canary": {
                    "action": "FileEdit",
                    "relativePath": relative_path,
                    "content": "must not write",
                    "idempotencyKey": relative_path.replace("/", "-"),
                },
            },
        )

        encoded = json.dumps(body, sort_keys=True)
        assert status == 409
        assert body["workspaceMutationReceipt"]["status"] == "denied"
        assert body["workspaceMutationReceipt"]["deniedReason"] == "path_policy_denied"
        assert relative_path not in encoded

    assert not list((tmp_path / "gate2-sandboxes" / "gate2-sandbox").glob("**/*"))


def test_gate2_delivery_receipt_persists_digest_only_sandbox_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)
    runtime.gate5b4c3_shadow_generation_route_config = (
        Gate5B4C3ShadowGenerationRouteConfig(
            counterStore=Gate5B4C3ShadowCounterStore(counter_path),
        )
    )

    status, canary = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "PatchApply",
                "relativePath": "gate2-loop-a/src/receipt.txt",
                "content": "receipt content must not leak",
                "idempotencyKey": "receipt-case",
            },
        },
    )
    assert status == 200

    receipt = TestClient(create_app(runtime)).post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": canary["requestDigest"],
            "bodyDigest": _digest("gate2-body"),
            "routeDecision": "python_selected_gate2_sandbox",
            "gate": "gate2_sandbox_workspace_canary",
            "deliveryStatus": "served_to_client",
            "reason": "served_to_client",
            "responseAuthority": "python",
            "servedAt": "2026-05-27T22:00:00.000Z",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": True,
            "selectedScope": {
                "selectedBotDigest": _digest("bot-gate2"),
                "selectedOwnerUserIdDigest": _digest("owner-gate2"),
                "environment": "staging",
            },
            "workspaceMutationReceiptDigest": canary["workspaceMutationReceipt"][
                "receiptDigest"
            ],
            "rollbackReceiptDigest": canary["rollbackReceipt"]["rollbackDigest"],
            "sandboxPathDigest": canary["sandboxPathDigest"],
        },
    )

    assert receipt.status_code == 202
    assert receipt.json()["status"] == "receipt_recorded"
    raw = json.loads(counter_path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    record = scope["requests"][canary["requestDigest"]]
    assert record["deliveryStatus"] == "served_to_client"
    assert record["routeDecision"] == "python_selected_gate2_sandbox"
    assert record["responseAuthority"] == "python"
    assert record["gate"] == "gate2_sandbox_workspace_canary"
    assert record["workspaceMutationReceiptDigest"] == canary["workspaceMutationReceipt"][
        "receiptDigest"
    ]
    assert record["rollbackReceiptDigest"] == canary["rollbackReceipt"]["rollbackDigest"]
    assert record["sandboxPathDigest"] == canary["sandboxPathDigest"]
    encoded = json.dumps(record, sort_keys=True)
    assert "receipt.txt" not in encoded
    assert "receipt content" not in encoded


def test_gate2_selected_delivery_receipt_accepts_selected_counter_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)
    runtime.gate5b4c3_shadow_generation_route_config = (
        Gate5B4C3ShadowGenerationRouteConfig(
            counterStore=Gate5B4C3ShadowCounterStore(counter_path),
        )
    )

    status, canary = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/selected-receipt.txt",
                "content": "selected receipt content must not leak",
                "idempotencyKey": "selected-receipt-case",
            },
        },
    )
    assert status == 200
    assert canary["status"] == "gate2_selected_sandbox_canary_completed"
    assert canary["responseAuthority"] == "python"
    assert canary["counter"]["pythonCounterRecordPresent"] is True

    receipt = TestClient(create_app(runtime)).post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": canary["requestDigest"],
            "bodyDigest": _digest("gate2-selected-body"),
            "routeDecision": "python_selected_gate2_sandbox",
            "gate": "gate2_sandbox_workspace_canary",
            "deliveryStatus": "served_to_client",
            "reason": "served_to_client",
            "responseAuthority": "python",
            "servedAt": "2026-05-31T17:00:00.000Z",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": True,
            "selectedScope": {
                "selectedBotDigest": _digest("bot-gate2"),
                "selectedOwnerUserIdDigest": _digest("owner-gate2"),
                "environment": "staging",
            },
            "workspaceMutationReceiptDigest": canary["workspaceMutationReceipt"][
                "receiptDigest"
            ],
            "rollbackReceiptDigest": canary["rollbackReceipt"]["rollbackDigest"],
            "sandboxPathDigest": canary["sandboxPathDigest"],
        },
    )

    assert receipt.status_code == 202
    assert receipt.json()["status"] == "receipt_recorded"
    assert receipt.json()["counter"]["pythonCounterRecordPresent"] is True
    raw = json.loads(counter_path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    assert canary["requestDigest"] in scope["requests"]
    record = scope["requests"][canary["requestDigest"]]
    assert record["status"] == "gate2_selected_sandbox_canary_completed"
    assert record["deliveryStatus"] == "served_to_client"
    assert record["responseAuthority"] == "python"
    encoded = json.dumps(record, sort_keys=True)
    assert "selected-receipt.txt" not in encoded
    assert "selected receipt content" not in encoded


def test_gate2_delivery_receipt_rejects_forged_unbacked_sandbox_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)
    runtime.gate5b4c3_shadow_generation_route_config = (
        Gate5B4C3ShadowGenerationRouteConfig(
            counterStore=Gate5B4C3ShadowCounterStore(counter_path),
        )
    )
    request_digest = _digest("unbacked-gate2-request")

    receipt = TestClient(create_app(runtime)).post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"Authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": request_digest,
            "bodyDigest": _digest("gate2-body"),
            "routeDecision": "python_selected_gate2_sandbox",
            "gate": "gate2_sandbox_workspace_canary",
            "deliveryStatus": "served_to_client",
            "reason": "served_to_client",
            "responseAuthority": "python",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": True,
            "selectedScope": {
                "selectedBotDigest": _digest("bot-gate2"),
                "selectedOwnerUserIdDigest": _digest("owner-gate2"),
                "environment": "staging",
            },
            "workspaceMutationReceiptDigest": _digest("forged-workspace-mutation"),
            "rollbackReceiptDigest": _digest("forged-rollback"),
            "sandboxPathDigest": _digest("forged-path"),
        },
    )

    assert receipt.status_code == 409
    assert receipt.json()["status"] == "receipt_rejected"
    assert receipt.json()["receiptStatus"] == "python_counter_record_required"
    assert receipt.json()["reason"] == "python_counter_record_required"
    assert receipt.json()["counter"]["pythonCounterRecordPresent"] is True


def test_gate2_delivery_receipt_identifies_evidence_mismatch_publicly(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)
    runtime.gate5b4c3_shadow_generation_route_config = (
        Gate5B4C3ShadowGenerationRouteConfig(
            counterStore=Gate5B4C3ShadowCounterStore(counter_path),
        )
    )

    status, canary = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/mismatch-receipt.txt",
                "content": "mismatch content must not leak",
                "idempotencyKey": "mismatch-receipt-case",
            },
        },
    )
    assert status == 200

    receipt = TestClient(create_app(runtime)).post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": canary["requestDigest"],
            "bodyDigest": _digest("gate2-mismatch-body"),
            "routeDecision": "python_selected_gate2_sandbox",
            "gate": "gate2_sandbox_workspace_canary",
            "deliveryStatus": "served_to_client",
            "reason": "served_to_client",
            "responseAuthority": "python",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": True,
            "selectedScope": {
                "selectedBotDigest": _digest("bot-gate2"),
                "selectedOwnerUserIdDigest": _digest("owner-gate2"),
                "environment": "staging",
            },
            "workspaceMutationReceiptDigest": _digest("wrong-mutation"),
            "rollbackReceiptDigest": canary["rollbackReceipt"]["rollbackDigest"],
            "sandboxPathDigest": canary["sandboxPathDigest"],
        },
    )

    assert receipt.status_code == 409
    body = receipt.json()
    assert body["status"] == "receipt_rejected"
    assert body["receiptStatus"] == "evidence_error"
    assert body["reason"] == "gate2_evidence_mismatch"
    assert body["counter"]["pythonCounterRecordPresent"] is True
    encoded = json.dumps(body, sort_keys=True)
    assert "mismatch-receipt.txt" not in encoded
    assert "mismatch content" not in encoded


def test_gate2_delivery_receipt_identifies_counter_store_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)

    receipt = TestClient(create_app(runtime)).post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": _digest("missing-counter-store"),
            "bodyDigest": _digest("gate2-body"),
            "routeDecision": "python_selected_gate2_sandbox",
            "gate": "gate2_sandbox_workspace_canary",
            "deliveryStatus": "served_to_client",
            "reason": "served_to_client",
            "responseAuthority": "python",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": True,
            "selectedScope": {
                "selectedBotDigest": _digest("bot-gate2"),
                "selectedOwnerUserIdDigest": _digest("owner-gate2"),
                "environment": "staging",
            },
            "workspaceMutationReceiptDigest": _digest("workspace-mutation"),
            "rollbackReceiptDigest": _digest("rollback"),
            "sandboxPathDigest": _digest("sandbox-path"),
        },
    )

    assert receipt.status_code == 503
    body = receipt.json()
    assert body["reason"] == "counter_store_unavailable"
    assert body["responseAuthority"] == "typescript"


def test_gate2_diagnostic_response_has_stable_route_and_authority_fields_for_relay(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Completed Gate 2 sandbox canaries without selected provider emit
    diagnostic-only route fields (typescript authority, python_diagnostic_only).

    The operation remains diagnostic-only for real workspace authority and
    defers to TypeScript for user-visible handling.
    """
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(**_gate2_readiness_env(), **_gate2_activation_env(tmp_path))
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/relay-contract.txt",
                "content": "relay contract verification content",
                "idempotencyKey": "gate2-loop-a",
            },
        },
    )

    assert status == 200
    # Diagnostic route fields — selected provider not enabled
    assert body["responseAuthority"] == "typescript"
    assert body["routeDecision"] == "python_diagnostic_only"
    assert body["diagnosticOnly"] is True
    assert body["localOnly"] is True
    assert body["fakeOnly"] is True
    assert body["gate"] == "gate2_sandbox_workspace_canary"
    assert body["status"] == "gate2_sandbox_workspace_canary_completed"
    # productionWorkspaceMutationAllowed must always be false
    assert body["productionWorkspaceMutationAllowed"] is False
    assert body["writeMutationAuthorityAllowed"] is False
    assert body["toolHostDispatchAllowed"] is False
    # Authority block must have all fields set to false
    authority = body["authority"]
    for key in [
        "userVisibleOutputAllowed",
        "canaryRoutingAllowed",
        "toolDispatchAllowed",
        "readOnlyToolDispatchAllowed",
        "writeMutationAuthorityAllowed",
        "workspaceMutationAllowed",
        "memoryWriteAllowed",
        "browserWebNetworkAllowed",
        "channelWritesAllowed",
        "dbWritesAllowed",
        "transcriptWritesAllowed",
        "sseWritesAllowed",
        "childExecutionAllowed",
        "missionRuntimeAllowed",
        "schedulerMutationAllowed",
        "evidenceBlockModeAllowed",
    ]:
        assert authority[key] is False, f"authority.{key} must be False"
    # Receipt digests must be present when workspace mutation receipts are included
    if "workspaceMutationReceipt" in body:
        assert body["workspaceMutationReceipt"]["receiptDigest"].startswith("sha256:")
    if "rollbackReceipt" in body:
        assert body["rollbackReceipt"]["rollbackDigest"].startswith("sha256:")
    # No raw content leaked
    encoded = json.dumps(body, sort_keys=True)
    assert "relay-contract.txt" not in encoded
    assert "relay contract verification" not in encoded
