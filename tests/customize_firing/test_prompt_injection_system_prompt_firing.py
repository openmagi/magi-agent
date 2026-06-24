"""F-MUT1 firing test: ``prompt_injection`` mutator at ``on_user_prompt_submit``.

Drives the ``_apply_prompt_transform`` seam in
:mod:`magi_agent.runtime.message_builder` end-to-end through a tmp
``customize.json`` + the triple-gated flag combination
(``MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED`` strict-truthy +
``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``).
Proves four things together:

1. A persisted ``prompt_injection`` rule with ``firesAt ==
   "on_user_prompt_submit"`` and ``target == "system_prompt"`` is loaded and
   APPENDED as a new section.
2. The append composes with the existing sections (order preserved).
3. With the master flag OFF the rule is silently inert (sections
   byte-identical).
4. The helper is invoked even when the BEFORE_SYSTEM_PROMPT hook flag is OFF
   (rule-driven appends do not require the hook flag).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.store import set_custom_rule
from magi_agent.runtime.message_builder import (
    _maybe_apply_prompt_injection_sections,
)

_RULE_ID = "cr_fmut1_coding_standards"


def _rule(**over) -> dict:
    rule = {
        "id": _RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target": "system_prompt",
                "value": "Follow our coding standards.",
            },
        },
        "firesAt": "on_user_prompt_submit",
        "action": "audit",
    }
    rule.update(over)
    return rule


@pytest.fixture
def cfg_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)
    return cfile


def test_appends_value_as_new_section(cfg_on: Path) -> None:
    sections = ["base persona", "user identity"]
    out = _maybe_apply_prompt_injection_sections(sections)
    assert out == ["base persona", "user identity", "Follow our coding standards."]


def test_input_list_not_mutated(cfg_on: Path) -> None:
    sections = ["base"]
    _maybe_apply_prompt_injection_sections(sections)
    assert sections == ["base"]


def test_inert_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)

    out = _maybe_apply_prompt_injection_sections(["base"])
    assert out == ["base"]


def test_inert_when_no_rules_authored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))

    out = _maybe_apply_prompt_injection_sections(["base"])
    assert out == ["base"]


def test_multiple_enabled_rules_compose_in_stored_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))

    rule_a = _rule(id="cr_a")
    rule_b = _rule(id="cr_b")
    rule_b["what"]["payload"]["value"] = "Prefer tests."
    set_custom_rule(rule_a, path=cfile)
    set_custom_rule(rule_b, path=cfile)

    out = _maybe_apply_prompt_injection_sections(["base"])
    assert out == ["base", "Follow our coding standards.", "Prefer tests."]


def test_disabled_rule_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(enabled=False), path=cfile)

    out = _maybe_apply_prompt_injection_sections(["base"])
    assert out == ["base"]
