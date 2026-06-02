from __future__ import annotations

import subprocess
import sys

from magi_agent.runtime.slash_control_boundary import (
    SlashControlBoundary,
    SlashControlConfig,
    SlashControlRequest,
)


def _request(text: str) -> SlashControlRequest:
    return SlashControlRequest(text=text, sessionKey="session-1", turnId="turn-1")


def test_slash_control_boundary_is_disabled_by_default() -> None:
    decision = SlashControlBoundary(SlashControlConfig()).project(_request("/plan ship it"))

    assert decision.status == "disabled"
    assert decision.reason_codes == ("slash_control_disabled",)
    assert decision.intent is None
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_slash_control_boundary_projects_first_class_commands_as_intents_only() -> None:
    boundary = SlashControlBoundary(
        SlashControlConfig(enabled=True, localFakeCommandProjectionEnabled=True),
    )

    commands = {
        "/compact": "compact",
        "/reset": "reset",
        "/status": "status",
        "/onboarding": "onboarding",
        "/plan interview first": "plan",
        "/goal finish branch": "goal",
        "/superpowers:using-superpowers": "superpowers",
    }
    projections = [boundary.project(_request(text)).public_projection() for text in commands]

    assert [projection["intent"]["command"] for projection in projections] == list(commands.values())
    assert all(projection["status"] == "command_intent" for projection in projections)
    assert all(projection["authorityFlags"]["productionWritesEnabled"] is False for projection in projections)
    assert all(projection["authorityFlags"]["userVisibleOutput"] is False for projection in projections)
    assert projections[4]["intent"]["recipePackRef"] == "openmagi.agent-methodology"


def test_slash_control_boundary_blocks_raw_private_payloads_and_redacts_metadata() -> None:
    boundary = SlashControlBoundary(
        SlashControlConfig(enabled=True, localFakeCommandProjectionEnabled=True),
    )

    decision = boundary.project(
        SlashControlRequest(
            text="/plan raw_tool_args /Users/kevin/private token ghp_slashSecret",
            sessionKey="session-1",
            metadata={
                "botToken": "123456:ABC-secret-token",
                "note": "safe",
                "rawPrompt": "hidden reasoning",
            },
        )
    )

    projection = decision.public_projection()
    encoded = str(projection)
    assert decision.status == "blocked"
    assert decision.reason_codes == ("slash_argument_private_payload_blocked",)
    assert "ghp_slashSecret" not in encoded
    assert "123456:ABC-secret-token" not in encoded
    assert "/Users/kevin" not in encoded
    assert projection["diagnosticMetadata"] == {
        "enabled": True,
        "localFakeCommandProjectionEnabled": True,
        "slashRuntimeAttached": False,
        "productionWritesEnabled": False,
        "routeAttached": False,
        "note": "safe",
    }


def test_slash_control_boundary_forged_authority_cannot_turn_on() -> None:
    from magi_agent.runtime.slash_control_boundary import (
        SlashCommandIntent,
        SlashControlAuthorityFlags,
        SlashControlDecision,
    )

    forged = SlashControlDecision.model_construct(
        status="command_intent",
        intent=SlashCommandIntent.model_construct(
            command="plan",
            raw_command="/plan",
            argument_preview="Authorization: Bearer unsafe-token",
            control_ref="/workspace/private",
            recipe_pack_ref="openmagi.agent-methodology",
            checkpoint_ref="checkpoint:agent-methodology:plan",
        ),
        authorityFlags=SlashControlAuthorityFlags.model_construct(
            slashRuntimeAttached=True,
            userVisibleOutput=True,
            productionWritesEnabled=True,
        ),
    )

    projection = forged.public_projection()
    encoded = str(projection)
    assert "unsafe-token" not in encoded
    assert "/workspace/private" not in encoded
    assert projection["authorityFlags"]["slashRuntimeAttached"] is False
    assert projection["authorityFlags"]["userVisibleOutput"] is False


def test_slash_control_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.runtime.slash_control_boundary")
forbidden = (
    "google.adk.runners",
    "google.adk.agents",
    "subprocess",
    "requests",
    "httpx",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
