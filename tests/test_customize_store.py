import json
from pathlib import Path

from magi_agent.customize.store import (
    DEFAULT_OVERRIDES,
    customize_path,
    load_overrides,
    set_user_rules,
    set_verification_override,
)


def test_set_user_rules_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    set_user_rules("Always cite sources.", path=p)
    assert load_overrides(p)["user_rules"] == "Always cite sources."


def test_set_user_rules_caps_length(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    out = set_user_rules("x" * 50_000, path=p)
    assert len(out["user_rules"]) == 20_000


def test_set_preset_override_persists_explicit_bool_and_mode(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    out = set_verification_override(
        "harness_presets", "coding-verification", True, mode="deterministic", path=p
    )
    assert out["verification"]["preset_overrides"]["coding-verification"] is True
    assert out["verification"]["modes"]["coding-verification"] == "deterministic"
    reloaded = load_overrides(p)
    assert reloaded["verification"]["preset_overrides"]["coding-verification"] is True


def test_set_preset_override_disable_retains_explicit_false(tmp_path: Path) -> None:
    # opt-out of a default-on gate must PERSIST as explicit False (not removed).
    p = tmp_path / "customize.json"
    set_verification_override("harness_presets", "coding-verification", True, path=p)
    out = set_verification_override("harness_presets", "coding-verification", False, path=p)
    assert out["verification"]["preset_overrides"]["coding-verification"] is False
    assert "coding-verification" not in out["verification"]["modes"]


def test_set_verification_override_recipes_list(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    out = set_verification_override("recipes", "research", True, path=p)
    assert "research" in out["verification"]["recipes"]
    out = set_verification_override("recipes", "research", False, path=p)
    assert "research" not in out["verification"]["recipes"]


def test_set_verification_override_hooks_kind(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    out = set_verification_override("hooks", "beforeCommit", True, path=p)
    assert out["verification"]["hooks"]["beforeCommit"] is True


def test_missing_file_returns_default(tmp_path: Path) -> None:
    result = load_overrides(tmp_path / "nope.json")
    assert result == DEFAULT_OVERRIDES
    result["tools"]["x"] = True
    assert "x" not in DEFAULT_OVERRIDES["tools"]


def test_malformed_json_returns_default(tmp_path: Path) -> None:
    target = tmp_path / "customize.json"
    target.write_text("{not json", encoding="utf-8")
    assert load_overrides(target) == DEFAULT_OVERRIDES


def test_partial_file_is_shape_normalized(tmp_path: Path) -> None:
    target = tmp_path / "customize.json"
    target.write_text(json.dumps({"tools": {"web_fetch": False}}), encoding="utf-8")
    result = load_overrides(target)
    assert result["tools"] == {"web_fetch": False}
    assert result["verification"] == {
        "recipes": [],
        "harness_presets": [],
        "preset_overrides": {},
        "hooks": {},
        "modes": {},
        "custom_rules": [],
        "seam_specs": [],
        # PR-F7 (2026-06-23): additive — operator-authored cost budgets.
        "budgets": {},
    }


def test_customize_path_respects_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    assert customize_path() == tmp_path / "c.json"
    monkeypatch.delenv("MAGI_CUSTOMIZE", raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    assert customize_path() == tmp_path / "cfg" / "customize.json"


def test_save_then_load_roundtrip(tmp_path):
    from magi_agent.customize.store import load_overrides, save_overrides
    target = tmp_path / "customize.json"
    data = load_overrides(target)  # defaults
    data["tools"]["web_fetch"] = False
    save_overrides(data, target)
    assert target.exists()
    reloaded = load_overrides(target)
    assert reloaded["tools"] == {"web_fetch": False}


def test_save_is_atomic_no_partial_temp_left(tmp_path):
    from magi_agent.customize.store import DEFAULT_OVERRIDES, save_overrides
    target = tmp_path / "customize.json"
    save_overrides(DEFAULT_OVERRIDES, target)
    # no leftover *.tmp sibling
    assert list(tmp_path.glob("*.tmp")) == []


def test_set_tool_override_creates_and_updates(tmp_path):
    from magi_agent.customize.store import load_overrides, set_tool_override
    target = tmp_path / "customize.json"
    set_tool_override("shell", False, target)
    assert load_overrides(target)["tools"]["shell"] is False
    set_tool_override("shell", True, target)
    assert load_overrides(target)["tools"]["shell"] is True


def test_save_creates_parent_dir(tmp_path):
    from magi_agent.customize.store import DEFAULT_OVERRIDES, save_overrides
    target = tmp_path / "nested" / "dir" / "customize.json"
    save_overrides(DEFAULT_OVERRIDES, target)
    assert target.exists()


# ---------------------------------------------------------------------------
# PR-D4 / N-39: parse cache preserves write-then-query freshness + isolation.
# ---------------------------------------------------------------------------
def test_load_overrides_reparses_after_save(tmp_path: Path) -> None:
    from magi_agent.customize.store import save_overrides

    p = tmp_path / "customize.json"
    save_overrides({"user_rules": "first"}, path=p)
    assert load_overrides(p)["user_rules"] == "first"
    # a subsequent save (os.replace -> new inode/mtime) must invalidate cache
    save_overrides({"user_rules": "second"}, path=p)
    assert load_overrides(p)["user_rules"] == "second"


def test_load_overrides_result_is_caller_mutable(tmp_path: Path) -> None:
    from magi_agent.customize.store import save_overrides

    p = tmp_path / "customize.json"
    save_overrides({"user_rules": "keep"}, path=p)
    first = load_overrides(p)
    first["user_rules"] = "mutated locally"
    first["tools"]["injected"] = True
    # cache must not be polluted by caller mutation of a prior result
    second = load_overrides(p)
    assert second["user_rules"] == "keep"
    assert "injected" not in second["tools"]
