"""PR2 — production cheap-model compaction summarizer (fail-open to truncation).

Hermetic + deterministic: every test injects a fake model (an async-generator
ADK-contract double) or relies on the no-key fall-open path. No real network, no
real provider, no system clock.

Coverage:
  * a tier over threshold is COMPRESSED by an injected fake summarizer (the
    summary marker is present; it is not a deterministic truncation)
  * the summarizer raising / no model resolvable => the tree falls open to a
    deterministic truncation and NEVER raises
  * flag OFF (compaction disabled) => the summarizer is never constructed / used
  * empty model output => raises (so the tree falls open) rather than writing an
    empty tier
  * the input text is already redacted by the caller (the runtime does not
    un-redact and forwards exactly what it is given)
  * MAGI_MEMORY_SUMMARIZER_MODEL override is honoured when present
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from magi_agent.memory.compaction_tree import CompactionTree, Summarizer
from magi_agent.memory.config import MemoryRuntimeConfig
from magi_agent.memory.summarizer_runtime import (
    CheapModelSummarizer,
    build_compaction_summarizer,
)


# ---------------------------------------------------------------------------
# ADK model contract doubles
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts


class _FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.content = _FakeContent([_FakePart(text)])


class _FakeModel:
    """Async-generator model double mirroring the ADK ``generate_content_async``
    contract used by ``readonly_classifier._invoke_llm``."""

    def __init__(self, *, chunks: list[str], model: str = "fake/cheap") -> None:
        self._chunks = chunks
        self.model = model
        self.requests: list[object] = []

    async def generate_content_async(self, llm_request, stream: bool = False):  # noqa: ARG002
        self.requests.append(llm_request)
        for chunk in self._chunks:
            yield _FakeResponse(chunk)


class _RaisingModel:
    def __init__(self, *, model: str = "fake/cheap") -> None:
        self.model = model

    async def generate_content_async(self, llm_request, stream: bool = False):  # noqa: ARG002
        raise RuntimeError("model exploded")
        yield  # pragma: no cover - make this an async generator


# ---------------------------------------------------------------------------
# Config + tier helpers (mirror tests/test_memory_compaction_tree.py)
# ---------------------------------------------------------------------------


def _config(**overrides) -> MemoryRuntimeConfig:
    base: dict[str, object] = {
        "compactionEnabled": True,
        "dailyThreshold": 5,
        "weeklyThreshold": 8,
        "monthlyThreshold": 12,
        "rootMaxTokens": 200,
        "cooldownHours": 24,
    }
    base.update(overrides)
    return MemoryRuntimeConfig(**base)


def _write_daily(memory: Path, day: str, *, lines: int) -> Path:
    daily = memory / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    path = daily / f"{day}.md"
    body = "\n".join(f"- entry {day} line {i}" for i in range(lines))
    path.write_text(body + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (a) over-threshold tier gets compressed by the runtime summarizer
# ---------------------------------------------------------------------------


def test_cheap_summarizer_is_a_protocol_instance() -> None:
    summ = CheapModelSummarizer(model_factory=lambda: _FakeModel(chunks=["x"]))
    assert isinstance(summ, Summarizer)


def test_cheap_summarizer_compresses_text() -> None:
    summ = CheapModelSummarizer(
        model_factory=lambda: _FakeModel(chunks=["MODEL ", "SUMMARY"])
    )
    out = summ.summarize("line one\nline two\nline three")
    assert out == "MODEL SUMMARY"


def test_tier_over_threshold_is_compressed_not_truncated(tmp_path: Path) -> None:
    """The injected runtime summarizer compresses an over-threshold daily tier;
    the written summary carries the model marker (it is NOT a truncation of the
    raw lines)."""
    memory = tmp_path / "memory"
    # A PRIOR day (today's file is left open, never summarized in place).
    # 20 lines, daily_threshold=5 -> over threshold -> summarized.
    _write_daily(memory, "2026-06-05", lines=20)
    summ = CheapModelSummarizer(
        model_factory=lambda: _FakeModel(chunks=["[[MODEL-SUMMARY]] condensed daily"])
    )
    tree = CompactionTree(memory, _config(), summarizer=summ)
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True
    assert result.summarized_count >= 1
    assert result.summarizer_failures == 0
    daily_text = (memory / "daily" / "2026-06-05.md").read_text(encoding="utf-8")
    assert "[[MODEL-SUMMARY]]" in daily_text
    # The raw enumerated lines were replaced by the summary, not merely truncated.
    assert "- entry 2026-06-05 line 19" not in daily_text


# ---------------------------------------------------------------------------
# (b) summarizer raising / no model => tree falls open to truncation, never raises
# ---------------------------------------------------------------------------


def test_summarize_raises_when_model_raises() -> None:
    summ = CheapModelSummarizer(model_factory=lambda: _RaisingModel())
    with pytest.raises(Exception):
        summ.summarize("a\nb\nc")


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "FIREWORKS_API_KEY",
        "OPENROUTER_API_KEY",
        "MAGI_PROVIDER",
        "MAGI_MODEL",
        "MAGI_MEMORY_SUMMARIZER_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "nonexistent-config.toml"))


def test_summarize_raises_when_no_model_resolvable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No factory and no provider config -> nothing to call -> raise so the tree
    # falls open. (Verified with NO api key configured.)
    _clear_provider_env(monkeypatch, tmp_path)
    summ = CheapModelSummarizer()
    with pytest.raises(Exception):
        summ.summarize("a\nb\nc")


def test_summarize_raises_on_empty_model_output() -> None:
    summ = CheapModelSummarizer(model_factory=lambda: _FakeModel(chunks=["   "]))
    with pytest.raises(Exception):
        summ.summarize("a\nb\nc")


def test_tree_falls_open_to_truncation_when_summarizer_raises(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-06-05", lines=20)
    summ = CheapModelSummarizer(model_factory=lambda: _RaisingModel())
    tree = CompactionTree(memory, _config(), summarizer=summ)
    # Must NOT raise — the tree catches the summarizer error and truncates.
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True
    assert result.summarizer_failures >= 1
    daily_text = (memory / "daily" / "2026-06-05.md").read_text(encoding="utf-8")
    # Truncation keeps real (raw) lines; no model marker.
    assert "- entry 2026-06-05 line 0" in daily_text


def test_no_key_summarizer_falls_open_in_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end with NO api key: build_compaction_summarizer yields a summarizer
    whose every call raises (no provider) so the tree truncates and never raises."""
    _clear_provider_env(monkeypatch, tmp_path)

    summ = build_compaction_summarizer(_config())
    assert summ is not None
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-06-05", lines=20)
    tree = CompactionTree(memory, _config(), summarizer=summ)
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True
    assert result.summarizer_failures >= 1
    daily_text = (memory / "daily" / "2026-06-05.md").read_text(encoding="utf-8")
    assert "- entry 2026-06-05 line 0" in daily_text


