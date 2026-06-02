import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)


def _sha256(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_selected_scope_exposes_full_workspace_tools_and_receipts(tmp_path):
    bundle = build_gate5b_full_toolhost_bundle(
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

    assert bundle.status == "ready"
    assert [tool.name for tool in bundle.tools] == list(GATE5B_FULL_TOOLHOST_TOOL_NAMES)
    assert set(bundle.exposed_tool_names) >= {"FileRead", "FileWrite", "FileEdit", "PatchApply", "Bash"}

    write_outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "notes/hello.txt", "content": "hello from selected toolhost\n"},
        request_digest=_sha256("request-1"),
        tool_call_id="call-write-1",
    )

    assert write_outcome.status == "ok"
    assert (tmp_path / "notes/hello.txt").read_text(encoding="utf-8") == (
        "hello from selected toolhost\n"
    )
    receipt = write_outcome.coding_mutation_receipt
    assert receipt is not None
    projection = receipt.public_projection()
    assert projection["toolName"] == "FileWrite"
    assert projection["status"] == "success"
    assert projection["productionWorkspaceMutationAllowed"] is False
    assert projection["workspaceDigest"].startswith("sha256:")

    bash_outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "printf ok"},
        request_digest=_sha256("request-1"),
        tool_call_id="call-bash-1",
    )

    assert bash_outcome.status == "ok"
    assert bash_outcome.coding_mutation_receipt is not None


@pytest.mark.asyncio
async def test_full_toolhost_blocks_path_escape_and_non_selected_scope(tmp_path):
    config = Gate5BFullToolHostConfig.model_validate(
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
    )
    non_selected = build_gate5b_full_toolhost_bundle(
        config=config,
        scope={
            "selectedBotDigest": _sha256("other-bot"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )
    assert non_selected.status == "blocked"
    assert non_selected.tools == ()

    selected = build_gate5b_full_toolhost_bundle(
        config=config,
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )

    blocked = await selected.host.dispatch(
        "FileWrite",
        {"path": "../escape.txt", "content": "nope"},
        request_digest=_sha256("request-2"),
        tool_call_id="call-write-escape",
    )

    assert blocked.status == "blocked"
    assert blocked.reason == "path_policy_denied"
    assert not (tmp_path.parent / "escape.txt").exists()
