"""Serve-path startup projection of persisted first-party policy overrides.

The ``magi-agent serve`` entry point mounts the ``/v1/app/customize`` PATCH
routes and serves the dashboard. Those routes project a toggle onto the LIVE
process, so a dashboard change works until the next restart. Before this fix the
serve path projected only the control-plane overlay at startup, so a persisted
builtin-policy opt-out or a source_citation gate-mode step-down silently reverted
to its default on the next boot ("persists but ignored on boot").

These tests pin the contract that the serve-path helper
``main._apply_persisted_customize_policy_overrides`` re-applies both persisted
seams from ``customize.json`` to a fresh env dict:

* a builtin-policy disable (``verify_before_replying``) reaches
  ``MAGI_VERIFY_BEFORE_REPLYING_ENABLED``,
* a citation gate-mode step-down (``audit`` / ``off``) reaches
  ``MAGI_SOURCE_CITATION_GATE_MODE``,
* ``MAGI_SOURCE_CITATION_ENABLED`` is NEVER touched (the boolean disable stays
  floored: capture / inline citations / Sources stay on in every mode),
* and the no-override path leaves the env byte-identical.

This also covers the pre-existing #1403 restart-revert: the boolean opt-out is an
overwrite projection, so it re-applies cleanly on boot.
"""

from __future__ import annotations

from magi_agent.customize.store import (
    set_builtin_policy_override,
    set_citation_gate_mode_override,
)
from magi_agent.main import _apply_persisted_customize_policy_overrides

VERIFY_ENV = "MAGI_VERIFY_BEFORE_REPLYING_ENABLED"
CITATION_ENABLED_ENV = "MAGI_SOURCE_CITATION_ENABLED"
CITATION_MODE_ENV = "MAGI_SOURCE_CITATION_GATE_MODE"


def _point_customize_at(monkeypatch, cfile) -> None:
    """Route the store's ``customize_path()`` at an isolated temp file."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))


def test_startup_projects_verify_disable_onto_its_flag(tmp_path, monkeypatch) -> None:
    cfile = tmp_path / "customize.json"
    _point_customize_at(monkeypatch, cfile)
    set_builtin_policy_override("verify_before_replying", False, cfile)

    env: dict[str, str] = {}
    _apply_persisted_customize_policy_overrides(env)

    assert env[VERIFY_ENV] == "0"
    # The citation boolean master flag is never touched by this seam.
    assert CITATION_ENABLED_ENV not in env


def test_startup_reenables_verify_over_a_stale_shell_export(
    tmp_path, monkeypatch
) -> None:
    # #1403 restart-revert regression guard: an overwrite projection must be able
    # to flip a prior 0 back to 1 on boot (a setdefault applier could not).
    cfile = tmp_path / "customize.json"
    _point_customize_at(monkeypatch, cfile)
    set_builtin_policy_override("verify_before_replying", True, cfile)

    env: dict[str, str] = {VERIFY_ENV: "0"}
    _apply_persisted_customize_policy_overrides(env)

    assert env[VERIFY_ENV] == "1"


def test_startup_projects_gate_mode_audit(tmp_path, monkeypatch) -> None:
    cfile = tmp_path / "customize.json"
    _point_customize_at(monkeypatch, cfile)
    set_citation_gate_mode_override("audit", cfile)

    env: dict[str, str] = {}
    _apply_persisted_customize_policy_overrides(env)

    assert env[CITATION_MODE_ENV] == "audit"
    # Gate-mode step-down never touches the citation ENABLED master flag.
    assert CITATION_ENABLED_ENV not in env


def test_startup_projects_gate_mode_off(tmp_path, monkeypatch) -> None:
    cfile = tmp_path / "customize.json"
    _point_customize_at(monkeypatch, cfile)
    set_citation_gate_mode_override("off", cfile)

    env: dict[str, str] = {}
    _apply_persisted_customize_policy_overrides(env)

    assert env[CITATION_MODE_ENV] == "off"
    assert CITATION_ENABLED_ENV not in env


def test_startup_projects_both_seams_together(tmp_path, monkeypatch) -> None:
    cfile = tmp_path / "customize.json"
    _point_customize_at(monkeypatch, cfile)
    set_builtin_policy_override("verify_before_replying", False, cfile)
    set_citation_gate_mode_override("audit", cfile)

    env: dict[str, str] = {}
    _apply_persisted_customize_policy_overrides(env)

    assert env[VERIFY_ENV] == "0"
    assert env[CITATION_MODE_ENV] == "audit"
    assert CITATION_ENABLED_ENV not in env


def test_startup_is_byte_identical_when_no_override(tmp_path, monkeypatch) -> None:
    # No customize file exists -> the projection must be a strict no-op: the env
    # dict is byte-identical before and after (default mode stays repair, verify
    # untouched, citation ENABLED untouched).
    cfile = tmp_path / "customize.json"
    _point_customize_at(monkeypatch, cfile)
    assert not cfile.exists()

    env: dict[str, str] = {"SOME_UNRELATED": "keepme"}
    before = dict(env)
    _apply_persisted_customize_policy_overrides(env)

    assert env == before
    assert VERIFY_ENV not in env
    assert CITATION_MODE_ENV not in env
    assert CITATION_ENABLED_ENV not in env


def test_startup_no_override_leaves_existing_env_untouched(
    tmp_path, monkeypatch
) -> None:
    # An empty customize file (all defaults) still leaves any pre-existing env
    # exactly as-is: absent overrides are tri-state, so nothing projects.
    cfile = tmp_path / "customize.json"
    _point_customize_at(monkeypatch, cfile)
    # Write a defaults-only file (no builtin_policies, citation_gate_mode=None).
    from magi_agent.customize.store import save_overrides

    save_overrides({}, cfile)

    env: dict[str, str] = {VERIFY_ENV: "1", CITATION_MODE_ENV: "repair"}
    before = dict(env)
    _apply_persisted_customize_policy_overrides(env)

    assert env == before
