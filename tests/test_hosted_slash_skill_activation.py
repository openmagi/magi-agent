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


# --- PR-X: realistic-body scan exemption (the 2026-07-11 canary incident) ---

_REALISTIC_SKILL_BODY = """---
name: stock-multibagger-screening
kind: prompt
permission: meta
---

# Screening skill v9.0

Calibration ledger: /workspace/workspace/multibagger-ledger/CALIBRATION.md
Reference: https://example.com/receipts and see /workspace/skills for peers.
Example config (illustrative, not a secret): api_key: YOUR_KEY_HERE
Step 1: gather fundamentals via DART filings.
"""


def test_realistic_body_with_urls_paths_and_key_prose_activates(tmp_path: Path) -> None:
    """The exact failure shape from the live canary: a real SKILL.md carrying
    URLs, absolute /workspace paths, and api_key-shaped prose must activate,
    not 422 the whole request via the private-material scan."""
    _write_skill(tmp_path, "custom-stock-multibagger-screening", _REALISTIC_SKILL_BODY)
    text = "/custom-stock-multibagger-screening 스킬에 대해 설명해줘"
    on = _build(text, enabled=True, root=tmp_path)
    activated = on.turn.activated_skill
    assert activated is not None
    assert activated.miss is False
    assert "https://example.com/receipts" in activated.body
    assert "/workspace/workspace/multibagger-ledger" in activated.body
    # The residual user request still rides the sanitized user channel.
    assert "스킬에 대해 설명해줘" in on.turn.sanitized_current_turn_text


def test_unsafe_text_outside_the_body_still_rejected(tmp_path: Path) -> None:
    """Only the body is exempt: the same forbidden text in sourcePath must
    still be rejected by the activation validator."""
    import pytest
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ActivatedSkill,
    )

    with pytest.raises(ValueError):
        Gate5B4C3ActivatedSkill.model_validate(
            {
                "skillName": "x",
                "invokedToken": "x",
                "sourcePath": "/workspace/skills/x/SKILL.md",
                "source": "workspace",
                "body": "ok",
                "bodyDigest": "sha256:" + "9" * 64,
                "truncated": False,
                "miss": False,
                "nearMatches": [],
            }
        )


def test_non_skill_mapping_with_body_key_still_scanned() -> None:
    """The exemption is shape-gated: an arbitrary mapping that merely has a
    'body' key cannot smuggle forbidden material past the scan."""
    import pytest
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        _reject_unsafe_value,
    )

    with pytest.raises(ValueError):
        _reject_unsafe_value({"body": "see /workspace/secrets", "note": "x"})


def test_contract_rejection_fails_open_to_plain_turn(tmp_path: Path, monkeypatch) -> None:
    """If the activation payload fails contract validation, the turn proceeds
    WITHOUT activation instead of raising out of the request build."""
    _write_skill(tmp_path, "custom-stock-multibagger-screening", _SKILL_BODY)

    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ActivatedSkill,
    )

    def _boom(cls, *_a: object, **_k: object) -> object:
        raise ValueError("contract says no")

    monkeypatch.setattr(
        Gate5B4C3ActivatedSkill, "model_validate", classmethod(_boom)
    )
    on = _build("/custom-stock-multibagger-screening hi", enabled=True, root=tmp_path)
    assert on.turn.activated_skill is None
