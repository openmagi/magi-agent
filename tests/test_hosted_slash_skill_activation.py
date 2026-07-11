"""A3 - hosted slash-to-skill activation wiring in _build_user_visible_generation_request.

Flag OFF => request byte-identical (no activatedSkill). Flag ON + a message
beginning with an installed skill name => turn.activated_skill is populated
from the on-disk SKILL.md; the user message is never modified. Resolver errors
fail open (turn proceeds without activation).

Fixtures mirror test_chat_generation_request_image_wiring.py.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.transport.chat import (
    _build_user_visible_generation_request,  # type: ignore[attr-defined]
)

from tests.test_chat_generation_request_image_wiring import (
    _make_generation_config,
    _make_route_config,
    _make_runtime,
)

_SKILL_BODY = """---
name: stock-multibagger-screening
kind: prompt
permission: meta
---

# Screening skill
Step 1: gather fundamentals.
"""


def _write_skill(root: Path, dir_name: str, body: str) -> None:
    d = root / "skills" / dir_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


def _payload(text: str) -> dict[str, object]:
    return {"messages": [{"role": "user", "content": text}]}


def _build(text: str, *, enabled: bool, root: Path | None = None):
    return _build_user_visible_generation_request(
        runtime=_make_runtime(),
        route_config=_make_route_config(),
        generation_config=_make_generation_config(),
        payload=_payload(text),
        trace_id=None,
        slash_skill_activation_enabled=enabled,
        slash_skill_workspace_root=root,
        slash_skill_body_max_chars=32000,
    )


def test_flag_off_leaves_activation_unset(tmp_path: Path) -> None:
    _write_skill(tmp_path, "custom-stock-multibagger-screening", _SKILL_BODY)
    text = "/custom-stock-multibagger-screening 스킬에 대해 설명해줘"
    off = _build(text, enabled=False, root=tmp_path)
    assert off.turn.activated_skill is None


def test_flag_on_incident_message_activates_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "custom-stock-multibagger-screening", _SKILL_BODY)
    text = "/custom-stock-multibagger-screening 스킬에 대해 설명해줘"
    on = _build(text, enabled=True, root=tmp_path)
    activated = on.turn.activated_skill
    assert activated is not None
    assert activated.miss is False
    # Directory typed with the custom- prefix resolves to the frontmatter name.
    assert activated.skill_name == "stock-multibagger-screening"
    assert "custom-stock-multibagger-screening" in activated.source_path
    assert "Step 1: gather fundamentals." in activated.body
    # The user message is never modified: the residual request survives in the
    # sanitized current-turn text.
    assert "스킬에 대해 설명해줘" in on.turn.sanitized_current_turn_text


def test_flag_on_unknown_skill_is_a_miss(tmp_path: Path) -> None:
    _write_skill(tmp_path, "custom-stock-multibagger-screening", _SKILL_BODY)
    on = _build("/custom-stock-multibagger-screning explain", enabled=True, root=tmp_path)
    activated = on.turn.activated_skill
    assert activated is not None
    assert activated.miss is True
    assert activated.body == ""


def test_flag_on_non_slash_message_is_unset(tmp_path: Path) -> None:
    _write_skill(tmp_path, "custom-stock-multibagger-screening", _SKILL_BODY)
    on = _build("please explain multibagger screening", enabled=True, root=tmp_path)
    assert on.turn.activated_skill is None


def test_flag_on_reserved_word_is_unset(tmp_path: Path) -> None:
    on = _build("/help me with this", enabled=True, root=tmp_path)
    assert on.turn.activated_skill is None


def test_resolver_failure_fails_open(tmp_path: Path, monkeypatch) -> None:
    # If the resolver itself raises, activation must fail open (None) rather
    # than break the turn.
    _write_skill(tmp_path, "custom-stock-multibagger-screening", _SKILL_BODY)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("resolver blew up")

    monkeypatch.setattr(
        "magi_agent.transport.generation_request.resolve_skill_slash", _boom
    )
    on = _build("/custom-stock-multibagger-screening hi", enabled=True, root=tmp_path)
    assert on.turn.activated_skill is None
