"""PR-C — CLI memory bootstrap: config.toml[memory] → env, install-default-on.

The bootstrap (``magi_agent.cli.memory_bootstrap.apply_memory_config_bootstrap``)
runs ONCE at real CLI startup.  It overlays ``~/.magi/config.toml[memory]`` on
the install defaults ``{enabled: True, prefer_local_search: True}`` and
``setdefault``s the matching ``MAGI_MEMORY_*`` env vars (precedence:
``env > config > install-default``).

Invariants under test:
  * config absent → memory env on (master + prefer_local_search).
  * ``[memory] enabled = false`` → master env "0".
  * explicit pre-set env wins over config (setdefault).
  * malformed config → install defaults, no crash.
  * correct env-var NAMES set (asserted against the memory/config.py constants).
  * the CODE default is UNCHANGED: resolve_memory_config(env={}, config={}) still
    has master False (safety invariant the existing memory-off tests rely on).
  * default-on E2E: fresh install → resolver master + prefer_local_search ON, and
    a small flow flushes a daily entry + projection includes memory.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from magi_agent.cli.memory_bootstrap import apply_memory_config_bootstrap
from magi_agent.memory.config import (
    MASTER_ENV_VAR,
    PREFER_LOCAL_SEARCH_ENV_VAR,
    WRITE_ENABLED_ENV_VAR,
    PREFER_QMD_AUTO_REGISTER_ENV_VAR,
    resolve_memory_config,
)


# ---------------------------------------------------------------------------
# Bootstrap unit behaviour
# ---------------------------------------------------------------------------


def test_config_absent_sets_memory_on() -> None:
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={})
    # Install defaults: master + prefer_local_search ON.
    assert env[MASTER_ENV_VAR] == "1"
    assert env[PREFER_LOCAL_SEARCH_ENV_VAR] == "1"


def test_config_absent_sets_correct_env_var_names() -> None:
    """The bootstrap sets EXACTLY the two install-default env vars, by the
    canonical names from memory/config.py (no hardcoded strings)."""
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={})
    assert set(env) == {MASTER_ENV_VAR, PREFER_LOCAL_SEARCH_ENV_VAR}
    # qmd auto-register stays opt-in: the bootstrap never sets it.
    assert PREFER_QMD_AUTO_REGISTER_ENV_VAR not in env


def test_config_enabled_false_sets_master_off() -> None:
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={"memory": {"enabled": False}})
    assert env[MASTER_ENV_VAR] == "0"
    # prefer_local_search not overridden → still its install default (on).
    assert env[PREFER_LOCAL_SEARCH_ENV_VAR] == "1"


def test_config_prefer_local_search_false_overrides_install_default() -> None:
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={"memory": {"prefer_local_search": False}})
    assert env[MASTER_ENV_VAR] == "1"
    assert env[PREFER_LOCAL_SEARCH_ENV_VAR] == "0"


def test_explicit_env_wins_over_config_setdefault() -> None:
    """A pre-set env var must survive the bootstrap (setdefault semantics)."""
    env = {MASTER_ENV_VAR: "0"}  # operator explicitly disabled the master
    apply_memory_config_bootstrap(env, config={"memory": {"enabled": True}})
    # setdefault did NOT overwrite the explicit "0", even though config says on.
    assert env[MASTER_ENV_VAR] == "0"
    # The other key was unset, so the install default applies.
    assert env[PREFER_LOCAL_SEARCH_ENV_VAR] == "1"


def test_config_string_bool_coerced() -> None:
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={"memory": {"enabled": "off"}})
    assert env[MASTER_ENV_VAR] == "0"


def test_malformed_config_falls_back_to_install_defaults_no_crash() -> None:
    # ``memory`` is not a table (a list) — bootstrap must not crash and must apply
    # the install defaults.
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={"memory": ["not", "a", "table"]})
    assert env[MASTER_ENV_VAR] == "1"
    assert env[PREFER_LOCAL_SEARCH_ENV_VAR] == "1"


def test_unrecognized_config_value_uses_install_default() -> None:
    # An un-coercible value (e.g. an int 2) is treated as "not set" → install default.
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={"memory": {"enabled": 2}})
    assert env[MASTER_ENV_VAR] == "1"


def test_loads_config_file_when_config_omitted(monkeypatch) -> None:
    """With no ``config`` arg the bootstrap reads providers._load_config_file."""
    import magi_agent.cli.memory_bootstrap as bootstrap_mod

    monkeypatch.setattr(
        "magi_agent.cli.providers._load_config_file",
        lambda: {"memory": {"enabled": False}},
    )
    env: dict[str, str] = {}
    bootstrap_mod.apply_memory_config_bootstrap(env)
    assert env[MASTER_ENV_VAR] == "0"


# ---------------------------------------------------------------------------
# CODE DEFAULT STILL OFF — the safety invariant
# ---------------------------------------------------------------------------


def test_code_default_resolver_still_off() -> None:
    """The bootstrap does NOT change the code-level default: the resolver with an
    empty env/config still has the master OFF. Existing memory-off tests rely on
    this (they never run the bootstrap)."""
    cfg = resolve_memory_config(env={}, config={})
    assert cfg.master_enabled is False
    assert cfg.write_enabled is False
    assert cfg.recall_enabled is False
    assert cfg.projection_enabled is False
    assert cfg.compaction_enabled is False
    assert cfg.prefer_local_search is False


# ---------------------------------------------------------------------------
# DEFAULT-ON E2E — fresh install through bootstrap → resolver → flow
# ---------------------------------------------------------------------------


def test_fresh_install_bootstrap_resolves_memory_on() -> None:
    """Simulate a fresh install (empty config, clean env), run the bootstrap,
    then resolve against the produced env: master + the cascade + the
    prefer_local_search opt-in are ON; qmd auto-register stays OFF."""
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={})

    cfg = resolve_memory_config(env=env, config={})
    assert cfg.master_enabled is True
    # Master cascade → engine sub-flags on.
    assert cfg.write_enabled is True
    assert cfg.recall_enabled is True
    assert cfg.projection_enabled is True
    assert cfg.compaction_enabled is True
    # The prefer_local_search opt-in we explicitly install-default ON.
    assert cfg.prefer_local_search is True
    # qmd auto-register stays opt-in (PyBM25 is the zero-dep default).
    assert cfg.prefer_qmd_auto_register is False


def test_fresh_install_full_flow_flushes_daily_and_projects(tmp_path: Path) -> None:
    """End-to-end on the produced env: a recorded turn flushes a daily entry and
    the prompt projection then includes that memory."""
    from magi_agent.runtime.memory_turn_hook import (
        record_turn,
        reset_session_compaction_state,
    )

    reset_session_compaction_state()

    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={})
    cfg = resolve_memory_config(env=env, config={})

    workspace = tmp_path
    record_turn(
        workspace_root=workspace,
        session_id="sess-1",
        turn_id="turn-1",
        user_text="remember the deploy node is hel-system-1",
        assistant_text="Noted: the deploy build node is hel-system-1 (aarch64).",
        used_tool=True,  # non-trivial → flushed
        config=cfg,
        today=date(2026, 6, 9),
    )

    daily = workspace / "memory" / "daily" / "2026-06-09.md"
    assert daily.is_file(), "default-on install should flush a daily entry"
    body = daily.read_text(encoding="utf-8")
    assert "hel-system-1" in body

    # Projection includes the flushed memory. The bootstrap set the projection
    # gate ON in ``env`` (via the master cascade), which is what the projection
    # gate reads at runtime; assert the resolver agrees, then drive the projector
    # with that resolved enabled flag.
    assert cfg.projection_enabled is True
    from magi_agent.memory.prompt_projection import MemoryPromptProjector

    result = MemoryPromptProjector(
        workspace_root=workspace, enabled=cfg.projection_enabled
    ).project()
    assert result.enabled is True
    assert "hel-system-1" in result.snapshot_block


# ---------------------------------------------------------------------------
# WIRING — both real CLI entrypoints run the bootstrap
# ---------------------------------------------------------------------------


def test_cli_app_main_invokes_bootstrap(monkeypatch) -> None:
    """``magi`` (cli.app:main) runs the memory bootstrap before dispatch."""
    import magi_agent.cli.app as app_mod
    import magi_agent.cli.memory_bootstrap as bootstrap_mod

    calls: list[object] = []
    # main() does a function-local ``from ... import apply_memory_config_bootstrap``,
    # so patch at the source module the import resolves against.
    monkeypatch.setattr(
        bootstrap_mod, "apply_memory_config_bootstrap", lambda env: calls.append(env)
    )
    # Stop short of actually running the Typer app / agent.
    monkeypatch.setattr(app_mod, "app", lambda: None)
    app_mod.main()
    assert calls and calls[0] is app_mod.os.environ


def test_serve_main_invokes_bootstrap(monkeypatch, tmp_path) -> None:
    """``magi-agent serve`` (main:main) runs the memory bootstrap at startup."""
    from magi_agent import main as main_module
    import magi_agent.cli.memory_bootstrap as bootstrap_mod

    calls: list[object] = []
    monkeypatch.setattr(
        bootstrap_mod, "apply_memory_config_bootstrap", lambda env: calls.append(env)
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kwargs: None)
    main_module.main(["serve", "--port", "9097"])
    assert calls and calls[0] is main_module.os.environ
