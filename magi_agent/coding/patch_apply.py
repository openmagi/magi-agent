"""Codex-style multi-file envelope patch parser + 4-pass fuzzy matcher.

This module is intentionally self-contained and PURE where possible:

* ``parse_patch_envelope`` turns an ``*** Begin Patch`` / ``*** End Patch``
  envelope into typed :class:`PatchFile` operations (Add/Delete/Update/Move).
* ``derive_new_contents`` applies an Update file's hunks to existing file text
  using a 4-pass seek (exact -> rstrip/trim-end -> full trim -> unicode
  normalize) with context-anchor and EOF-anchor handling.
* ``plan_patch`` / ``apply_patch`` separate verify-then-apply so that
  multi-file patches are atomic: every operation is verified against a read
  snapshot first, and only if all verify does any IO occur.

The only impure surface is ``apply_patch`` (it reads + writes the workspace via
an injected ``Filesystem`` protocol). Parsing and matching never touch IO, so
they remain trivially testable and import-boundary safe.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


PatchOpKind = Literal["add", "delete", "update", "move"]


class PatchParseError(ValueError):
    """Raised when the envelope grammar is malformed."""


class PatchApplyError(ValueError):
    """Raised when a verified-and-applied patch cannot be realized.

    Carries a per-file ``reason`` so callers can surface a clear message.
    """

    def __init__(self, reason: str, *, path: str | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(reason if path is None else f"{path}: {reason}")


_BEGIN_PATCH = "*** Begin Patch"
_END_PATCH = "*** End Patch"
_ADD_FILE = "*** Add File: "
_DELETE_FILE = "*** Delete File: "
_UPDATE_FILE = "*** Update File: "
_MOVE_TO = "*** Move to: "
_HUNK_MARKER = "@@"
_END_OF_FILE = "*** End of File"

# Unicode normalization map applied in the 4th matcher pass.
_SMART_QUOTE_MAP = {
    "‘": "'",  # left single
    "’": "'",  # right single
    "‚": "'",  # single low-9
    "‛": "'",  # single high-reversed-9
    "“": '"',  # left double
    "”": '"',  # right double
    "„": '"',  # double low-9
    "‟": '"',  # double high-reversed-9
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "…": "...",  # ellipsis
    " ": " ",  # non-breaking space
}


def _normalize_unicode(text: str) -> str:
    out = text
    for source, target in _SMART_QUOTE_MAP.items():
        if source in out:
            out = out.replace(source, target)
    return unicodedata.normalize("NFKC", out)


@dataclass(frozen=True)
class PatchHunkLine:
    """A single ``@@`` hunk line: context (' '), removal ('-') or add ('+')."""

    kind: Literal["context", "remove", "add"]
    text: str


@dataclass(frozen=True)
class PatchHunk:
    """One ``@@`` block of context/remove/add lines within an Update file."""

    context_header: str
    lines: tuple[PatchHunkLine, ...]
    is_end_of_file: bool = False


@dataclass(frozen=True)
class PatchFile:
    """A typed file operation parsed from the envelope."""

    kind: PatchOpKind
    path: str
    move_to: str | None = None
    add_lines: tuple[str, ...] = ()
    hunks: tuple[PatchHunk, ...] = ()


@dataclass(frozen=True)
class FileChange:
    """The realized effect of applying one :class:`PatchFile`."""

    kind: PatchOpKind
    path: str
    move_to: str | None = None
    new_content: str | None = None
    # For move/delete, the source that was removed.
    removed_path: str | None = None


class Filesystem(Protocol):
    """Minimal IO surface so ``apply_patch`` stays injectable/testable."""

    def read(self, relative_path: str) -> str: ...

    def exists(self, relative_path: str) -> bool: ...

    def write(self, relative_path: str, content: str) -> None: ...

    def delete(self, relative_path: str) -> None: ...


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_patch_envelope(patch_text: str) -> tuple[PatchFile, ...]:
    """Parse a Codex-style envelope into typed file operations.

    Raises :class:`PatchParseError` on any structural problem.
    """

    if not isinstance(patch_text, str) or not patch_text.strip():
        raise PatchParseError("empty_patch")

    raw_lines = patch_text.split("\n")
    # Locate begin/end markers (tolerant of leading/trailing blank lines).
    begin_index = _index_of(raw_lines, _BEGIN_PATCH)
    if begin_index is None:
        raise PatchParseError("missing_begin_patch")
    end_index = _index_of(raw_lines, _END_PATCH, start=begin_index + 1)
    if end_index is None:
        raise PatchParseError("missing_end_patch")

    body = raw_lines[begin_index + 1 : end_index]
    files: list[PatchFile] = []
    index = 0
    seen_paths: set[str] = set()
    while index < len(body):
        line = body[index]
        if line.strip() == "":
            index += 1
            continue
        if line.startswith(_ADD_FILE):
            file_op, index = _parse_add(body, index)
        elif line.startswith(_DELETE_FILE):
            file_op, index = _parse_delete(body, index)
        elif line.startswith(_UPDATE_FILE):
            file_op, index = _parse_update(body, index)
        else:
            raise PatchParseError("unexpected_envelope_line")
        if file_op.path in seen_paths:
            raise PatchParseError("duplicate_file_op")
        seen_paths.add(file_op.path)
        files.append(file_op)

    if not files:
        raise PatchParseError("no_file_operations")
    return tuple(files)


def _parse_add(body: list[str], index: int) -> tuple[PatchFile, int]:
    path = body[index][len(_ADD_FILE) :].strip()
    if not path:
        raise PatchParseError("add_file_missing_path")
    index += 1
    add_lines: list[str] = []
    while index < len(body) and not _is_file_header(body[index]):
        line = body[index]
        if line.startswith("+"):
            add_lines.append(line[1:])
        elif line.strip() == "":
            # Trailing/blank separator lines inside an Add block are tolerated
            # only when they are genuinely empty additions; bare blank lines
            # between hunks are skipped at the top level, so treat as content.
            add_lines.append("")
        else:
            raise PatchParseError("add_file_line_must_start_with_plus")
        index += 1
    return (
        PatchFile(kind="add", path=path, add_lines=tuple(add_lines)),
        index,
    )


def _parse_delete(body: list[str], index: int) -> tuple[PatchFile, int]:
    path = body[index][len(_DELETE_FILE) :].strip()
    if not path:
        raise PatchParseError("delete_file_missing_path")
    index += 1
    # A Delete block has no further content lines.
    while index < len(body) and not _is_file_header(body[index]):
        if body[index].strip() != "":
            raise PatchParseError("delete_file_has_body")
        index += 1
    return (PatchFile(kind="delete", path=path), index)


def _parse_update(body: list[str], index: int) -> tuple[PatchFile, int]:
    path = body[index][len(_UPDATE_FILE) :].strip()
    if not path:
        raise PatchParseError("update_file_missing_path")
    index += 1
    move_to: str | None = None
    if index < len(body) and body[index].startswith(_MOVE_TO):
        move_to = body[index][len(_MOVE_TO) :].strip()
        if not move_to:
            raise PatchParseError("move_to_missing_path")
        index += 1

    hunks: list[PatchHunk] = []
    while index < len(body) and not _is_file_header(body[index]):
        line = body[index]
        if line.strip() == "":
            index += 1
            continue
        if not line.startswith(_HUNK_MARKER):
            raise PatchParseError("update_file_expected_hunk_marker")
        hunk, index = _parse_hunk(body, index)
        hunks.append(hunk)

    if not hunks:
        raise PatchParseError("update_file_has_no_hunks")
    kind: PatchOpKind = "move" if move_to is not None else "update"
    return (
        PatchFile(kind=kind, path=path, move_to=move_to, hunks=tuple(hunks)),
        index,
    )


def _parse_hunk(body: list[str], index: int) -> tuple[PatchHunk, int]:
    header = body[index][len(_HUNK_MARKER) :].strip()
    index += 1
    lines: list[PatchHunkLine] = []
    is_end_of_file = False
    while index < len(body):
        line = body[index]
        if _is_file_header(line) or line.startswith(_HUNK_MARKER):
            break
        if line.startswith(_END_OF_FILE):
            is_end_of_file = True
            index += 1
            break
        if line.startswith("+"):
            lines.append(PatchHunkLine(kind="add", text=line[1:]))
        elif line.startswith("-"):
            lines.append(PatchHunkLine(kind="remove", text=line[1:]))
        elif line.startswith(" "):
            lines.append(PatchHunkLine(kind="context", text=line[1:]))
        elif line.strip() == "":
            # A bare empty line inside a hunk represents an empty context line.
            lines.append(PatchHunkLine(kind="context", text=""))
        else:
            raise PatchParseError("hunk_line_invalid_prefix")
        index += 1
    if not lines:
        raise PatchParseError("hunk_has_no_lines")
    return (
        PatchHunk(context_header=header, lines=tuple(lines), is_end_of_file=is_end_of_file),
        index,
    )


def _is_file_header(line: str) -> bool:
    return line.startswith((_ADD_FILE, _DELETE_FILE, _UPDATE_FILE)) or line == _END_PATCH


def _index_of(lines: Sequence[str], target: str, *, start: int = 0) -> int | None:
    for offset in range(start, len(lines)):
        if lines[offset].strip() == target:
            return offset
    return None


# ---------------------------------------------------------------------------
# 4-pass matcher + hunk application
# ---------------------------------------------------------------------------

# Pass transforms, applied in escalating fuzziness order. Each is a callable
# that normalizes a single line for comparison only (never mutates output).
_PASS_TRANSFORMS = (
    lambda s: s,  # 1: exact
    lambda s: s.rstrip(),  # 2: trim-end
    lambda s: s.strip(),  # 3: full trim
    lambda s: _normalize_unicode(s).strip(),  # 4: unicode-normalize + trim
)


def _seek_context(
    file_lines: list[str],
    needle: list[str],
    *,
    start: int,
    eof_anchor: bool,
) -> int | None:
    """Find ``needle`` in ``file_lines`` at/after ``start`` using 4 passes.

    Returns the start index of the match or ``None``. With ``eof_anchor`` the
    needle must align with the end of the file.
    """

    if not needle:
        return start
    for transform in _PASS_TRANSFORMS:
        normalized_needle = [transform(line) for line in needle]
        if eof_anchor:
            candidate = len(file_lines) - len(needle)
            if candidate >= start and _window_matches(
                file_lines, candidate, normalized_needle, transform
            ):
                return candidate
            continue
        for candidate in range(start, len(file_lines) - len(needle) + 1):
            if _window_matches(file_lines, candidate, normalized_needle, transform):
                return candidate
    return None


def _window_matches(
    file_lines: list[str],
    start: int,
    normalized_needle: list[str],
    transform,
) -> bool:
    for offset, needle_line in enumerate(normalized_needle):
        if transform(file_lines[start + offset]) != needle_line:
            return False
    return True


def derive_new_contents(file_text: str, hunks: Sequence[PatchHunk]) -> str:
    """Apply ``hunks`` to ``file_text`` and return the new file contents.

    Uses the 4-pass matcher to locate each hunk's context/removed lines.
    Raises :class:`PatchApplyError` if any hunk cannot be located.
    """

    had_trailing_newline = file_text.endswith("\n")
    file_lines = file_text.split("\n")
    if had_trailing_newline:
        # split() leaves a trailing "" element; drop it so indices map to real
        # lines and re-add the newline at the end.
        file_lines = file_lines[:-1]

    result: list[str] = []
    cursor = 0
    for hunk in hunks:
        # The "old" view of the hunk = context + remove lines, in order.
        old_lines = [
            line.text
            for line in hunk.lines
            if line.kind in {"context", "remove"}
        ]
        new_lines = [
            line.text
            for line in hunk.lines
            if line.kind in {"context", "add"}
        ]
        if not old_lines:
            # Pure-insertion hunk: anchor to EOF or current cursor.
            if hunk.is_end_of_file:
                result.extend(file_lines[cursor:])
                cursor = len(file_lines)
            result.extend(new_lines)
            continue

        match_index = _seek_context(
            file_lines,
            old_lines,
            start=cursor,
            eof_anchor=hunk.is_end_of_file,
        )
        if match_index is None:
            raise PatchApplyError("hunk_context_not_found")
        # Emit unchanged lines before the match.
        result.extend(file_lines[cursor:match_index])
        # Emit the new view of this hunk.
        result.extend(new_lines)
        cursor = match_index + len(old_lines)

    # Emit the remaining file tail.
    result.extend(file_lines[cursor:])

    new_text = "\n".join(result)
    if had_trailing_newline and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text


# ---------------------------------------------------------------------------
# Verify-then-apply (multi-file atomicity)
# ---------------------------------------------------------------------------


def plan_patch(
    files: Sequence[PatchFile],
    filesystem: Filesystem,
) -> list[FileChange]:
    """Verify every file op against the current FS snapshot, computing changes.

    No writes occur. Raises :class:`PatchApplyError` (with ``path``) on the
    first verification failure so the caller can apply NOTHING.
    """

    changes: list[FileChange] = []
    # Track virtual existence so multi-file ops within one patch stay coherent.
    for file_op in files:
        if file_op.kind == "add":
            if filesystem.exists(file_op.path):
                raise PatchApplyError("add_target_exists", path=file_op.path)
            content = "\n".join(file_op.add_lines)
            if file_op.add_lines:
                content += "\n"
            changes.append(
                FileChange(kind="add", path=file_op.path, new_content=content)
            )
        elif file_op.kind == "delete":
            if not filesystem.exists(file_op.path):
                raise PatchApplyError("delete_target_missing", path=file_op.path)
            changes.append(
                FileChange(kind="delete", path=file_op.path, removed_path=file_op.path)
            )
        elif file_op.kind in {"update", "move"}:
            if not filesystem.exists(file_op.path):
                raise PatchApplyError("update_target_missing", path=file_op.path)
            current = filesystem.read(file_op.path)
            try:
                new_content = derive_new_contents(current, file_op.hunks)
            except PatchApplyError as exc:
                raise PatchApplyError(exc.reason, path=file_op.path) from exc
            if file_op.kind == "move":
                destination = file_op.move_to or ""
                if not destination:
                    raise PatchApplyError("move_missing_destination", path=file_op.path)
                if filesystem.exists(destination) and destination != file_op.path:
                    raise PatchApplyError("move_target_exists", path=destination)
                changes.append(
                    FileChange(
                        kind="move",
                        path=file_op.path,
                        move_to=destination,
                        new_content=new_content,
                        removed_path=file_op.path,
                    )
                )
            else:
                changes.append(
                    FileChange(
                        kind="update", path=file_op.path, new_content=new_content
                    )
                )
        else:  # pragma: no cover - exhaustive guard
            raise PatchApplyError("unsupported_op", path=file_op.path)
    return changes


def apply_changes(changes: Sequence[FileChange], filesystem: Filesystem) -> None:
    """Apply pre-verified changes. Call only after ``plan_patch`` succeeds."""

    for change in changes:
        if change.kind == "add":
            filesystem.write(change.path, change.new_content or "")
        elif change.kind == "delete":
            filesystem.delete(change.path)
        elif change.kind == "update":
            filesystem.write(change.path, change.new_content or "")
        elif change.kind == "move":
            filesystem.write(change.move_to or "", change.new_content or "")
            if (change.move_to or "") != change.path:
                filesystem.delete(change.path)


def apply_patch(patch_text: str, filesystem: Filesystem) -> list[FileChange]:
    """Parse, verify ALL ops, then apply atomically.

    Returns the list of realized :class:`FileChange`. Raises
    :class:`PatchParseError` for malformed envelopes or
    :class:`PatchApplyError` (with ``path``) if any op fails verification, in
    which case NO files are mutated.
    """

    files = parse_patch_envelope(patch_text)
    changes = plan_patch(files, filesystem)
    apply_changes(changes, filesystem)
    return changes


__all__ = [
    "FileChange",
    "Filesystem",
    "PatchApplyError",
    "PatchFile",
    "PatchHunk",
    "PatchHunkLine",
    "PatchParseError",
    "apply_patch",
    "derive_new_contents",
    "parse_patch_envelope",
    "plan_patch",
]
