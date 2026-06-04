from __future__ import annotations

import pytest

from magi_agent.coding.patch_apply import (
    FileChange,
    PatchApplyError,
    PatchParseError,
    apply_patch,
    derive_new_contents,
    parse_patch_envelope,
    plan_patch,
)


class FakeFs:
    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files: dict[str, str] = dict(files or {})

    def read(self, relative_path: str) -> str:
        return self.files[relative_path]

    def exists(self, relative_path: str) -> bool:
        return relative_path in self.files

    def write(self, relative_path: str, content: str) -> None:
        self.files[relative_path] = content

    def delete(self, relative_path: str) -> None:
        self.files.pop(relative_path, None)


# ---------------------------------------------------------------------------
# Envelope parsing
# ---------------------------------------------------------------------------


def test_parse_add_file():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: src/new.py\n"
        "+print('hi')\n"
        "+x = 1\n"
        "*** End Patch\n"
    )
    files = parse_patch_envelope(patch)
    assert len(files) == 1
    assert files[0].kind == "add"
    assert files[0].path == "src/new.py"
    assert files[0].add_lines == ("print('hi')", "x = 1")


def test_parse_delete_file():
    patch = "*** Begin Patch\n*** Delete File: old.py\n*** End Patch\n"
    files = parse_patch_envelope(patch)
    assert files[0].kind == "delete"
    assert files[0].path == "old.py"


def test_parse_update_file_with_hunk():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "@@ def f():\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
        "*** End Patch\n"
    )
    files = parse_patch_envelope(patch)
    assert files[0].kind == "update"
    assert files[0].move_to is None
    assert len(files[0].hunks) == 1
    kinds = [line.kind for line in files[0].hunks[0].lines]
    assert kinds == ["context", "remove", "add"]


def test_parse_move_file():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "*** Move to: b.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )
    files = parse_patch_envelope(patch)
    assert files[0].kind == "move"
    assert files[0].path == "a.py"
    assert files[0].move_to == "b.py"


def test_parse_multi_file():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: new.py\n"
        "+a\n"
        "*** Delete File: gone.py\n"
        "*** Update File: edit.py\n"
        "@@\n"
        "-x\n"
        "+y\n"
        "*** End Patch\n"
    )
    files = parse_patch_envelope(patch)
    assert [f.kind for f in files] == ["add", "delete", "update"]


@pytest.mark.parametrize(
    "patch,reason",
    [
        ("nonsense", "missing_begin_patch"),
        ("*** Begin Patch\n*** Add File: x\n+a\n", "missing_end_patch"),
        ("*** Begin Patch\n*** End Patch\n", "no_file_operations"),
        ("*** Begin Patch\n*** Add File: \n+a\n*** End Patch\n", "add_file_missing_path"),
        ("*** Begin Patch\nrandom line\n*** End Patch\n", "unexpected_envelope_line"),
        (
            "*** Begin Patch\n*** Update File: a.py\n*** End Patch\n",
            "update_file_has_no_hunks",
        ),
        ("", "empty_patch"),
    ],
)
def test_parse_malformed(patch, reason):
    with pytest.raises(PatchParseError) as exc:
        parse_patch_envelope(patch)
    assert exc.value.args[0] == reason


def test_parse_duplicate_file_op_rejected():
    patch = (
        "*** Begin Patch\n"
        "*** Delete File: a.py\n"
        "*** Delete File: a.py\n"
        "*** End Patch\n"
    )
    with pytest.raises(PatchParseError) as exc:
        parse_patch_envelope(patch)
    assert exc.value.args[0] == "duplicate_file_op"


# ---------------------------------------------------------------------------
# 4-pass matcher / derive_new_contents
# ---------------------------------------------------------------------------


def test_derive_exact_match():
    original = "line1\nline2\nline3\n"
    (hunk,) = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: a\n@@\n line1\n-line2\n+LINE2\n line3\n*** End Patch\n"
    )[0].hunks
    assert derive_new_contents(original, [hunk]) == "line1\nLINE2\nline3\n"


def test_derive_trim_end_pass():
    # File has trailing whitespace the patch does not.
    original = "alpha  \nbeta\n"
    (hunk,) = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: a\n@@\n-alpha\n+ALPHA\n beta\n*** End Patch\n"
    )[0].hunks
    assert derive_new_contents(original, [hunk]) == "ALPHA\nbeta\n"


def test_derive_full_trim_pass():
    # File has leading indentation the patch lacks.
    original = "    indented\nnext\n"
    (hunk,) = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: a\n@@\n-indented\n+done\n next\n*** End Patch\n"
    )[0].hunks
    assert derive_new_contents(original, [hunk]) == "done\nnext\n"


