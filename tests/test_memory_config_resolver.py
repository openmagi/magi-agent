"""PR1 — single memory config resolver: precedence, default matrix, locks.

Governance invariant under test
-------------------------------
``resolve_memory_config`` is the single source of truth for Hipocampus memory
activation.  A flag gates *activation*, never *capability*:

  * Master OFF (default in PR1) → every write/recall/projection/compaction
    sub-flag resolves OFF (inert + safe).
  * Master ON → the sub-flags that enable the engine flip ON, EXCEPT the two
    documented opt-ins that stay False even under master-on: ``soul_write`` and
    ``vector_search``.
  * Explicit env/config override beats the master default, which beats the
    hardcoded default.

The resolver intentionally does NOT touch the two permanently-frozen authority
fields (DB-mutation / ADK-memory-service write) — those stay
``Literal[False]`` on the harness configs regardless of the resolver, asserted
here against the harness models.
"""
from __future__ import annotations

from typing import Literal, get_args

import pydantic
import pytest

from magi_agent.memory.config import (
    MASTER_ENV_VAR,
    MemoryRuntimeConfig,
    resolve_memory_config,
)


# ---------------------------------------------------------------------------
# Default matrix — master OFF (PR1 default)
# ---------------------------------------------------------------------------


def test_master_defaults_off_when_nothing_set() -> None:
    cfg = resolve_memory_config(env={}, config={})
    assert cfg.master_enabled is False
    # Every activation sub-flag is inert when the master is off.
    assert cfg.write_kill_switch_enabled is False
    assert cfg.write_readiness_enabled is False
    assert cfg.write_enabled is False
    assert cfg.recall_enabled is False
    assert cfg.projection_enabled is False
    assert cfg.compaction_enabled is False
    assert cfg.soul_write_enabled is False
    assert cfg.vector_search is False


def test_default_tunables_are_stable_regardless_of_master() -> None:
    cfg = resolve_memory_config(env={}, config={})
    assert cfg.prefer_qmd is True
    assert cfg.cooldown_hours == 24
    assert cfg.daily_threshold == 200
    assert cfg.weekly_threshold == 300
    assert cfg.monthly_threshold == 500
    assert cfg.root_max_tokens == 3000
    assert cfg.mode == "normal"
    assert cfg.recall_k >= 1
    assert cfg.recall_max_bytes >= 1


def test_config_is_frozen_immutable() -> None:
    cfg = resolve_memory_config(env={}, config={})
    # Frozen pydantic v2 raises ValidationError on attribute mutation.
    with pytest.raises(pydantic.ValidationError):
        cfg.write_enabled = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Master ON — activation sub-flags flip, opt-ins stay off
# ---------------------------------------------------------------------------


def test_master_on_enables_engine_subflags_except_optins() -> None:
    cfg = resolve_memory_config(env={MASTER_ENV_VAR: "1"}, config={})
    assert cfg.master_enabled is True
    # Engine activation sub-flags follow the master.
    assert cfg.write_readiness_enabled is True
    assert cfg.write_enabled is True
    assert cfg.recall_enabled is True
    assert cfg.projection_enabled is True
    assert cfg.compaction_enabled is True
    # The kill-switch is explicit-only: master-on must NOT engage it (writes
    # alive), so it stays OFF unless set.
    assert cfg.write_kill_switch_enabled is False
    # Documented opt-ins stay OFF even under master-on.
    assert cfg.soul_write_enabled is False
    assert cfg.vector_search is False


def test_kill_switch_engages_only_on_explicit_override() -> None:
    cfg = resolve_memory_config(
        env={MASTER_ENV_VAR: "1", "MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED": "1"},
        config={},
    )
    assert cfg.write_kill_switch_enabled is True


def test_master_on_via_config_toml_table() -> None:
    cfg = resolve_memory_config(env={}, config={"memory": {"enabled": True}})
    assert cfg.master_enabled is True
    assert cfg.write_enabled is True
    assert cfg.recall_enabled is True


# ---------------------------------------------------------------------------
# Precedence — explicit override > master default > hardcoded default
# ---------------------------------------------------------------------------


def test_explicit_env_override_beats_master_off() -> None:
    # Master is off, but an explicit recall-on env override wins.
    cfg = resolve_memory_config(
        env={"MAGI_MEMORY_RECALL_ENABLED": "1"},
        config={},
    )
    assert cfg.master_enabled is False
    assert cfg.recall_enabled is True
    # Untouched sub-flags follow the (off) master.
    assert cfg.write_enabled is False


def test_explicit_env_override_beats_master_on() -> None:
    # Master on, but an explicit write-off env override wins.
    cfg = resolve_memory_config(
        env={MASTER_ENV_VAR: "1", "MAGI_MEMORY_WRITE_ENABLED": "0"},
        config={},
    )
    assert cfg.master_enabled is True
    assert cfg.write_enabled is False
    # Other sub-flags still follow the master.
    assert cfg.recall_enabled is True


def test_soul_write_opt_in_can_be_explicitly_enabled() -> None:
    cfg = resolve_memory_config(
        env={MASTER_ENV_VAR: "1", "MAGI_SOUL_WRITE_ENABLED": "1"},
        config={},
    )
    assert cfg.soul_write_enabled is True


