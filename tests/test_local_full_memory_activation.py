"""WS2 PR2a - belt-and-suspenders memory master in the LOCAL_FULL overlay.

Design: WS2 memory continuity, section 5 "PR2a - belt-and-suspenders master
entry + bootstrap-contract reconciliation".

The shipped ``magi`` / ``magi serve`` full install ALREADY resolves the memory
master ON via the one-time CLI bootstrap
(``cli/memory_bootstrap.apply_memory_config_bootstrap`` -> setdefault
``MAGI_MEMORY_ENABLED=1``), which runs BEFORE the
``apply_local_full_runtime_defaults`` overlay. PR2a therefore does NOT activate
memory; it adds ``MAGI_MEMORY_ENABLED=1`` to ``LOCAL_FULL_RUNTIME_ENV_DEFAULTS``
purely as defense-in-depth so the overlay surface is self-consistent with the
bootstrap and covers a non-CLI embedder that applies the overlay WITHOUT the
bootstrap. It is a no-op at real CLI startup (the bootstrap setdefault wins by
ordering).

These tests pin:
  * the corrected premise (bootstrap already resolves the master + cascade ON),
  * the overlay entry being a no-op behind the bootstrap yet load-bearing for a
    bootstrap-less embedder,
  * the full overlay resolving master + recall + projection + compaction +
    prefer_local_search + write ON while keeping the opt-ins OFF,
  * the lean-profile-widening (#641) hazard: eval / safe / registry-default stay
    master OFF, and an explicit operator opt-out (``MAGI_MEMORY_ENABLED=0``) wins,
  * the SC-8 hosted guarantees: the hosted overlay never sets a memory flag, and
    a hosted-identity bot never runs the full overlay at all.

Hermetic per SC-9: every resolver call injects an explicit ``env=`` dict and a
module-scoped autouse fixture clears any inherited ``MAGI_MEMORY_*`` /
``MAGI_RUNTIME_PROFILE`` from the developer shell via a prefix glob.
"""
from __future__ import annotations

import pytest

from magi_agent.cli.memory_bootstrap import apply_memory_config_bootstrap
from magi_agent.config.models import RuntimeConfig
from magi_agent.main import _local_runtime_defaults_active
from magi_agent.memory.config import MASTER_ENV_VAR, resolve_memory_config
from magi_agent.runtime.hosted_defaults import (
    CONTROL_STAGES,
    apply_hosted_runtime_defaults,
)
from magi_agent.runtime.local_defaults import (
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)