def test_derive_unicode_normalize_pass():
    # File uses smart quotes + em dash + ellipsis; patch uses ASCII.
    original = "say “hello” — done…\nkeep\n"
    (hunk,) = parse_patch_envelope(
        '*** Begin Patch\n*** Update File: a\n@@\n-say "hello" - done...\n+changed\n keep\n*** End Patch\n'
    )[0].hunks
    assert derive_new_contents(original, [hunk]) == "changed\nkeep\n"


def test_derive_eof_anchor_pass():
    original = "head\nmid\ntail\n"
    (hunk,) = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: a\n@@\n tail\n+appended\n*** End of File\n*** End Patch\n"
    )[0].hunks
    result = derive_new_contents(original, [hunk])
    assert result == "head\nmid\ntail\nappended\n"


def test_derive_context_not_found():
    (hunk,) = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: a\n@@\n-missing\n+x\n*** End Patch\n"
    )[0].hunks
    with pytest.raises(PatchApplyError) as exc:
        derive_new_contents("totally\ndifferent\n", [hunk])
    assert exc.value.reason == "hunk_context_not_found"


# ---------------------------------------------------------------------------
# Verify-then-apply (atomicity)
# ---------------------------------------------------------------------------


def test_apply_multi_file_success():
    fs = FakeFs({"old.py": "gone\n", "edit.py": "x\nkeep\n"})
    patch = (
        "*** Begin Patch\n"
        "*** Add File: new.py\n"
        "+created\n"
        "*** Delete File: old.py\n"
        "*** Update File: edit.py\n"
        "@@\n"
        "-x\n"
        "+y\n"
        " keep\n"
        "*** End Patch\n"
    )
    changes = apply_patch(patch, fs)
    assert {c.kind for c in changes} == {"add", "delete", "update"}
    assert fs.files["new.py"] == "created\n"
    assert "old.py" not in fs.files
    assert fs.files["edit.py"] == "y\nkeep\n"


def test_apply_move():
    fs = FakeFs({"a.py": "old\nkeep\n"})
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "*** Move to: b.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        " keep\n"
        "*** End Patch\n"
    )
    apply_patch(patch, fs)
    assert "a.py" not in fs.files
    assert fs.files["b.py"] == "new\nkeep\n"


def test_apply_atomic_no_partial_writes_on_failure():
    fs = FakeFs({"edit.py": "x\n"})
    # Add succeeds in isolation, but the update hunk won't match -> nothing applied.
    patch = (
        "*** Begin Patch\n"
        "*** Add File: new.py\n"
        "+created\n"
        "*** Update File: edit.py\n"
        "@@\n"
        "-DOES_NOT_EXIST\n"
        "+y\n"
        "*** End Patch\n"
    )
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(patch, fs)
    assert exc.value.path == "edit.py"
    assert exc.value.reason == "hunk_context_not_found"
    # Critical: the Add must NOT have been written.
    assert "new.py" not in fs.files
    assert fs.files == {"edit.py": "x\n"}


def test_plan_add_existing_rejected():
    fs = FakeFs({"dup.py": "exists\n"})
    files = parse_patch_envelope(
        "*** Begin Patch\n*** Add File: dup.py\n+x\n*** End Patch\n"
    )
    with pytest.raises(PatchApplyError) as exc:
        plan_patch(files, fs)
    assert exc.value.reason == "add_target_exists"


def test_plan_delete_missing_rejected():
    fs = FakeFs({})
    files = parse_patch_envelope(
        "*** Begin Patch\n*** Delete File: nope.py\n*** End Patch\n"
    )
    with pytest.raises(PatchApplyError) as exc:
        plan_patch(files, fs)
    assert exc.value.reason == "delete_target_missing"


def test_plan_returns_filechange_objects():
    fs = FakeFs({})
    files = parse_patch_envelope(
        "*** Begin Patch\n*** Add File: new.py\n+a\n*** End Patch\n"
    )
    changes = plan_patch(files, fs)
    assert isinstance(changes[0], FileChange)
    # plan must not mutate the fs.
    assert "new.py" not in fs.files


# ---------------------------------------------------------------------------
# Virtual-existence coherence (intra-patch op interaction)
# ---------------------------------------------------------------------------


def test_move_dest_collides_with_add():
    # Move a.py -> b.py AND Add b.py. Both target b.py; the move claims it
    # first virtually so the add must conflict (NOT silently clobber).
    fs = FakeFs({"a.py": "old\nkeep\n"})
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "*** Move to: b.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        " keep\n"
        "*** Add File: b.py\n"
        "+collide\n"
        "*** End Patch\n"
    )
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(patch, fs)
    assert exc.value.reason == "add_target_exists"
    assert exc.value.path == "b.py"
    # Atomic: nothing applied.
    assert fs.files == {"a.py": "old\nkeep\n"}


