# tests/gates/test_gate5b_shell_env_hygiene.py
"""Non-interactive env hygiene for the Bash/TestRun subprocess.

Pagers and progress bars hang or flood the bounded output capture; the shell
subprocess env therefore carries non-interactive defaults. They are DEFAULTS:
any value already present in the env (overlay/caller) and any inline
``KEY=value`` assignment in the command itself win over them.
"""
import hashlib

import pytest

from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.gates import gate5b_full_toolhost as g5


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


EXPECTED_DEFAULTS = {
    "PAGER": "cat",
    "MANPAGER": "cat",
    "GIT_PAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
}


def test_noninteractive_defaults_constant_matches_expected() -> None:
    assert dict(g5._NONINTERACTIVE_ENV_DEFAULTS) == EXPECTED_DEFAULTS


def test_bash_env_includes_noninteractive_defaults(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_EGRESS_PROXY_ENABLED", raising=False)
    env = g5._build_bash_env(EgressProxyConfig.from_env({}))
    assert env["PATH"]
    for key, value in EXPECTED_DEFAULTS.items():
        assert env[key] == value


def test_noninteractive_defaults_do_not_override_existing_values() -> None:
    env = {"PAGER": "less", "TQDM_DISABLE": "0"}
    g5._apply_noninteractive_env_defaults(env)
    # Explicit values win over the hygiene defaults.
    assert env["PAGER"] == "less"
    assert env["TQDM_DISABLE"] == "0"
    # Missing keys still receive the defaults.
    assert env["MANPAGER"] == "cat"
    assert env["GIT_PAGER"] == "cat"
    assert env["LESS"] == "-R"
    assert env["PIP_PROGRESS_BAR"] == "off"


def _bash_bundle(tmp_path, *, max_calls: int = 4):
    return g5.build_gate5b_full_toolhost_bundle(
        config=g5.Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": ("Bash",),
                "maxToolCallsPerTurn": max_calls,
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
async def test_gate5b_bash_subprocess_sees_noninteractive_defaults(tmp_path) -> None:
    bundle = _bash_bundle(tmp_path)

    for index, (key, value) in enumerate(
        (("PAGER", "cat"), ("GIT_PAGER", "cat"), ("TQDM_DISABLE", "1"))
    ):
        outcome = await bundle.host.dispatch(
            "Bash",
            {"command": f"printenv {key}"},
            request_digest=_sha256(f"request-printenv-hygiene-{index}"),
            tool_call_id=f"call-printenv-hygiene-{index}",
        )

        assert outcome.status == "ok"
        assert isinstance(outcome.output_preview, dict)
        assert str(outcome.output_preview["stdout"]) == f"{value}\n"


@pytest.mark.asyncio
async def test_gate5b_bash_inline_env_assignment_wins_over_defaults(tmp_path) -> None:
    bundle = _bash_bundle(tmp_path)

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "PAGER=less printenv PAGER"},
        request_digest=_sha256("request-printenv-hygiene-override"),
        tool_call_id="call-printenv-hygiene-override",
    )

    assert outcome.status == "ok"
    assert isinstance(outcome.output_preview, dict)
    assert str(outcome.output_preview["stdout"]) == "less\n"
