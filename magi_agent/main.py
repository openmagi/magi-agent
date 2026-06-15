from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

import uvicorn

from . import __version__
from .app import create_app
from .config.env import (
    LOCAL_DEV_MODEL_SENTINEL,
    RuntimeEnvError,
    parse_gate5b4c3_shadow_generation_route_env,
    parse_runtime_env,
)
from .config.models import RuntimeConfig
from .evidence.observed_egress import (
    build_gate1a_observed_egress_evidence_provider_from_env,
)
from .runtime.openmagi_runtime import OpenMagiRuntime
from .runtime.hosted_defaults import apply_hosted_runtime_defaults
from .runtime.local_defaults import (
    LOCAL_FULL_RUNTIME_DEFAULTS_ENABLED_ENV,
    LOCAL_FULL_RUNTIME_ENV_DEFAULTS,
    apply_local_full_runtime_defaults,
    local_full_runtime_defaults_enabled,
)
from .transport.chat import (
    build_gate1a_readonly_tools_config_from_env,
    build_gate5b_full_toolhost_config_from_env,
    build_gate5b_user_visible_chat_route_config_from_env,
)

def resolve_server_port(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    env = os.environ if environ is None else environ
    default_port = int(env.get("CORE_AGENT_PORT", "8080"))
    raw_args = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(prog="magi-agent")
    parser.add_argument("--port", type=int, default=default_port)

    if raw_args and raw_args[0] == "serve":
        raw_args = raw_args[1:]
    elif raw_args and not raw_args[0].startswith("-"):
        parser.error(f"unknown command: {raw_args[0]}")

    return int(parser.parse_args(raw_args).port)


def main(argv: Sequence[str] | None = None) -> None:
    from .ops.otel_noise import silence_otel_detach_noise

    silence_otel_detach_noise()
    # Install-default-on memory: overlay ~/.magi/config.toml[memory] on the
    # install defaults ({enabled, prefer_local_search}) and setdefault the
    # matching MAGI_MEMORY_* env vars so the runtime gates (memory_turn_hook on
    # the SSE chat path, recall, projection) see them. Runs ONLY from this real
    # ``magi-agent serve`` entrypoint (never during library/test imports);
    # the code-level default is unchanged. Fail-soft.
    #
    # Gate by runtime profile, mirroring apply_local_full_runtime_defaults below:
    # the lean/opt-out profiles (safe|minimal|off|conservative|eval) must NOT
    # inherit install-default-on memory — they leave it at the code default (off)
    # unless config/env explicitly enables it.
    if local_full_runtime_defaults_enabled(os.environ):
        # File-driven install profile (e.g. Homebrew-seeded ~/.magi/profile.env):
        # setdefault MAGI_*/CORE_AGENT_* flags BEFORE the memory bootstrap so a
        # profile that sets MAGI_RUNTIME_PROFILE/memory flags is honoured. No file
        # => no-op (pip installs stay at code defaults). Explicit env still wins.
        from .cli.install_profile_bootstrap import apply_install_profile_bootstrap

        apply_install_profile_bootstrap(os.environ)
        from .cli.memory_bootstrap import apply_memory_config_bootstrap

        apply_memory_config_bootstrap(os.environ)
    port = resolve_server_port(argv)
    config = _parse_runtime_config(os.environ)
    if _local_runtime_defaults_active(config):
        apply_local_full_runtime_defaults(os.environ)
        _print_local_startup_notice(port)
    else:
        # Hosted bots (real bot_id/user_id/gateway_token) never inherit the
        # local-dev full overlay. Apply the explicit hosted control-stage overlay
        # instead: no-op unless MAGI_DEPLOYMENT=hosted, and byte-identical to
        # today at the default stage (off). See runtime/hosted_defaults.py.
        apply_hosted_runtime_defaults(os.environ)
    runtime = OpenMagiRuntime(config=config)
    runtime.gate5b4c3_shadow_generation_route_config = (
        parse_gate5b4c3_shadow_generation_route_env(os.environ)
    )
    runtime.gate5b_user_visible_chat_route_config = (
        build_gate5b_user_visible_chat_route_config_from_env(os.environ, config)
    )
    runtime.gate1a_readonly_tools_config = build_gate1a_readonly_tools_config_from_env(
        os.environ,
        config,
    )
    runtime.gate5b_full_toolhost_config = build_gate5b_full_toolhost_config_from_env(
        os.environ,
        config,
    )
    runtime.gate1a_observed_egress_evidence_provider = (
        build_gate1a_observed_egress_evidence_provider_from_env(os.environ)
    )
    app = create_app(runtime)
    uvicorn.run(app, host="0.0.0.0", port=port)


def _parse_runtime_config(environ: Mapping[str, str]):
    try:
        return parse_runtime_env(environ)
    except RuntimeEnvError:
        if _env_enabled(environ.get("MAGI_AGENT_REQUIRE_ENV")):
            raise
        local_env = {
            "BOT_ID": "local-bot",
            "USER_ID": "local-user",
            "GATEWAY_TOKEN": "local-dev-token",
            "CORE_AGENT_API_PROXY_URL": "http://127.0.0.1:0",
            "CORE_AGENT_CHAT_PROXY_URL": "http://127.0.0.1:0",
            "CORE_AGENT_REDIS_URL": "redis://127.0.0.1:0/0",
            "CORE_AGENT_MODEL": LOCAL_DEV_MODEL_SENTINEL,
            "CORE_AGENT_VERSION": f"{__version__}-local",
            **dict(environ),
        }
        return parse_runtime_env(local_env)


def _env_enabled(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _local_runtime_defaults_active(config: RuntimeConfig) -> bool:
    return (
        config.bot_id == "local-bot"
        and config.user_id == "local-user"
        and config.gateway_token == "local-dev-token"
    )


def _print_local_startup_notice(port: int) -> None:
    """Print an onboarding notice when ``serve`` runs with no hosted env.

    Shows the dashboard URL and whether a model provider is configured, so a
    fresh ``brew install`` + ``magi-agent serve`` user knows whether the chat
    will produce live replies or needs an API key first.
    """

    try:
        from .cli.providers import resolve_provider_config

        provider = resolve_provider_config()
    except Exception:
        provider = None

    lines = [
        "",
        "Open Magi Agent — local runtime",
        f"  Dashboard: http://localhost:{port}/dashboard",
    ]
    if provider is not None:
        lines.append(
            f"  Model provider: {provider.provider} ({provider.model}) — chat is ready."
        )
    else:
        lines.append(
            "  Model provider: none configured — the dashboard loads but chat "
            "replies need an API key."
        )
        lines.append(
            "  Set one of ANTHROPIC_API_KEY / OPENAI_API_KEY / "
            "GEMINI_API_KEY (or GOOGLE_API_KEY) / FIREWORKS_API_KEY (or add a "
            "[model] section to ~/.magi/config.toml), then restart serve."
        )
    lines.append("")
    print("\n".join(lines), file=sys.stderr, flush=True)