# NOTE: same-path interactions (delete-then-add, add-then-delete, add-then-add)
# are rejected by the parser's ``duplicate_file_op`` guard before they reach
# plan_patch, so they're exercised here at the plan_patch level with hand-built
# ops to verify the virtual-existence model is coherent for the supported set.


def test_delete_then_add_same_path_is_coherent():
    # Delete a.py then Add a.py: the delete frees the path virtually so the
    # add is allowed and produces the new content.
    from magi_agent.coding.patch_apply import PatchFile

    fs = FakeFs({"a.py": "old\n"})
    ops = (
        PatchFile(kind="delete", path="a.py"),
        PatchFile(kind="add", path="a.py", add_lines=("fresh",)),
    )
    changes = plan_patch(ops, fs)
    assert [c.kind for c in changes] == ["delete", "add"]
    assert changes[1].new_content == "fresh\n"


def test_add_then_delete_same_path_is_coherent():
    # Add new.py then Delete new.py: add creates it virtually so the delete
    # is allowed (does not surface delete_target_missing against live FS).
    from magi_agent.coding.patch_apply import PatchFile

    fs = FakeFs({})
    ops = (
        PatchFile(kind="add", path="new.py", add_lines=("temp",)),
        PatchFile(kind="delete", path="new.py"),
    )
    changes = plan_patch(ops, fs)
    assert [c.kind for c in changes] == ["add", "delete"]


def test_add_then_add_same_path_conflicts():
    # Two adds of the same path: first claims it virtually, second conflicts.
    from magi_agent.coding.patch_apply import PatchFile

    fs = FakeFs({})
    dup = (
        PatchFile(kind="add", path="x.py", add_lines=("a",)),
        PatchFile(kind="add", path="x.py", add_lines=("b",)),
    )
    with pytest.raises(PatchApplyError) as exc:
        plan_patch(dup, fs)
    assert exc.value.reason == "add_target_exists"


# ---------------------------------------------------------------------------
# Unanchored insertion handling
# ---------------------------------------------------------------------------


def test_leading_pure_insertion_accepted():
    # A context-free insertion at the very start of the file is unambiguous.
    original = "first\nsecond\n"
    (hunk,) = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: a\n@@\n+header\n*** End Patch\n"
    )[0].hunks
    assert derive_new_contents(original, [hunk]) == "header\nfirst\nsecond\n"


def test_eof_pure_insertion_accepted():
    # A context-free insertion anchored to EOF is unambiguous.
    original = "first\nsecond\n"
    (hunk,) = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: a\n@@\n+footer\n*** End of File\n*** End Patch\n"
    )[0].hunks
    assert derive_new_contents(original, [hunk]) == "first\nsecond\nfooter\n"


def test_midfile_pure_insertion_rejected():
    # A pure-insertion hunk that lands after an earlier anchored hunk (cursor
    # advanced past 0) and is not EOF-anchored has no anchor -> reject.
    original = "alpha\nbeta\ngamma\n"
    hunks = parse_patch_envelope(
        "*** Begin Patch\n"
        "*** Update File: a\n"
        "@@\n"
        " alpha\n"
        "+x\n"  # anchored hunk advances cursor past start
        "@@\n"
        "+orphan\n"  # bare insertion, mid-file, no EOF marker -> ambiguous
        "*** End Patch\n"
    )[0].hunks
    with pytest.raises(PatchApplyError) as exc:
        derive_new_contents(original, hunks)
    assert exc.value.reason == "insertion_without_context"


# ---------------------------------------------------------------------------
# CRLF line endings (documents current behavior)
# ---------------------------------------------------------------------------


def test_crlf_file_current_behavior_documented():
    # derive_new_contents splits on "\n" only, so a CRLF file keeps the "\r"
    # as a suffix on each line. The matcher's exact pass therefore won't match
    # a patch context line lacking the "\r"; the rstrip/full-trim passes do.
    # This test documents that current behavior: a context line written
    # without "\r" still matches via the trim passes, and the surrounding
    # unmodified lines RETAIN their original "\r" (no rewrite of untouched
    # lines).
    original = "alpha\r\nbeta\r\ngamma\r\n"
    (hunk,) = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: a\n@@\n-beta\n+BETA\n*** End Patch\n"
    )[0].hunks
    result = derive_new_contents(original, [hunk])
    # The matched/replaced line is emitted WITHOUT "\r" (patch-supplied),
    # while untouched alpha/gamma keep their original "\r" -> mixed endings.
    assert result == "alpha\r\nBETA\ngamma\r\n"


# ---------------------------------------------------------------------------
# Intra-patch virtual content: move-then-update-dest (MEDIUM fix)
# ---------------------------------------------------------------------------