@pytest.fixture(autouse=True)
def clear_memory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module-scoped hermeticity: strip any inherited ``MAGI_MEMORY_*`` and
    ``MAGI_RUNTIME_PROFILE`` from ``os.environ`` so a developer shell that exports
    ``MAGI_MEMORY_*=1`` (Kevin's does) cannot perturb these injected-env cases.

    A prefix GLOB (not an allow-list) is mandatory: the family includes
    ``MAGI_MEMORY_RECALL_RERANK_ENABLED`` and ``MAGI_MEMORY_LOCAL_DEV``, both of
    which an allow-list could silently miss. Scoped to THIS module only (never a
    root-conftest autouse) to avoid the #641-class blast radius across the ~40
    other ``MAGI_MEMORY``-referencing test files.
    """
    import os

    for key in list(os.environ):
        if key.startswith("MAGI_MEMORY"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)


# ---------------------------------------------------------------------------
# Corrected premise: the bootstrap (not the overlay) is the activation seam.
# ---------------------------------------------------------------------------
def test_bootstrap_already_resolves_master_on() -> None:
    """The shipped CLI path recalls WITHOUT the overlay (encodes section 1.1)."""
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={})
    cfg = resolve_memory_config(env=env)
    assert cfg.master_enabled is True
    assert cfg.recall_enabled is True
    assert cfg.projection_enabled is True
    assert cfg.compaction_enabled is True
    assert cfg.prefer_local_search is True


def test_overlay_entry_is_noop_behind_bootstrap() -> None:
    """PR2a is belt-and-suspenders, not the fix.

    Case 1: bootstrap FIRST, then overlay -> the overlay setdefault must not flip
    the master the bootstrap already set. Case 2: overlay ONLY (a non-CLI embedder
    with no bootstrap) -> the belt-and-suspenders entry sets the master.
    """
    # Case 1: bootstrap then overlay.
    env: dict[str, str] = {}
    apply_memory_config_bootstrap(env, config={})
    bootstrap_value = env[MASTER_ENV_VAR]
    apply_local_full_runtime_defaults(env)
    assert env[MASTER_ENV_VAR] == "1"
    assert env[MASTER_ENV_VAR] == bootstrap_value

    # Case 2: overlay only (the case the belt-and-suspenders entry actually covers).
    env2: dict[str, str] = {}
    apply_local_full_runtime_defaults(env2)
    assert env2[MASTER_ENV_VAR] == "1"


# ---------------------------------------------------------------------------
# Full overlay resolution.
# ---------------------------------------------------------------------------
def test_full_profile_resolves_master_on() -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    cfg = resolve_memory_config(env=env)
    assert cfg.master_enabled is True
    assert cfg.recall_enabled is True
    assert cfg.projection_enabled is True
    assert cfg.compaction_enabled is True
    assert cfg.prefer_local_search is True
    assert cfg.write_enabled is True


def test_full_profile_keeps_optins_off() -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    cfg = resolve_memory_config(env=env)
    # The opt-ins stay OFF under the LOCAL_FULL overlay surface. The SEPARATE
    # dogfood-full-on.env surface deliberately sets vector_search ON; the two
    # opposite assertions are different surfaces, NOT a contradiction.
    assert cfg.soul_write_enabled is False
    assert cfg.vector_search is False
    assert cfg.prefer_qmd_auto_register is False


# ---------------------------------------------------------------------------
# Lean-profile-widening (#641) hazard pins: eval / safe / registry-default OFF.
# ---------------------------------------------------------------------------
def test_eval_profile_master_off() -> None:
    env: dict[str, str] = {}
    apply_local_eval_runtime_defaults(env)
    cfg = resolve_memory_config(env=env)
    assert cfg.master_enabled is False
    assert cfg.recall_enabled is False
    assert cfg.projection_enabled is False
    assert cfg.compaction_enabled is False


def test_safe_profile_master_off() -> None:
    env: dict[str, str] = {"MAGI_RUNTIME_PROFILE": "safe"}
    apply_local_full_runtime_defaults(env)  # no-ops under a safe profile
    cfg = resolve_memory_config(env=env)
    assert cfg.master_enabled is False


def test_registry_default_unchanged() -> None:
    cfg = resolve_memory_config(env={})
    assert cfg.master_enabled is False


def test_explicit_operator_optout_wins() -> None:
    env: dict[str, str] = {MASTER_ENV_VAR: "0"}
    apply_local_full_runtime_defaults(env)
    cfg = resolve_memory_config(env=env)
    assert cfg.master_enabled is False


# ---------------------------------------------------------------------------
# SC-8: hosted stays OFF on both layers.
# ---------------------------------------------------------------------------
def test_hosted_defaults_unchanged() -> None:
    """The hosted overlay never sets a memory flag, at any control stage."""
    for stage in CONTROL_STAGES:
        env: dict[str, str] = {
            "MAGI_DEPLOYMENT": "hosted",
            "MAGI_CONTROL_STAGE": stage,
        }
        apply_hosted_runtime_defaults(env)
        assert MASTER_ENV_VAR not in env
        assert resolve_memory_config(env=env).master_enabled is False


def test_hosted_identity_skips_full_overlay() -> None:
    """The load-bearing SC-8 seam: a hosted-identity bot never runs the full
    overlay, so the PR2a ``MAGI_MEMORY_ENABLED=1`` seed (which lives ONLY in
    ``LOCAL_FULL_RUNTIME_ENV_DEFAULTS``) is never applied on hosted.
    """
    config = RuntimeConfig(
        botId="186bf3d7-7d00-4c8b-86c9-c1734c66a1e4",
        userId="hosted-user-42",
        gatewayToken="hosted-gateway-token",
        apiProxyUrl="http://127.0.0.1:1/",
        chatProxyUrl="http://127.0.0.1:2/",
        redisUrl="redis://127.0.0.1:3/0",
        model="claude-opus-4-8",
    )
    assert _local_runtime_defaults_active(config) is False

    # Drive the dispatch directly: a hosted bot takes the ELSE branch and runs
    # only apply_hosted_runtime_defaults, never apply_local_full_runtime_defaults.
    env: dict[str, str] = {"MAGI_DEPLOYMENT": "hosted"}
    apply_hosted_runtime_defaults(env)
    assert MASTER_ENV_VAR not in env
