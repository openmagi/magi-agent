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
    # I-1: route the bootstrap port through the typed flag registry.
    # ``flag_int`` returns ``spec.default`` (8080) for unset / malformed,
    # byte-identical to the prior ``int(env.get(NAME, "8080"))`` shape.
    from magi_agent.config.flags import flag_int  # noqa: PLC0415

    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    default_port = flag_int("CORE_AGENT_PORT", env=env)
    # ``flag_str`` returns the registry default ("127.0.0.1") when unset.
    default_host = flag_str("MAGI_SERVE_HOST", env=env) or "127.0.0.1"
    raw_args = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(prog="magi-agent")
    parser.add_argument("--port", type=int, default=default_port)
    # ``--host`` is declared here too so it is DISCOVERABLE in
    # ``magi-agent --help`` (this is the parser that handles ``-h``, being the
    # first resolver invoked). The authoritative host value is still resolved by
    # ``resolve_server_host``; here we only parse-and-ignore it via
    # ``parse_known_args``, so behaviour is unchanged.
    parser.add_argument(
        "--host",
        type=str,
        default=default_host,
        help="Server bind host (env MAGI_SERVE_HOST, default 127.0.0.1 loopback).",
    )

    if raw_args and raw_args[0] == "serve":
        raw_args = raw_args[1:]
    elif raw_args and not raw_args[0].startswith("-"):
        parser.error(f"unknown command: {raw_args[0]}")

    return int(parser.parse_known_args(raw_args)[0].port)


