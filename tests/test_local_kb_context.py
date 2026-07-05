"""Local KB_CONTEXT turn resolver — self-host parity with chat-proxy kb-context.

The hosted chat-proxy (`infra/docker/chat-proxy/kb-context.js`) parses the
`[KB_CONTEXT: id=filename]` marker the dashboard prepends, downloads the
converted document text, inlines it into the user turn wrapped as
`<current-turn-source authority="L1">`, and strips the marker. The local runtime
had no equivalent, so attached-file content never reached the agent. These tests
pin the local resolver's behaviour.
"""

from __future__ import annotations

from pathlib import Path

from magi_agent.transport.kb_context import apply_kb_context


def _apply(prompt: str, root: Path) -> str:
    return apply_kb_context(prompt, workspace_root=root, bot_id="local-bot")


def test_no_marker_returns_prompt_unchanged(tmp_path: Path) -> None:
    assert _apply("just a question", tmp_path) == "just a question"


def test_inlines_text_file_and_strips_marker(tmp_path: Path) -> None:
    doc = tmp_path / "knowledge" / "Downloads" / "notes.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Revenue\nQ1 was strong.", encoding="utf-8")

    prompt = "[KB_CONTEXT: knowledge/Downloads/notes.md=notes.md]\nSummarize this."
    out = _apply(prompt, tmp_path)

    assert "[KB_CONTEXT:" not in out
    assert "Summarize this." in out
    assert "Q1 was strong." in out
    assert '<current-turn-source kind="knowledge" authority="L1">' in out
    assert "[file: notes.md]" in out


def test_missing_file_is_fail_soft_note_not_raise(tmp_path: Path) -> None:
    prompt = "[KB_CONTEXT: knowledge/Downloads/gone.pdf=gone.pdf]\nRead it."
    out = _apply(prompt, tmp_path)
    assert "[KB_CONTEXT:" not in out
    assert "Read it." in out
    assert "gone.pdf" in out  # the note still names the file
    # No content, but the turn survived.
    assert '<current-turn-source' in out


def test_traversal_id_is_refused_no_content_leak(tmp_path: Path) -> None:
    secret = tmp_path.parent / "outside-secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    prompt = "[KB_CONTEXT: ../outside-secret.txt=outside-secret.txt]\nhi"
    out = _apply(prompt, tmp_path)
    assert "TOP SECRET" not in out
    assert "hi" in out


def test_empty_user_text_still_injects_content(tmp_path: Path) -> None:
    doc = tmp_path / "knowledge" / "Downloads" / "a.txt"
    doc.parent.mkdir(parents=True)
    doc.write_text("hello world", encoding="utf-8")
    prompt = "[KB_CONTEXT: knowledge/Downloads/a.txt=a.txt]"
    out = _apply(prompt, tmp_path)
    assert "hello world" in out
    assert "[KB_CONTEXT:" not in out


def test_multiple_refs_all_inlined(tmp_path: Path) -> None:
    base = tmp_path / "knowledge" / "Downloads"
    base.mkdir(parents=True)
    (base / "one.txt").write_text("first doc", encoding="utf-8")
    (base / "two.txt").write_text("second doc", encoding="utf-8")
    prompt = (
        "[KB_CONTEXT: knowledge/Downloads/one.txt=one.txt, "
        "knowledge/Downloads/two.txt=two.txt]\nCompare."
    )
    out = _apply(prompt, tmp_path)
    assert "first doc" in out
    assert "second doc" in out
    assert "Compare." in out
