"""E-1: ``builtin_catalog.json`` must ship in the installed wheel/bottle.

The catalog is loaded via ``importlib.resources`` so the JSON has to be
declared in ``[tool.setuptools.package-data]`` (mirrors the bundled-pack /
slash-command-template gates already in the repo).
"""

from __future__ import annotations

import importlib.resources as resources
import json


def test_builtin_catalog_resource_is_loadable() -> None:
    """``importlib.resources`` resolves the JSON from an installed layout."""
    files = resources.files("magi_agent.models")
    data = (files / "builtin_catalog.json").read_text(encoding="utf-8")
    payload = json.loads(data)
    assert isinstance(payload, dict)
    assert "records" in payload
    assert isinstance(payload["records"], list)
    assert payload["records"], "builtin_catalog.json has no records"


def test_builtin_catalog_records_carry_required_fields() -> None:
    files = resources.files("magi_agent.models")
    payload = json.loads(
        (files / "builtin_catalog.json").read_text(encoding="utf-8")
    )
    required = {
        "provider",
        "model",
        "label",
        "source",
        "tier",
        "capabilities",
        "context_window",
        "max_output_tokens",
        "litellm_prefix",
        "last_verified",
    }
    for record in payload["records"]:
        missing = required - record.keys()
        assert not missing, (
            f"record {record.get('provider', '?')}:{record.get('model', '?')} "
            f"missing fields {missing}"
        )


def test_builtin_catalog_provider_aliases_declared() -> None:
    files = resources.files("magi_agent.models")
    payload = json.loads(
        (files / "builtin_catalog.json").read_text(encoding="utf-8")
    )
    aliases = payload.get("provider_aliases", {})
    # The legacy registry duplicated each gemini record under both ``google``
    # and ``gemini``; the catalog declares the alias here.
    assert aliases.get("google") == "gemini"
