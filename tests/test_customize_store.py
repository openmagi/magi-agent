import json
from pathlib import Path

from magi_agent.customize.store import DEFAULT_OVERRIDES, customize_path, load_overrides


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
        "hooks": {},
        "custom_rules": [],
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
