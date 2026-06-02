from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

import uvicorn

from .app import create_app
from .config.env import (
    RuntimeEnvError,
    parse_gate5b4c3_shadow_generation_route_env,
    parse_runtime_env,
)
from .evidence.observed_egress import (
    build_gate1a_observed_egress_evidence_provider_from_env,
)
from .runtime.openmagi_runtime import OpenMagiRuntime
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
    port = resolve_server_port(argv)
    config = _parse_runtime_config(os.environ)
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
            "CORE_AGENT_MODEL": "local-dev",
            "CORE_AGENT_VERSION": "0.1.1-local",
            **dict(environ),
        }
        return parse_runtime_env(local_env)


def _env_enabled(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}