def test_move_then_update_dest_uses_moved_content():
    # Move a.py -> b.py (editing content in the move hunk), then Update b.py
    # (editing the moved content further).  The update must see the content
    # produced by the move, NOT the stale on-disk b.py (which doesn't exist).
    fs = FakeFs({"a.py": "alpha\nbeta\ngamma\n"})
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "*** Move to: b.py\n"
        "@@\n"
        "-alpha\n"
        "+ALPHA\n"
        " beta\n"
        " gamma\n"
        "*** Update File: b.py\n"
        "@@\n"
        " ALPHA\n"
        "-beta\n"
        "+BETA\n"
        " gamma\n"
        "*** End Patch\n"
    )
    changes = apply_patch(patch, fs)
    assert "a.py" not in fs.files
    # After move: b.py = "ALPHA\nbeta\ngamma\n"
    # After update b.py: b.py = "ALPHA\nBETA\ngamma\n"
    assert fs.files["b.py"] == "ALPHA\nBETA\ngamma\n"
    assert [c.kind for c in changes] == ["move", "update"]


def test_update_then_update_same_path_uses_first_update_content():
    # Two consecutive updates to the same path: the second must see the content
    # produced by the first (via virtual_content), not the stale on-disk file.
    # (Parser rejects duplicate source paths, so use plan_patch directly.)
    from magi_agent.coding.patch_apply import PatchFile, PatchHunk, PatchHunkLine

    fs = FakeFs({"x.py": "line1\nline2\n"})
    hunk1 = PatchHunk(
        context_header="",
        lines=(
            PatchHunkLine(kind="remove", text="line1"),
            PatchHunkLine(kind="add", text="LINE1"),
            PatchHunkLine(kind="context", text="line2"),
        ),
    )
    hunk2 = PatchHunk(
        context_header="",
        lines=(
            PatchHunkLine(kind="context", text="LINE1"),
            PatchHunkLine(kind="remove", text="line2"),
            PatchHunkLine(kind="add", text="LINE2"),
        ),
    )
    ops = (
        PatchFile(kind="update", path="x.py", hunks=(hunk1,)),
        PatchFile(kind="update", path="x.py", hunks=(hunk2,)),
    )
    changes = plan_patch(ops, fs)
    assert changes[0].new_content == "LINE1\nline2\n"
    assert changes[1].new_content == "LINE1\nLINE2\n"


# ---------------------------------------------------------------------------
# Leading-insertion double-prepend prevention (LOW 1)
# ---------------------------------------------------------------------------


def test_double_leading_insertion_rejected():
    # Two consecutive pure-insertion hunks at cursor 0 (no context/EOF anchor).
    # The first is accepted; the second must be rejected as
    # ``insertion_without_context`` rather than silently prepending again.
    original = "body\n"
    hunks = parse_patch_envelope(
        "*** Begin Patch\n"
        "*** Update File: a\n"
        "@@\n"
        "+first_prepend\n"
        "@@\n"
        "+second_prepend\n"
        "*** End Patch\n"
    )[0].hunks
    with pytest.raises(PatchApplyError) as exc:
        derive_new_contents(original, hunks)
    assert exc.value.reason == "insertion_without_context"


def test_leading_insertion_then_context_hunk_still_works():
    # A leading insertion followed by a normal context-anchored hunk must still
    # work: the flag must not prevent the context hunk from matching.
    original = "alpha\nbeta\n"
    hunks = parse_patch_envelope(
        "*** Begin Patch\n"
        "*** Update File: a\n"
        "@@\n"
        "+header\n"
        "@@\n"
        " alpha\n"
        "-beta\n"
        "+BETA\n"
        "*** End Patch\n"
    )[0].hunks
    result = derive_new_contents(original, hunks)
    assert result == "header\nalpha\nBETA\n"


# ---------------------------------------------------------------------------
# plan_patch unit-level reason assertions (LOW 2)
# ---------------------------------------------------------------------------


def test_update_missing_raises_update_target_missing():
    # Verify that plan_patch raises the precise reason code, not a generic one.
    fs = FakeFs({})
    files = parse_patch_envelope(
        "*** Begin Patch\n*** Update File: missing.py\n@@\n-x\n+y\n*** End Patch\n"
    )
    with pytest.raises(PatchApplyError) as exc:
        plan_patch(files, fs)
    assert exc.value.reason == "update_target_missing"
    assert exc.value.path == "missing.py"


def test_delete_missing_raises_delete_target_missing():
    # Verify that plan_patch raises the precise reason code, not a generic one.
    fs = FakeFs({})
    files = parse_patch_envelope(
        "*** Begin Patch\n*** Delete File: missing.py\n*** End Patch\n"
    )
    with pytest.raises(PatchApplyError) as exc:
        plan_patch(files, fs)
    assert exc.value.reason == "delete_target_missing"
    assert exc.value.path == "missing.py"
