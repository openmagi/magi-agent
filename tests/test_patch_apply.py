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
