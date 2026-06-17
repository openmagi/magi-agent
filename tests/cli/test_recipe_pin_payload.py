"""Task 4: payload reader for user-explicit recipe pin.

``_pinned_recipe_pack_ids_from_payload`` reads ``pinnedRecipePackIds``
(camelCase) or ``pinned_recipe_pack_ids`` (snake_case) from the request
payload and returns a tuple of non-empty strings. Validation is downstream in
``normalize_pinned_recipe_pack_ids``; the reader is a thin string filter only.
"""
from __future__ import annotations

from magi_agent.transport.chat_routes import _pinned_recipe_pack_ids_from_payload


def test_pinned_ids_from_payload_camel_and_snake() -> None:
    assert _pinned_recipe_pack_ids_from_payload(
        {"pinnedRecipePackIds": ["openmagi.dev-coding"]}
    ) == ("openmagi.dev-coding",)
    assert _pinned_recipe_pack_ids_from_payload(
        {"pinned_recipe_pack_ids": ["x"]}
    ) == ("x",)
    assert _pinned_recipe_pack_ids_from_payload({}) == ()
    assert _pinned_recipe_pack_ids_from_payload(
        {"pinnedRecipePackIds": "nope"}
    ) == ()


def test_pinned_ids_from_payload_non_mapping_returns_empty() -> None:
    assert _pinned_recipe_pack_ids_from_payload(None) == ()
    assert _pinned_recipe_pack_ids_from_payload([]) == ()
    assert _pinned_recipe_pack_ids_from_payload("string") == ()


def test_pinned_ids_from_payload_filters_non_strings_and_empty() -> None:
    assert _pinned_recipe_pack_ids_from_payload(
        {"pinnedRecipePackIds": ["valid", "", 42, None, "also-valid"]}
    ) == ("valid", "also-valid")


def test_pinned_ids_from_payload_camel_takes_priority_over_snake() -> None:
    result = _pinned_recipe_pack_ids_from_payload(
        {
            "pinnedRecipePackIds": ["camel-pack"],
            "pinned_recipe_pack_ids": ["snake-pack"],
        }
    )
    assert result == ("camel-pack",)
