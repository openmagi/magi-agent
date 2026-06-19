"""A-2 static meta-test: no live direct-ADK web tool append site remains.

The fast direct web tools (``web_search``/``web_fetch``/``research_fact``) must
flow through ``ToolDispatcher`` via registry manifests, never be appended as bare
``FunctionTool`` objects OUTSIDE the dispatcher. Direct appends bypass the URL
policy, egress proxy, live-gate accounting, receipts, and redaction ladder.

This meta-test is exhaustive: it greps ``magi_agent/cli`` and
``magi_agent/transport`` for every known direct-append shape and asserts ZERO
live append/loop site remains. The only permitted occurrence of
``build_web_search_tools`` is the deprecated shim definition in
``magi_agent/tools/web_search_tools.py`` (which now returns ``[]``).
"""
from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _python_sources(*relative_dirs: str) -> list[Path]:
    root = _repo_root()
    files: list[Path] = []
    for rel in relative_dirs:
        base = root / "magi_agent" / rel
        if base.exists():
            files.extend(base.rglob("*.py"))
    return files


def test_no_live_build_web_search_tools_append_site() -> None:
    """No CLI/transport module appends bare web FunctionTools outside the dispatcher."""
    offenders: list[str] = []
    for path in _python_sources("cli", "transport"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Any live reference to the deprecated builder in cli/transport is an
            # append/loop site (the only legitimate definition lives in
            # tools/web_search_tools.py, which is not scanned here).
            if "build_web_search_tools" in stripped:
                offenders.append(f"{path}:{lineno}: {stripped}")
            # Bare FunctionTool wrappers around the raw web fns are also forbidden.
            if "FunctionTool(web_search" in stripped or "FunctionTool(web_fetch" in stripped:
                offenders.append(f"{path}:{lineno}: {stripped}")
    assert not offenders, "live direct-web append site(s) remain:\n" + "\n".join(offenders)


def test_direct_web_builder_is_deprecated_empty_shim() -> None:
    """``build_web_search_tools()`` is a deprecated no-op returning ``[]``."""
    import os

    from magi_agent.tools.web_search_tools import build_web_search_tools

    # Even with provider keys present, the shim returns nothing — capability now
    # flows through the dispatcher-backed registry manifests instead.
    os.environ.setdefault("BRAVE_API_KEY", "k1")
    os.environ.setdefault("FIRECRAWL_API_KEY", "k2")
    assert build_web_search_tools() == []
