"""Guards docs/sessions.md against compaction overstatement (PR 17-PR2, E5-a/B7).

Two factual doc gaps motivated these checks:

- The page claimed older turns are *summarized* and that the runtime
  *triggers compaction when the context approaches the model's limit*
  (present tense, overstated). There are in fact two compaction stacks in the
  code:

    * ``magi_agent.context.auto_compact.AutoCompactionEngine`` — a full
      *LLM-based summary* path that has **no live caller on the model loop**
      (deferred / optional).
    * ``magi_agent.adk_bridge.context_compaction`` — a *tail-keep truncation*
      plugin wired into the live control plane and **default-ON** in the
      local-full / hosted-full profiles via
      ``MAGI_CONTEXT_COMPACTION_ENABLED``.

  So the compaction that actually runs keeps the recent tail and drops older
  turns — it does **not** summarize them. The doc must say that honestly and
  name the live flag.

- The page described session state as persisted to "the workspace PVC". PVC is
  a hosted-Kubernetes concept with no meaning for the OSS CLI/serve runtime, so
  the bare "PVC" reference is a context leak and is dropped.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = ROOT / "docs" / "sessions.md"


def _text() -> str:
    return SESSIONS.read_text(encoding="utf-8")


def test_compaction_is_not_described_as_summarization() -> None:
    text = _text()
    # The live compaction is tail-keep truncation, not summarization.
    assert "older turns are summarized" not in text
    assert "compacts older turns into a summary" not in text


def test_compaction_describes_tail_keep_truncation() -> None:
    text = _text().lower()
    assert "tail" in text
    assert "truncat" in text


def test_live_compaction_flag_is_named() -> None:
    assert "MAGI_CONTEXT_COMPACTION_ENABLED" in _text()


def test_llm_summary_path_is_marked_deferred_not_live() -> None:
    text = _text()
    # The summary engine must be described as the deferred/optional path,
    # distinct from the truncation that actually runs.
    assert "auto_compact" in text or "AutoCompactionEngine" in text
    assert "summary" in text.lower()


def test_present_tense_overstatement_is_removed() -> None:
    text = _text()
    # The unqualified present-tense claim that the runtime triggers compaction
    # whenever the context approaches the model limit overstated a dormant path.
    assert "triggers compaction when the context approaches the model's limit" not in text


def test_bare_pvc_persistence_reference_is_dropped() -> None:
    text = _text()
    assert "workspace PVC" not in text
