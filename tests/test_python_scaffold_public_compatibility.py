from __future__ import annotations

import importlib


def test_recipe_compiler_exports_pack_digest_builder() -> None:
    compiler = importlib.import_module("magi_agent.recipes.compiler")

    assert callable(compiler.build_recipe_pack_digest)


def test_runtime_events_exports_normalized_event_contract() -> None:
    events = importlib.import_module("magi_agent.runtime.events")

    assert hasattr(events, "NormalizedEvent")
    assert hasattr(events, "normalized_events_to_agent_events")
    assert hasattr(events, "normalized_events_to_transcript")


def test_runtime_issuance_support_imports_without_tests_pythonpath() -> None:
    support = importlib.import_module("runtime_issuance_support")

    assert callable(support.issue_test_runtime_authority)
