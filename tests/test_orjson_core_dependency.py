"""Regression guard: orjson must be a declared, importable core dependency.

Incident (0.1.132): the release bumped the unpinned ``litellm>=1.74.0`` to
1.92.0, whose non-proxy ``completion(tools=...)`` path lazily imports its MCP
tool handler, and that import chain reaches ``litellm.proxy`` and runs a bare
``import orjson``. litellm declares orjson only under its own ``proxy`` extra,
so a plain ``litellm`` install (the Homebrew ``pip install magi-agent`` and the
runtime Docker image, neither of which takes that extra) omitted it. Every
LiteLlm-routed provider (openai, gemini, fireworks/kimi) that passed tools then
failed with ``ModuleNotFoundError: No module named 'orjson'``, surfaced to the
user only as ``child_llm_collector_status_failed`` (and confabulated by the
model as a "provider route" problem). The anthropic path uses the native
cache-aware Claude model rather than litellm, so it never hit the import and
masked the gap.

Nothing in CI exercised the real litellm completion path (the child-runner
tests mock it), so the missing dependency shipped. These two guards would have
caught it: orjson must be importable in the installed environment, and it must
stay pinned as a core dependency so a plain install keeps shipping it.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_orjson_is_importable() -> None:
    import orjson  # noqa: F401


def test_orjson_pinned_as_core_dependency() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = pyproject["project"]["dependencies"]
    assert any(
        dep.replace(" ", "").lower().startswith("orjson") for dep in deps
    ), "orjson must be a core runtime dependency: litellm's completion-with-tools path imports it"
