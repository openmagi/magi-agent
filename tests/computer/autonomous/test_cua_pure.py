import pytest

from magi_agent.computer.autonomous.cua_pure import (
    UIElement,
    action_to_cua_call,
    parse_action,
    parse_window_state,
)

_SAMPLE = """\
# Safari — Start Page
[element_index 1] AXButton "Back"
[element_index 2] AXTextField "Address"
some non-element line
[element_index 3] AXLink "Privacy Report"
"""


def test_parse_window_state_extracts_elements() -> None:
    els = parse_window_state(_SAMPLE)
    assert els == [
        UIElement(index=1, role="AXButton", label="Back"),
        UIElement(index=2, role="AXTextField", label="Address"),
        UIElement(index=3, role="AXLink", label="Privacy Report"),
    ]


def test_parse_window_state_empty() -> None:
    assert parse_window_state("no tagged lines here") == []


def test_parse_action_extracts_first_json() -> None:
    text = 'Reasoning...\n```json\n{"action": "click", "element_index": 2}\n```'
    assert parse_action(text) == {"action": "click", "element_index": 2}


def test_parse_action_rejects_no_json() -> None:
    with pytest.raises(ValueError):
        parse_action("I cannot find any element")


def test_parse_action_requires_action_key() -> None:
    with pytest.raises(ValueError):
        parse_action('{"element_index": 2}')


def test_action_click_by_element_index() -> None:
    name, args = action_to_cua_call(
        {"action": "click", "element_index": 2}, pid=42, window_id=7
    )
    assert name == "click"
    assert args == {"pid": 42, "window_id": 7, "element_index": 2}


def test_action_type_text() -> None:
    name, args = action_to_cua_call(
        {"action": "type", "text": "hello", "element_index": 2}, pid=42, window_id=7
    )
    assert name == "type_text"
    assert args == {"pid": 42, "window_id": 7, "text": "hello", "element_index": 2}


def test_action_key_maps_to_hotkey() -> None:
    name, args = action_to_cua_call(
        {"action": "key", "keys": ["cmd", "c"]}, pid=42, window_id=7
    )
    assert name == "hotkey"
    assert args == {"pid": 42, "keys": ["cmd", "c"], "window_id": 7}


def test_action_scroll() -> None:
    name, args = action_to_cua_call(
        {"action": "scroll", "direction": "down", "amount": 3}, pid=42, window_id=7
    )
    assert name == "scroll"
    assert args == {"pid": 42, "direction": "down", "amount": 3, "window_id": 7}


def test_unknown_action_raises() -> None:
    with pytest.raises(ValueError):
        action_to_cua_call({"action": "teleport"}, pid=1, window_id=1)
