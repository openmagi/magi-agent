"""rem2/F4 (deep-review N-08): cli/providers -> engine/providers pure move.

Provider/key resolution moves into the engine kernel with a ``sys.modules``
self-alias shim at the old ``magi_agent.cli.providers`` path, so old and new
paths are the SAME module object (identity, private names, and monkeypatch
targets preserved).
"""

from __future__ import annotations


def test_engine_providers_module_exists() -> None:
    import magi_agent.engine.providers as new

    assert new is not None


def test_old_and_new_paths_are_same_module() -> None:
    import magi_agent.cli.providers as old
    import magi_agent.engine.providers as new

    assert old is new


def test_key_symbols_identity() -> None:
    import magi_agent.cli.providers as old
    import magi_agent.engine.providers as new

    for name in (
        "ProviderConfig",
        "resolve_provider_config",
        "SUPPORTED_PROVIDERS",
        "UnknownProviderError",
        "_infer_provider_for_model",
    ):
        assert getattr(old, name) is getattr(new, name)


def test_supported_providers_frozen() -> None:
    from magi_agent.engine.providers import SUPPORTED_PROVIDERS

    assert SUPPORTED_PROVIDERS == (
        "anthropic",
        "openai",
        "gemini",
        "fireworks",
        "openrouter",
    )
