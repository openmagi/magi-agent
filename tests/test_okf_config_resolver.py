"""PR1 — OKF knowledge config resolver: cascade, precedence, hermetic env.

Mirrors ``tests/test_memory_config_resolver.py``.  The resolver is the single
source of truth for OKF activation:

  * Master OFF (default) → lookup follows it OFF; index-inject stays OFF.
  * Master ON → lookup follows ON, EXCEPT ``index_inject`` which is an opt-in
    that stays False even under master-on.
  * Explicit env/config override beats the master default, which beats the
    hardcoded default.

Every test passes an explicit ``env=`` dict — never reads ``os.environ`` —
so the suite is hermetic regardless of any ``MAGI_*`` exports in the shell.
"""
from __future__ import annotations

import pydantic
import pytest

from magi_agent.knowledge.okf.config import (
    MASTER_ENV_VAR,
    MAX_DOC_BYTES,
    OkfConfig,
    resolve_okf_config,
)


# ---------------------------------------------------------------------------
# Default matrix — master OFF
# ---------------------------------------------------------------------------


def test_master_defaults_off_when_nothing_set() -> None:
    cfg = resolve_okf_config(env={}, config={})
    assert cfg.master_enabled is False
    assert cfg.lookup_enabled is False
    assert cfg.index_inject_enabled is False
    assert cfg.bundle_paths == ()


def test_default_tunables_are_stable() -> None:
    cfg = resolve_okf_config(env={}, config={})
    assert cfg.max_records == 8
    assert cfg.max_preview_chars == 2000
    assert cfg.max_docs == 500
    assert cfg.max_total_bytes == 33554432


def test_config_is_frozen_immutable() -> None:
    cfg = resolve_okf_config(env={}, config={})
    with pytest.raises(pydantic.ValidationError):
        cfg.lookup_enabled = True  # type: ignore[misc]


def test_max_doc_bytes_constant_is_256kb() -> None:
    assert MAX_DOC_BYTES == 262144


# ---------------------------------------------------------------------------
# Cascade — master ON enables lookup, index-inject stays opt-in
# ---------------------------------------------------------------------------


def test_master_on_enables_lookup_but_not_index_inject() -> None:
    cfg = resolve_okf_config(env={MASTER_ENV_VAR: "1"}, config={})
    assert cfg.master_enabled is True
    assert cfg.lookup_enabled is True
    # index-inject is opt-in even under master-on.
    assert cfg.index_inject_enabled is False


def test_master_on_via_config_toml_table() -> None:
    cfg = resolve_okf_config(env={}, config={"knowledge_okf": {"enabled": True}})
    assert cfg.master_enabled is True
    assert cfg.lookup_enabled is True


def test_index_inject_opt_in_can_be_explicitly_enabled() -> None:
    cfg = resolve_okf_config(
        env={MASTER_ENV_VAR: "1", "MAGI_KNOWLEDGE_OKF_INDEX_INJECT_ENABLED": "1"},
        config={},
    )
    assert cfg.index_inject_enabled is True


# ---------------------------------------------------------------------------
# Precedence — explicit override > master default > hardcoded default
# ---------------------------------------------------------------------------


def test_explicit_lookup_override_beats_master_off() -> None:
    cfg = resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED": "1"},
        config={},
    )
    assert cfg.master_enabled is False
    assert cfg.lookup_enabled is True


def test_explicit_lookup_override_beats_master_on() -> None:
    cfg = resolve_okf_config(
        env={MASTER_ENV_VAR: "1", "MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED": "0"},
        config={},
    )
    assert cfg.master_enabled is True
    assert cfg.lookup_enabled is False


def test_config_override_beats_master_but_env_beats_config() -> None:
    cfg = resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED": "0"},
        config={"knowledge_okf": {"lookup_enabled": True}},
    )
    assert cfg.lookup_enabled is False
    cfg2 = resolve_okf_config(
        env={},
        config={"knowledge_okf": {"index_inject_enabled": True}},
    )
    assert cfg2.index_inject_enabled is True


# ---------------------------------------------------------------------------
# Bundle paths — colon-separated → tuple
# ---------------------------------------------------------------------------


def test_bundle_paths_split_on_colon() -> None:
    cfg = resolve_okf_config(
        env={"MAGI_OKF_BUNDLE_PATHS": "/a/bundle:/b/other:/c"},
        config={},
    )
    assert cfg.bundle_paths == ("/a/bundle", "/b/other", "/c")


def test_bundle_paths_empty_when_unset() -> None:
    cfg = resolve_okf_config(env={}, config={})
    assert cfg.bundle_paths == ()


def test_bundle_paths_skip_blank_segments() -> None:
    cfg = resolve_okf_config(
        env={"MAGI_OKF_BUNDLE_PATHS": "/a::/b:"},
        config={},
    )
    assert cfg.bundle_paths == ("/a", "/b")


# ---------------------------------------------------------------------------
# Int clamping + fail-soft fallback
# ---------------------------------------------------------------------------


def test_int_overrides_resolve() -> None:
    cfg = resolve_okf_config(
        env={
            "MAGI_KNOWLEDGE_OKF_MAX_RECORDS": "5",
            "MAGI_KNOWLEDGE_OKF_MAX_PREVIEW_CHARS": "1000",
        },
        config={"knowledge_okf": {"max_docs": 42}},
    )
    assert cfg.max_records == 5
    assert cfg.max_preview_chars == 1000
    assert cfg.max_docs == 42


def test_max_records_clamps_to_bounds() -> None:
    # Below minimum (1) → default.
    assert resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_MAX_RECORDS": "0"}, config={}
    ).max_records == 8
    # Above maximum (20) → default.
    assert resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_MAX_RECORDS": "999"}, config={}
    ).max_records == 8
    # In range preserved.
    assert resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_MAX_RECORDS": "20"}, config={}
    ).max_records == 20


def test_max_preview_chars_clamps_to_bounds() -> None:
    # Zero is valid (lower bound is 0).
    assert resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_MAX_PREVIEW_CHARS": "0"}, config={}
    ).max_preview_chars == 0
    # Above maximum (8000) → default.
    assert resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_MAX_PREVIEW_CHARS": "99999"}, config={}
    ).max_preview_chars == 2000


def test_bad_int_falls_back_to_default_without_raising() -> None:
    cfg = resolve_okf_config(
        env={
            "MAGI_KNOWLEDGE_OKF_MAX_RECORDS": "not-a-number",
            "MAGI_KNOWLEDGE_OKF_MAX_DOCS": "abc",
        },
        config={},
    )
    assert cfg.max_records == 8
    assert cfg.max_docs == 500


def test_max_docs_and_total_bytes_floor_at_one() -> None:
    # ge=1: zero/negative → default.
    assert resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_MAX_DOCS": "0"}, config={}
    ).max_docs == 500
    assert resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_MAX_TOTAL_BYTES": "-1"}, config={}
    ).max_total_bytes == 33554432


def test_can_construct_okf_config_directly() -> None:
    # The model stores already-resolved values; direct construction works for
    # callers that build it without the resolver (e.g. loader tests).
    cfg = OkfConfig(maxRecords=3, maxDocs=2)
    assert cfg.max_records == 3
    assert cfg.max_docs == 2
