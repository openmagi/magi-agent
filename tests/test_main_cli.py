from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

import magi_agent
from magi_agent import main as main_module
from magi_agent.config.env import RuntimeEnvError
from magi_agent.main import resolve_server_port

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_LOCAL_FULL_RUNTIME_DEFAULTS = {
    "MAGI_RUNTIME_PROFILE": "full",
    "MAGI_AGENT_LOCAL_CHAT_ROUTE": "on",
    "MAGI_STREAMING_CHAT": "on",
    "MAGI_FIRST_PARTY_TOOLS_ENABLED": "1",
    "MAGI_RUNNER_POLICY_ROUTING_ENABLED": "1",
    "MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED": "0",
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
    "MAGI_LOOP_GUARD_ENABLED": "1",
    "MAGI_ERROR_RECOVERY_ENABLED": "1",
    "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
    "MAGI_MAX_STEPS_BRAKE_ENABLED": "1",
    "MAGI_SELF_REVIEW_ENABLED": "1",
    "MAGI_SELF_REVIEW_SHADOW": "0",
    "MAGI_SELF_REVIEW_PIPELINE_ENABLED": "1",
    "MAGI_SELF_REVIEW_LIVE_ENABLED": "1",
    "MAGI_SELF_REVIEW_TELEMETRY_ENABLED": "1",
    "MAGI_READ_LEDGER_ENABLED": "1",
    "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED": "1",
    "MAGI_LSP_DIAGNOSTICS_ENABLED": "1",
    "MAGI_READ_QUALITY_ENABLED": "1",
    "MAGI_RIPGREP_ENABLED": "1",
    "MAGI_APPLY_PATCH_ENABLED": "1",
    "MAGI_PROVIDER_REPAIR_ENABLED": "1",
    "MAGI_TOOL_CONCURRENCY_ENABLED": "1",
    "MAGI_MAX_TOOL_CONCURRENCY": "8",
    "MAGI_MODEL_AWARE_PROMPTS_ENABLED": "1",
    "MAGI_CODING_REPAIR_LOOP_ENABLED": "1",
    "MAGI_GA_LIVE_ENABLED": "1",
    "MAGI_MESSAGE_CACHE_ENABLED": "1",
    "MAGI_FILE_TOOLS_ENABLED": "1",
    "MAGI_SELF_INTROSPECTION_ENABLED": "1",
    "MAGI_MEMORY_WRITE_READINESS_ENABLED": "1",
    "MAGI_MEMORY_WRITE_ENABLED": "1",
    "MAGI_MEMORY_LOCAL_DEV": "1",
    "MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED": "1",
    "MAGI_DEFERRED_TOOLS_ENABLED": "1",
    "MAGI_CHANNEL_WORKFLOWS_ENABLED": "1",
    "MAGI_WORKFLOW_EXECUTOR_ENABLED": "1",
    "MAGI_SCHEDULER_EXECUTOR_ENABLED": "1",
    "MAGI_SCHEDULER_SHADOW": "0",
    "MAGI_OBSERVABILITY_ENABLED": "1",
    "MAGI_OBS_HOME": ".openmagi",
    "MAGI_SESSION_PERSISTENCE_ENABLED": "1",
    "MAGI_LEARNING_ENABLED": "true",
    "MAGI_LEARNING_REFLECTION_ENABLED": "1",
    "MAGI_LEARNING_DASHBOARD_ENABLED": "1",
    "MAGI_LEARNING_TELEMETRY_ENABLED": "1",
    "MAGI_LEARNING_LIVE_ENABLED": "1",
    "MAGI_SKILL_CURATOR_ENABLED": "1",
    "MAGI_SKILL_CURATOR_SHADOW": "0",
    "MAGI_AUTOPILOT": "1",
}


@pytest.fixture(autouse=True)
def _restore_process_env_after_test():
    original = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(original)


def test_release_version_and_local_health_version_are_aligned() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    version = pyproject["project"]["version"]

    assert version == magi_agent.__version__
    config = main_module._parse_runtime_config({})  # noqa: SLF001
    assert config.build.version == f"{version}-local"


def test_resolve_server_port_uses_core_agent_port_when_no_args() -> None:
    assert resolve_server_port([], environ={"CORE_AGENT_PORT": "9090"}) == 9090