# ---------------------------------------------------------------------------
# (c) flag OFF => the summarizer is not constructed / not used
# ---------------------------------------------------------------------------


def test_build_summarizer_returns_none_when_compaction_disabled() -> None:
    assert build_compaction_summarizer(_config(compactionEnabled=False)) is None


def test_build_summarizer_returns_summarizer_when_enabled() -> None:
    summ = build_compaction_summarizer(_config(compactionEnabled=True))
    assert isinstance(summ, CheapModelSummarizer)


# ---------------------------------------------------------------------------
# Input is already redacted: the runtime forwards it verbatim (does not un-redact)
# ---------------------------------------------------------------------------


def test_summarize_forwards_redacted_input_verbatim() -> None:
    captured: list[str] = []

    class _CapturingModel(_FakeModel):
        async def generate_content_async(self, llm_request, stream: bool = False):  # noqa: ARG002
            # Pull the user text out of the ADK request and record it.
            for content in llm_request.contents:
                for part in content.parts:
                    if getattr(part, "text", None):
                        captured.append(part.text)
            for chunk in self._chunks:
                yield _FakeResponse(chunk)

    summ = CheapModelSummarizer(
        model_factory=lambda: _CapturingModel(chunks=["ok"])
    )
    redacted = "user said [REDACTED] then asked a question"
    summ.summarize(redacted)
    joined = "\n".join(captured)
    assert redacted in joined
    # The runtime never reconstructs a secret; the literal redaction token survives.
    assert "[REDACTED]" in joined


# ---------------------------------------------------------------------------
# MAGI_MEMORY_SUMMARIZER_MODEL override threading (factory path bypasses it, so
# assert the env var name is the documented override and is read by the builder)
# ---------------------------------------------------------------------------


def test_summarizer_model_override_env_is_honoured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When a real provider config IS resolvable, the override model id flows into
    the built model. We assert via a stubbed provider+builder seam."""
    monkeypatch.setenv("MAGI_MEMORY_SUMMARIZER_MODEL", "anthropic/claude-cheap-test")

    built_models: list[str] = []

    from magi_agent.cli.providers import ProviderConfig

    def _fake_resolve(*, model_override=None, env=None, config=None):  # noqa: ARG001
        # The builder must pass the override through as model_override.
        assert model_override == "anthropic/claude-cheap-test"
        return ProviderConfig(
            provider="anthropic", model="claude-cheap-test", api_key="k"
        )

    def _fake_build(config, env=None):  # noqa: ARG001
        built_models.append(config.model)
        return _FakeModel(chunks=["ok"], model=config.model)

    monkeypatch.setattr(
        "magi_agent.memory.summarizer_runtime.resolve_provider_config", _fake_resolve
    )
    monkeypatch.setattr(
        "magi_agent.memory.summarizer_runtime._build_litellm_model", _fake_build
    )

    summ = build_compaction_summarizer(_config())
    assert isinstance(summ, CheapModelSummarizer)
    out = summ.summarize("a\nb\nc")
    assert out == "ok"
    assert built_models == ["claude-cheap-test"]