def test_vector_search_opt_in_can_be_explicitly_enabled() -> None:
    cfg = resolve_memory_config(
        env={MASTER_ENV_VAR: "1", "MAGI_MEMORY_VECTOR_SEARCH": "1"},
        config={},
    )
    assert cfg.vector_search is True


def test_config_override_beats_master_but_env_beats_config() -> None:
    # config table flips recall on; env flips it back off — env wins.
    cfg = resolve_memory_config(
        env={"MAGI_MEMORY_RECALL_ENABLED": "0"},
        config={"memory": {"recall_enabled": True}},
    )
    assert cfg.recall_enabled is False
    # config-only override (no env) takes effect.
    cfg2 = resolve_memory_config(
        env={},
        config={"memory": {"projection_enabled": True}},
    )
    assert cfg2.projection_enabled is True


def test_tunable_overrides_resolve() -> None:
    cfg = resolve_memory_config(
        env={"MAGI_MEMORY_DAILY_THRESHOLD": "111", "MAGI_MEMORY_COOLDOWN_HOURS": "6"},
        config={"memory": {"root_max_tokens": 1234}},
    )
    assert cfg.daily_threshold == 111
    assert cfg.cooldown_hours == 6
    assert cfg.root_max_tokens == 1234


# ---------------------------------------------------------------------------
# Permanently-frozen authority fields — DB + ADK-memory writes stay Literal[False]
# ---------------------------------------------------------------------------


def _literal_false_only(annotation: object) -> bool:
    return get_args(annotation) == (False,)


def test_db_and_adk_write_fields_remain_literal_false_on_harness_configs() -> None:
    from magi_agent.harness.memory_compaction import MemoryCompactionHarnessConfig
    from magi_agent.harness.memory_write import MemoryWriteHarnessConfig

    for model in (MemoryWriteHarnessConfig, MemoryCompactionHarnessConfig):
        db_anno = model.model_fields["database_mutation_allowed"].annotation
        adk_anno = model.model_fields["adk_memory_service_write_enabled"].annotation
        assert _literal_false_only(db_anno), f"{model.__name__}.database_mutation_allowed must stay Literal[False]"
        assert _literal_false_only(adk_anno), f"{model.__name__}.adk_memory_service_write_enabled must stay Literal[False]"


def test_db_and_adk_write_fields_coerce_forged_true_to_false() -> None:
    from magi_agent.harness.memory_compaction import MemoryCompactionHarnessConfig
    from magi_agent.harness.memory_write import MemoryWriteHarnessConfig

    write_cfg = MemoryWriteHarnessConfig.model_validate(
        {"databaseMutationAllowed": True, "adkMemoryServiceWriteEnabled": True}
    )
    compaction_cfg = MemoryCompactionHarnessConfig.model_validate(
        {"databaseMutationAllowed": True, "adkMemoryServiceWriteEnabled": True}
    )
    assert write_cfg.database_mutation_allowed is False
    assert write_cfg.adk_memory_service_write_enabled is False
    assert compaction_cfg.database_mutation_allowed is False
    assert compaction_cfg.adk_memory_service_write_enabled is False


def test_invalid_mode_falls_back_to_normal() -> None:
    # An invalid mode value (env or config) must clamp to the default rather
    # than tripping the MemoryMode Literal — the happy path is covered above.
    cfg_env = resolve_memory_config(env={"MAGI_MEMORY_MODE": "turbo"}, config={})
    assert cfg_env.mode == "normal"
    cfg_cfg = resolve_memory_config(env={}, config={"memory": {"mode": "turbo"}})
    assert cfg_cfg.mode == "normal"


# ---------------------------------------------------------------------------
# Fail-soft int tunables — out-of-range clamps to default, never raises
# ---------------------------------------------------------------------------


def test_out_of_range_int_clamps_to_default_without_raising() -> None:
    # An in-range-parseable but out-of-bounds value (below the field minimum)
    # gets the same forgiveness as a malformed string: clamp-to-default instead
    # of raising a pydantic ValidationError out of resolve_memory_config().
    cfg = resolve_memory_config(
        env={"MAGI_MEMORY_DAILY_THRESHOLD": "-5"},
        config={},
    )
    assert cfg.daily_threshold == 200  # hardcoded default, no raise
    # Zero also trips the ge=1 constraint → default.
    cfg_zero = resolve_memory_config(
        env={"MAGI_MEMORY_DAILY_THRESHOLD": "0"},
        config={},
    )
    assert cfg_zero.daily_threshold == 200
    # cooldown_hours has ge=0, so 0 is valid and must be preserved.
    cfg_cooldown = resolve_memory_config(
        env={"MAGI_MEMORY_COOLDOWN_HOURS": "0"},
        config={},
    )
    assert cfg_cooldown.cooldown_hours == 0
    # Out-of-range via the config table is also clamped.
    cfg_table = resolve_memory_config(
        env={},
        config={"memory": {"recall_k": -1}},
    )
    assert cfg_table.recall_k == 8