def resolve_server_host(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the uvicorn bind host for ``serve``.

    Mirrors :func:`resolve_server_port`: an optional ``--host`` CLI flag wins,
    otherwise the typed ``MAGI_SERVE_HOST`` flag (env) is read, which now
    defaults to ``127.0.0.1`` (loopback only) so a stock ``magi serve`` is not
    LAN-reachable. Hosted infra sets ``MAGI_SERVE_HOST=0.0.0.0`` in its env;
    the desktop shell passes ``--host 127.0.0.1`` explicitly.
    """
    env = os.environ if environ is None else environ
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    # ``flag_str`` returns the registry default ("127.0.0.1") when unset.
    default_host = flag_str("MAGI_SERVE_HOST", env=env) or "127.0.0.1"
    raw_args = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(prog="magi-agent")
    parser.add_argument("--host", type=str, default=default_host)

    if raw_args and raw_args[0] == "serve":
        raw_args = raw_args[1:]
    elif raw_args and not raw_args[0].startswith("-"):
        parser.error(f"unknown command: {raw_args[0]}")

    # Ignore unrelated flags (e.g. ``--port``) so the two resolvers compose.
    host = parser.parse_known_args(raw_args)[0].host
    return str(host)


def main(argv: Sequence[str] | None = None) -> None:
    from .ops.otel_noise import silence_otel_detach_noise

    silence_otel_detach_noise()

    # ``magi-agent vault-serve`` runs the standalone Agent Vault sidecar (CA
    # bootstrap + credential-injection proxy + token-authed admin API). It is a
    # separate process from ``serve`` and shares none of the runtime wiring
    # below; dispatch early and return.
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "vault-serve":
        from .credentials_admin.vault_server import run_vault_server

        run_vault_server()
        return

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
        # setdefault MAGI_* flags BEFORE the memory bootstrap so a profile that
        # sets MAGI_RUNTIME_PROFILE/memory flags is honoured. No file => no-op
        # (pip installs stay at code defaults). Explicit env still wins.
        from .cli.install_profile_bootstrap import apply_install_profile_bootstrap

        apply_install_profile_bootstrap(os.environ)
        from .cli.memory_bootstrap import apply_memory_config_bootstrap

        apply_memory_config_bootstrap(os.environ)
    port = resolve_server_port(argv)
    host = resolve_server_host(argv)
    # Project the RESOLVED bind host (which honours the ``--host`` flag) back
    # onto the env so the transport-layer exposure<->authority coupling
    # (local_serve_permission_mode) sees the effective host, not just the raw
    # ``MAGI_SERVE_HOST`` env. Overwrite so a ``--host`` that differs from the
    # env value wins at request time too.
    os.environ["MAGI_SERVE_HOST"] = host
    config = _parse_runtime_config(os.environ)
    if _local_runtime_defaults_active(config):
        # ``MAGI_RUNTIME_PROFILE=lab`` is local-full plus the experimental
        # flat-flag set (see runtime/local_defaults.apply_lab_runtime_defaults).
        # Mirror the cli/app.py dispatch so a local ``magi-agent serve`` under the
        # lab profile gets the same opt-in dogfood tier. Both paths are
        # setdefault-based, so explicit env (incl. per-flag MAGI_X=0) still wins.
        from .config.flags import flag_str

        if (flag_str("MAGI_RUNTIME_PROFILE", env=os.environ) or "").strip().lower() == "lab":
            from .runtime.local_defaults import apply_lab_runtime_defaults

            apply_lab_runtime_defaults(os.environ)
        else:
            apply_local_full_runtime_defaults(os.environ)
        # User-facing control-plane behavior toggles (~/.magi/customize.json)
        # win over the profile seed just applied: project them onto os.environ
        # as an explicit overwrite. Fail-soft; a no-op when the section is empty.
        _apply_local_control_plane_overrides(os.environ)
        _maybe_start_local_vault_proxy(os.environ)
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
    uvicorn.run(app, host=host, port=port)


def _parse_runtime_config(environ: Mapping[str, str]):
    try:
        return parse_runtime_env(environ)
    except RuntimeEnvError:
        # I-1: route the require-env toggle through the typed flag
        # registry. Byte-identical: ``flag_bool`` returns ``False`` on
        # ``None``/unset (matches ``_env_enabled(None) -> False``) and
        # delegates every set value to ``is_true`` (matches
        # ``_env_enabled`` which itself wraps ``is_true``).
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415

        if flag_bool("MAGI_AGENT_REQUIRE_ENV", env=environ):
            raise
        # Per-install random gateway token (P0): replaces the publicly-known
        # ``local-dev-token`` constant with a token generated + persisted 0600
        # at ``~/.magi/serve_token`` and reused across runs. An explicit
        # ``GATEWAY_TOKEN`` in ``environ`` still wins (the ``**dict(environ)``
        # spread below), keeping the hosted path byte-identical.
        from magi_agent.config.serve_token import local_serve_gateway_token

        local_env = {
            "BOT_ID": "local-bot",
            "USER_ID": "local-user",
            "GATEWAY_TOKEN": local_serve_gateway_token(),
            "CORE_AGENT_API_PROXY_URL": "http://127.0.0.1:0",
            "CORE_AGENT_CHAT_PROXY_URL": "http://127.0.0.1:0",
            "CORE_AGENT_REDIS_URL": "redis://127.0.0.1:0/0",
            "CORE_AGENT_MODEL": LOCAL_DEV_MODEL_SENTINEL,
            "CORE_AGENT_VERSION": f"{__version__}-local",
            **dict(environ),
        }
        return parse_runtime_env(local_env)


def _env_enabled(value: str | None) -> bool:
    # I-2 PR A: delegates to the canonical truthy leaf. ``None`` reads False
    # (was already the behaviour); explicit truthy values read True.
    from magi_agent.config._truthy import is_true  # noqa: PLC0415

    return value is not None and is_true(value)


def _local_runtime_defaults_active(config: RuntimeConfig) -> bool:
    # Local-mode detection keys on the per-install serve token (P0): an explicit
    # hosted ``GATEWAY_TOKEN`` never matches, so hosted behaviour is unchanged.
    from magi_agent.config.serve_token import is_local_serve_token

    return (
        config.bot_id == "local-bot"
        and config.user_id == "local-user"
        and is_local_serve_token(config.gateway_token)
    )


def _apply_local_control_plane_overrides(environ) -> None:
    """Project ``customize.json`` control-plane toggles onto the environment.

    Runs after the profile (lab/full) seed so an explicit user toggle wins.
    Fail-soft: any failure to load/apply leaves the profile defaults in place.
    """
    try:
        from .customize.control_plane_overrides import (
            apply_control_plane_overrides_to_env,
        )
        from .customize.store import load_overrides

        apply_control_plane_overrides_to_env(environ, load_overrides())
    except Exception:  # noqa: BLE001 - never let a customize read break startup
        return


def _maybe_start_local_vault_proxy(environ) -> None:
    """Start the local credential-injecting proxy when enabled (local serve only).

    Gated by ``local_vault_proxy_enabled`` (requires the native local vault AND
    the proxy flag, forced OFF when MAGI_VAULT_ADMIN_URL is set — i.e. hosted).
    When it can start, the three ``MAGI_EGRESS_PROXY_*`` vars are setdefault'd to
    point at it so the A egress seam routes tool egress through the proxy.

    Fail-soft: if the optional ``magi-agent[vault]`` extra (mitmproxy) is missing,
    log the install hint and continue serving WITHOUT the proxy — never crash
    serve.
    """
    from .credentials_admin.local_vault import resolve_vault_dir
    from .credentials_admin.vault_local import local_vault_proxy_enabled

    if not local_vault_proxy_enabled(environ):
        return

    from .credentials_admin.local_proxy import (
        LocalProxyUnavailable,
        start_local_proxy,
    )

    vault_dir = resolve_vault_dir(environ)
    try:
        handle = start_local_proxy(vault_dir)
    except LocalProxyUnavailable as exc:
        print(
            "  Credential proxy: not started — "
            f"{exc} (mitmproxy not installed). "
            "Install the 'vault' extra to let the bot use registered "
            "credentials without seeing them: pip install 'magi-agent[vault]'.",
            file=sys.stderr,
            flush=True,
        )
        return
    except Exception:  # noqa: BLE001 - never crash serve on proxy failure
        print(
            "  Credential proxy: failed to start; continuing without it.",
            file=sys.stderr,
            flush=True,
        )
        return

    environ.setdefault("MAGI_EGRESS_PROXY_ENABLED", "1")
    environ.setdefault(
        "MAGI_EGRESS_PROXY_URL", f"http://127.0.0.1:{handle.port}"
    )
    environ.setdefault("MAGI_EGRESS_PROXY_CA_CERT_PATH", handle.ca_cert_path)
    # Keep the handle alive for the process lifetime + best-effort clean shutdown.
    import atexit

    atexit.register(handle.stop)
    print(
        f"  Credential proxy: 127.0.0.1:{handle.port} — registered credentials "
        "are injected into matching egress (the bot never sees the secret).",
        file=sys.stderr,
        flush=True,
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
            f"  Model provider: {provider.provider} ({provider.model}). Chat is ready."
        )
    else:
        from .config.flags import flag_bool  # noqa: PLC0415

        lines.append(
            "  Model provider: none configured. The dashboard loads but chat "
            "replies need an API key."
        )
        if flag_bool("MAGI_ONBOARDING_WIZARD_ENABLED"):
            lines.append(
                f"  Finish setup in the dashboard: http://localhost:{port}/dashboard "
                "(enter an API key, no restart needed)."
            )
        else:
            lines.append(
                "  Set one of ANTHROPIC_API_KEY / OPENAI_API_KEY / "
                "GEMINI_API_KEY (or GOOGLE_API_KEY) / FIREWORKS_API_KEY (or add a "
                "[model] section to ~/.magi/config.toml), then restart serve."
            )
    lines.append("")
    print("\n".join(lines), file=sys.stderr, flush=True)
