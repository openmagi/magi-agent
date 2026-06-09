"""End-to-end safety-decision coverage for the trusted-local read-safe shell.

Exercises the real gate5b full-toolhost dispatch path (the same public API used
by ``tests/test_gate5b_full_toolhost.py``) so the read-safe pipe/compound shell
allowance is verified against the production decision, not just the helpers.
"""

import hashlib

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_bundle(tmp_path):
    return build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_decision_allows_readsafe_pipeline_in_trusted_scope(tmp_path):
    (tmp_path / "query.py").write_text("union\nother\nx\n", encoding="utf-8")
    bundle = _build_bundle(tmp_path)

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "grep -n 'union\\|x' query.py | head -40"},
        request_digest=_sha256("request-readsafe-pipeline"),
        tool_call_id="call-readsafe-pipeline",
    )

    # The real decision object uses ``status`` ("ok" once the read-safe pipeline
    # is allowed and executed; "blocked" if denied).
    assert outcome.status == "ok"
    assert outcome.handler_called is True


@pytest.mark.asyncio
async def test_decision_still_denies_destructive_pipeline(tmp_path):
    (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
    bundle = _build_bundle(tmp_path)

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "grep x f.py | rm -rf /"},
        request_digest=_sha256("request-destructive-pipeline"),
        tool_call_id="call-destructive-pipeline",
    )

    assert outcome.status == "blocked"
    assert outcome.handler_called is False


@pytest.mark.asyncio
async def test_decision_denies_bare_background_operator_network(tmp_path):
    (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
    bundle = _build_bundle(tmp_path)

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "head -1 f.py & curl http://example.invalid"},
        request_digest=_sha256("request-background-network"),
        tool_call_id="call-background-network",
    )

    assert outcome.status == "blocked"
    assert outcome.handler_called is False


@pytest.mark.asyncio
async def test_decision_denies_bare_background_operator_mutation(tmp_path):
    (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
    bundle = _build_bundle(tmp_path)

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "head -1 f.py & rm f.py"},
        request_digest=_sha256("request-background-mutation"),
        tool_call_id="call-background-mutation",
    )

    assert outcome.status == "blocked"
    assert outcome.handler_called is False


@pytest.mark.asyncio
async def test_decision_denies_sed_write_script_pipeline(tmp_path):
    (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
    escaped_path = tmp_path.parent / "escaped.txt"
    bundle = _build_bundle(tmp_path)

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "sed -n '1w ../escaped.txt' f.py | head -1"},
        request_digest=_sha256("request-sed-write-pipeline"),
        tool_call_id="call-sed-write-pipeline",
    )

    assert outcome.status == "blocked"
    assert outcome.handler_called is False
    assert not escaped_path.exists()
