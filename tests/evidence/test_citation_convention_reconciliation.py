"""Fix 1: exactly one citation convention ships on every prompt-assembly path.

MAGI_SOURCE_CITATION_ENABLED is profile-default-ON. When it is on, the runtime
requires the registry-backed ``[src_N]`` convention (stable ids for dedup +
verification), so the legacy markdown-link ``<citation-convention>`` block must
NOT also ship. When it is off, the markdown block ships and the ``[src_N]`` block
does not. These tests lock the invariant on BOTH the direct
``build_system_prompt`` path and the ``build_cli_instruction`` (CLI/serve) path,
plus the cache-splitting ``build_system_prompt_blocks`` variant.

Invariant, on EACH path:
- flag ON:  ``<source_citation>`` present, ``<citation-convention>`` absent.
- flag OFF: ``<citation-convention>`` present, ``<source_citation>`` absent.
"""
from __future__ import annotations


def _assert_exactly_one(text: str, *, flag_on: bool) -> None:
    if flag_on:
        assert "<source_citation>" in text
        assert "<citation-convention>" not in text
    else:
        assert "<citation-convention>" in text
        assert "<source_citation>" not in text


# ---------------------------------------------------------------------------
# build_system_prompt (direct string path)
# ---------------------------------------------------------------------------


def test_build_system_prompt_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.runtime.message_builder import build_system_prompt

    prompt = build_system_prompt(session_key="s1", turn_id="t1")
    _assert_exactly_one(prompt, flag_on=True)


def test_build_system_prompt_flag_off(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    from magi_agent.runtime.message_builder import build_system_prompt

    prompt = build_system_prompt(session_key="s1", turn_id="t1")
    _assert_exactly_one(prompt, flag_on=False)


def test_build_system_prompt_env_param_overrides_ambient(monkeypatch) -> None:
    # An explicit env argument is authoritative over the process environment.
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.runtime.message_builder import build_system_prompt

    prompt = build_system_prompt(
        session_key="s1", turn_id="t1", env={"MAGI_SOURCE_CITATION_ENABLED": "0"}
    )
    _assert_exactly_one(prompt, flag_on=False)


# ---------------------------------------------------------------------------
# build_system_prompt_blocks (cache-split path, symmetric with the above)
# ---------------------------------------------------------------------------


def test_build_system_prompt_blocks_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.runtime.message_builder import build_system_prompt_blocks

    blocks = build_system_prompt_blocks(session_key="s1", turn_id="t1")
    joined = "\n".join(str(block.get("text", "")) for block in blocks)
    _assert_exactly_one(joined, flag_on=True)


def test_build_system_prompt_blocks_flag_off(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    from magi_agent.runtime.message_builder import build_system_prompt_blocks

    blocks = build_system_prompt_blocks(session_key="s1", turn_id="t1")
    joined = "\n".join(str(block.get("text", "")) for block in blocks)
    _assert_exactly_one(joined, flag_on=False)


# ---------------------------------------------------------------------------
# build_cli_instruction (the real CLI + serve runtime path)
# ---------------------------------------------------------------------------


def test_build_cli_instruction_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s1")
    _assert_exactly_one(prompt, flag_on=True)
    # Exactly one <source_citation> block (the CLI path no longer double-appends).
    assert prompt.count("<source_citation>") == 1


def test_build_cli_instruction_flag_off(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s1")
    _assert_exactly_one(prompt, flag_on=False)