def test_resolve_server_port_supports_serve_port_command() -> None:
    assert resolve_server_port(["serve", "--port", "9091"], environ={}) == 9091


def test_resolve_server_port_supports_direct_port_option() -> None:
    assert resolve_server_port(["--port", "9092"], environ={}) == 9092


def test_resolve_server_port_rejects_unknown_commands() -> None:
    with pytest.raises(SystemExit):
        resolve_server_port(["run"], environ={})


def test_main_help_does_not_require_runtime_environment(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(["--help"])

    assert exc_info.value.code == 0
    assert "usage: magi-agent" in capsys.readouterr().out


def test_main_serve_help_does_not_require_runtime_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(["serve", "--help"])

    assert exc_info.value.code == 0
    assert "usage: magi-agent" in capsys.readouterr().out


def test_main_uses_local_full_runtime_defaults_when_env_is_absent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    for key in EXPECTED_LOCAL_FULL_RUNTIME_DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(main_module.LOCAL_FULL_RUNTIME_DEFAULTS_ENABLED_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    main_module.main(["serve", "--port", "9093"])

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9093
    for key, value in EXPECTED_LOCAL_FULL_RUNTIME_DEFAULTS.items():
        assert main_module.os.environ[key] == value


def test_main_local_full_runtime_defaults_respect_safe_profile(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    for key in EXPECTED_LOCAL_FULL_RUNTIME_DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    main_module.main(["serve", "--port", "9095"])

    assert captured["port"] == 9095
    assert main_module.os.environ["MAGI_RUNTIME_PROFILE"] == "safe"
    assert "MAGI_RUNNER_POLICY_ROUTING_ENABLED" not in main_module.os.environ
    assert "MAGI_WORKFLOW_EXECUTOR_ENABLED" not in main_module.os.environ
    assert "MAGI_SELF_INTROSPECTION_ENABLED" not in main_module.os.environ
    assert "MAGI_MEMORY_WRITE_READINESS_ENABLED" not in main_module.os.environ
    assert "MAGI_MEMORY_WRITE_ENABLED" not in main_module.os.environ
    assert "MAGI_MEMORY_LOCAL_DEV" not in main_module.os.environ


@pytest.mark.parametrize("profile", ("safe", "minimal", "off", "conservative"))
def test_main_local_full_runtime_defaults_keep_safe_profiles_inert(
    monkeypatch,
    tmp_path: Path,
    profile: str,
) -> None:
    captured: dict[str, object] = {}

    for key in EXPECTED_LOCAL_FULL_RUNTIME_DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", profile)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module.uvicorn,
        "run",
        lambda app, **kwargs: captured.update(kwargs),
    )
    main_module.main(["serve", "--port", "9095"])

    assert captured["port"] == 9095
    assert main_module.os.environ["MAGI_RUNTIME_PROFILE"] == profile
    for key in (
        "MAGI_SELF_INTROSPECTION_ENABLED",
        "MAGI_MEMORY_WRITE_READINESS_ENABLED",
        "MAGI_MEMORY_WRITE_ENABLED",
        "MAGI_MEMORY_LOCAL_DEV",
    ):
        assert key not in main_module.os.environ


def test_main_local_full_runtime_defaults_respect_explicit_opt_out(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    for key in EXPECTED_LOCAL_FULL_RUNTIME_DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(main_module.LOCAL_FULL_RUNTIME_DEFAULTS_ENABLED_ENV, "0")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    main_module.main(["serve", "--port", "9096"])

    assert captured["port"] == 9096
    assert "MAGI_RUNTIME_PROFILE" not in main_module.os.environ
    assert "MAGI_RUNNER_POLICY_ROUTING_ENABLED" not in main_module.os.environ


def test_main_local_full_runtime_defaults_do_not_override_explicit_flags(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    for key in EXPECTED_LOCAL_FULL_RUNTIME_DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED", "1")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    main_module.main(["serve", "--port", "9097"])

    assert captured["port"] == 9097
    assert main_module.os.environ["MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED"] == "1"
    assert main_module.os.environ["MAGI_COMPOSIO_ENABLED"] == "on"


def test_main_can_require_runtime_environment(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_AGENT_REQUIRE_ENV", "1")

    with pytest.raises(RuntimeEnvError, match="Missing required runtime env"):
        main_module.main(["serve", "--port", "9094"])
