from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
import errno
import os
from pathlib import Path, PurePosixPath
import socket
import subprocess
import sys
import tempfile
from time import perf_counter
import unicodedata
from urllib.parse import quote

import pytest

from magi_agent.execution_authority import canonicalization
from magi_agent.execution_authority.canonicalization import (
    CanonicalizationError,
    canonical_file_resource,
    canonical_http_resource,
    workspace_relative_path,
)
from magi_agent.ops.safety import canonical_digest


def _workspace_prefix(root: Path) -> str:
    resolved = root.resolve(strict=True)
    root_stat = resolved.stat()
    digest = canonical_digest(
        {
            "realPath": str(resolved),
            "stDev": root_stat.st_dev,
            "stIno": root_stat.st_ino,
        }
    )
    return f"workspace://{digest}/"


def _case_alias_or_skip(path: Path) -> Path:
    alias = path.with_name(path.name.swapcase())
    if alias == path or not alias.exists():
        pytest.skip("filesystem is case-sensitive")
    try:
        if not alias.samefile(path):
            pytest.skip("filesystem is case-sensitive")
    except OSError:
        pytest.skip("filesystem does not expose a stable case alias")
    return alias


def _relative_path_for_candidate_bytes(root: Path, total_bytes: int) -> str:
    relative_bytes = total_bytes - len(str(root).encode("utf-8")) - 1
    component_count = (relative_bytes + 255) // 256
    letter_count = relative_bytes - (component_count - 1)
    lengths: list[int] = []
    for _ in range(component_count):
        length = min(255, letter_count)
        lengths.append(length)
        letter_count -= length
    assert letter_count == 0
    return "/".join("x" * length for length in lengths)


def test_file_resource_binds_resolved_root_without_exposing_absolute_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "nested" / "hello world.txt"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")

    resource = canonical_file_resource(workspace, target)

    assert resource == _workspace_prefix(workspace) + "nested/hello%20world.txt"
    assert str(workspace.resolve()) not in resource


def test_file_resource_percent_encodes_each_utf8_segment(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "한 글" / "résumé#.txt"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")

    resource = canonical_file_resource(workspace, target)

    assert resource == (
        _workspace_prefix(workspace) + "%ED%95%9C%20%EA%B8%80/r%C3%A9sum%C3%A9%23.txt"
    )


def test_file_resource_case_alias_root_converges_on_case_insensitive_fs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "WorkspaceCase"
    workspace.mkdir()
    (workspace / "file.txt").write_text("hello", encoding="utf-8")
    alias = _case_alias_or_skip(workspace)

    actual_ref = canonical_file_resource(workspace, "file.txt")
    alias_ref = canonical_file_resource(alias, "file.txt")

    assert actual_ref == _workspace_prefix(workspace) + "file.txt"
    assert alias_ref == actual_ref
    assert workspace_relative_path(alias, actual_ref) == PurePosixPath("file.txt")


def test_file_resource_case_alias_path_uses_stored_entry_spelling(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "ActualDirectory" / "MixedName.txt"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")
    _case_alias_or_skip(target.parent)

    actual_ref = canonical_file_resource(workspace, target)
    alias_ref = canonical_file_resource(
        workspace,
        "actualdirectory/mixedname.txt",
    )

    assert alias_ref == actual_ref
    assert alias_ref.endswith("/ActualDirectory/MixedName.txt")


def test_file_resource_accepts_absolute_root_alias_with_different_depth(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "deep" / "physical" / "workspace"
    workspace.mkdir(parents=True)
    target = workspace / "file.txt"
    target.write_text("hello", encoding="utf-8")
    root_alias = tmp_path / "root-alias"
    root_alias.symlink_to(workspace, target_is_directory=True)

    actual_ref = canonical_file_resource(workspace, target)
    alias_ref = canonical_file_resource(workspace, root_alias / "file.txt")

    assert alias_ref == actual_ref


def test_file_resource_accepts_relative_path_and_retains_create_suffix(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "existing").mkdir(parents=True)

    resource = canonical_file_resource(workspace, "existing/new/deep/file.txt")

    assert resource == _workspace_prefix(workspace) + "existing/new/deep/file.txt"


@pytest.mark.parametrize(
    "path",
    (
        "Future.txt",
        "new/Future.txt",
        "Future/file.txt",
        "straße.txt",
        unicodedata.normalize("NFD", "résumé.txt"),
        "new/" + unicodedata.normalize("NFD", "résumé.txt"),
        unicodedata.normalize("NFD", "résumé") + "/file.txt",
    ),
)
def test_file_resource_rejects_non_deterministic_unresolved_creation_spelling(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(CanonicalizationError, match="creation spelling"):
        canonical_file_resource(workspace, path)


@pytest.mark.parametrize("path", ("future.txt", "new/deep/future.txt", "résumé.txt"))
def test_file_resource_accepts_casefold_stable_nfc_creation_spelling(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    resource = canonical_file_resource(workspace, path)
    target = workspace.joinpath(*path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("created", encoding="utf-8")

    assert resource == _workspace_prefix(workspace) + "/".join(
        quote(segment, safe="") for segment in path.split("/")
    )
    assert canonical_file_resource(workspace, target) == resource


def test_file_resource_allows_stable_creation_below_existing_stored_parent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    stored_parent = workspace / "ActualDirectory"
    stored_parent.mkdir(parents=True)

    resource = canonical_file_resource(workspace, stored_parent / "future.txt")

    assert resource == _workspace_prefix(workspace) + "ActualDirectory/future.txt"


@pytest.mark.parametrize(
    "stored_name",
    ("Future.txt", unicodedata.normalize("NFD", "résumé.txt")),
)
def test_file_resource_preserves_existing_stored_spelling(
    tmp_path: Path,
    stored_name: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / stored_name
    target.write_text("existing", encoding="utf-8")

    resource = canonical_file_resource(workspace, target)

    assert workspace_relative_path(workspace, resource).name == stored_name


@pytest.mark.parametrize(
    ("stale_name", "created_name"),
    (
        ("Future.txt", "future.txt"),
        (unicodedata.normalize("NFD", "résumé.txt"), "résumé.txt"),
    ),
)
def test_workspace_relative_path_never_authorizes_stale_creation_alias(
    tmp_path: Path,
    stale_name: str,
    created_name: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stale_ref = _workspace_prefix(workspace) + quote(stale_name, safe="")
    (workspace / created_name).write_text("created", encoding="utf-8")

    with pytest.raises(CanonicalizationError, match="canonical workspace identity"):
        workspace_relative_path(workspace, stale_ref)


def test_file_resource_follows_directory_and_final_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    real_directory = workspace / "real-directory"
    real_directory.mkdir(parents=True)
    real_file = real_directory / "real-file.txt"
    real_file.write_text("hello", encoding="utf-8")
    (workspace / "directory-alias").symlink_to(real_directory, target_is_directory=True)
    (workspace / "file-alias.txt").symlink_to(real_file)

    directory_ref = canonical_file_resource(
        workspace,
        "directory-alias/real-file.txt",
    )
    final_ref = canonical_file_resource(workspace, "file-alias.txt")

    expected = _workspace_prefix(workspace) + "real-directory/real-file.txt"
    assert directory_ref == expected
    assert final_ref == expected


@pytest.mark.parametrize("kind", ("directory", "final", "create_suffix"))
def test_file_resource_rejects_symlink_escape(tmp_path: Path, kind: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")

    if kind == "directory":
        (workspace / "escape").symlink_to(outside, target_is_directory=True)
        path: str | Path = "escape/outside.txt"
    elif kind == "final":
        (workspace / "escape.txt").symlink_to(outside_file)
        path = "escape.txt"
    else:
        (workspace / "escape").symlink_to(outside, target_is_directory=True)
        path = "escape/not-created-yet.txt"

    with pytest.raises(CanonicalizationError, match="workspace") as caught:
        canonical_file_resource(workspace, path)

    assert str(workspace.resolve()) not in str(caught.value)
    assert str(outside.resolve()) not in str(caught.value)


def test_file_resource_uses_component_containment_not_string_prefix(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    sibling = tmp_path / "work-evil"
    sibling.mkdir()
    target = sibling / "file.txt"
    target.write_text("outside", encoding="utf-8")

    with pytest.raises(CanonicalizationError, match="workspace"):
        canonical_file_resource(workspace, target)


def test_file_resource_rejects_symlink_outside_then_back_inside(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    (outside / "back").symlink_to(workspace, target_is_directory=True)

    with pytest.raises(CanonicalizationError, match="traverses outside"):
        canonical_file_resource(workspace, "escape/back/file.txt")


def test_file_resource_rejects_unresolved_suffix_that_pops_above_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("inside", encoding="utf-8")

    with pytest.raises(CanonicalizationError, match="above the workspace root"):
        canonical_file_resource(
            workspace,
            "missing/../../workspace/file.txt",
        )


def test_file_resource_resumes_symlink_checks_after_unresolved_parent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    (outside / "back").symlink_to(workspace, target_is_directory=True)

    with pytest.raises(CanonicalizationError, match="traverses outside"):
        canonical_file_resource(
            workspace,
            "missing/../escape/back/file.txt",
        )


def test_file_resource_allows_unresolved_parent_normalization_within_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "file.txt"
    target.write_text("inside", encoding="utf-8")

    assert canonical_file_resource(
        workspace,
        "missing/../file.txt",
    ) == canonical_file_resource(workspace, target)


def test_file_resource_rejects_broken_and_looping_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "broken").symlink_to(workspace / "missing-target")
    (workspace / "loop-a").symlink_to(workspace / "loop-b")
    (workspace / "loop-b").symlink_to(workspace / "loop-a")

    for path in ("broken", "broken/child", "loop-a", "loop-a/child"):
        with pytest.raises(CanonicalizationError, match="symlink"):
            canonical_file_resource(workspace, path)


def test_file_resource_rejects_non_directory_existing_ancestor(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("not a directory", encoding="utf-8")

    with pytest.raises(CanonicalizationError, match="directory"):
        canonical_file_resource(workspace, "file.txt/child")


def test_file_resource_rejects_existing_hard_link_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_text("shared inode", encoding="utf-8")
    second.hardlink_to(first)

    for target in (first, second, "missing/../first.txt"):
        with pytest.raises(CanonicalizationError, match="hard link"):
            canonical_file_resource(workspace, target)


def test_file_resource_rejects_existing_fifo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fifo = workspace / "pipe"
    os.mkfifo(fifo)

    with pytest.raises(CanonicalizationError, match="regular file or directory"):
        canonical_file_resource(workspace, fifo)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX is unavailable")
def test_file_resource_rejects_existing_unix_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="magi-authority-", dir="/tmp") as temporary:
        workspace = Path(temporary) / "workspace"
        workspace.mkdir()
        socket_path = workspace / "socket"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))

            with pytest.raises(CanonicalizationError, match="regular file or directory"):
                canonical_file_resource(workspace, socket_path)


@pytest.mark.parametrize(
    "path",
    (
        "bad\x00name",
        "bad\ud800name",
    ),
)
def test_file_resource_rejects_non_wire_safe_path_text(tmp_path: Path, path: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(CanonicalizationError, match="path"):
        canonical_file_resource(workspace, path)


@pytest.mark.parametrize(
    "path",
    (
        "/".join("x" for _ in range(400)),
        "x" * 1_599,
    ),
)
def test_file_resource_rejects_oversized_paths_before_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    workspace = tmp_path / "workspace"

    def unexpected_root_resolution(_root: object) -> Path:
        raise AssertionError("oversized path reached the filesystem")

    monkeypatch.setattr(
        canonicalization,
        "_resolved_workspace_root",
        unexpected_root_resolution,
    )
    started = perf_counter()
    with pytest.raises(CanonicalizationError, match="budget") as caught:
        canonical_file_resource(workspace, path)

    assert perf_counter() - started < 0.05
    assert str(workspace) not in str(caught.value)


@pytest.mark.parametrize(
    "path",
    (
        "/" + "/".join("x" for _ in range(33)),
        "/" + "x" * 256,
    ),
)
def test_file_resource_bounds_nonlexical_absolute_paths_before_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    def unexpected_root_resolution(_root: object) -> Path:
        raise AssertionError("oversized absolute path reached the filesystem")

    monkeypatch.setattr(
        canonicalization,
        "_resolved_workspace_root",
        unexpected_root_resolution,
    )

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_file_resource("/workspace", path)


def test_file_resource_rejects_exact_total_budget_overflow_before_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _relative_path_for_candidate_bytes(Path("/workspace"), 1_024)

    def unexpected_root_resolution(_root: object) -> Path:
        raise AssertionError("over-budget path reached the filesystem")

    monkeypatch.setattr(
        canonicalization,
        "_resolved_workspace_root",
        unexpected_root_resolution,
    )

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_file_resource("/workspace", path)


def test_file_resource_rejects_raw_combined_overflow_before_cwd_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = "/".join("r" * 200 for _ in range(4))
    path = "p" * 220
    assert len(root.encode()) + 1 + len(path.encode()) == 1_024

    def unexpected_absolute_resolution(_path: object) -> Path:
        raise AssertionError("over-budget path reached cwd resolution")

    def unexpected_root_resolution(_root: object) -> Path:
        raise AssertionError("over-budget path reached the filesystem")

    monkeypatch.setattr(
        canonicalization,
        "_lexical_absolute_workspace_root",
        unexpected_absolute_resolution,
    )
    monkeypatch.setattr(canonicalization, "_resolved_workspace_root", unexpected_root_resolution)

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_file_resource(root, path)


def test_workspace_segment_budget_counts_utf8_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    boundary = "가" * 85

    resource = canonical_file_resource(workspace, boundary)

    assert workspace_relative_path(workspace, resource) == PurePosixPath(boundary)


def test_workspace_segment_rejects_multibyte_overflow_before_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overflow = "가" * 86
    encoded_overflow = quote(overflow, safe="")
    ref = f"workspace://sha256:{'0' * 64}/{encoded_overflow}"

    def unexpected_root_resolution(_root: object) -> Path:
        raise AssertionError("over-budget multibyte segment reached the filesystem")

    monkeypatch.setattr(
        canonicalization,
        "_resolved_workspace_root",
        unexpected_root_resolution,
    )

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_file_resource("/workspace", overflow)
    with pytest.raises(CanonicalizationError, match="budget"):
        workspace_relative_path("/workspace", ref)


class _FailingPathLike:
    def __fspath__(self) -> str:
        raise OSError("sensitive backend failure")


def test_workspace_pathlike_oserror_is_contained() -> None:
    fake_ref = f"workspace://sha256:{'0' * 64}/file.txt"
    operations = (
        lambda: canonical_file_resource(_FailingPathLike(), "file.txt"),
        lambda: canonical_file_resource("/workspace", _FailingPathLike()),
        lambda: workspace_relative_path(_FailingPathLike(), fake_ref),
    )

    for operation in operations:
        with pytest.raises(CanonicalizationError) as caught:
            operation()
        assert "sensitive backend failure" not in str(caught.value)


def test_filesystem_race_oserror_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/workspace")
    candidate = root / "missing"

    monkeypatch.setattr(canonicalization, "_resolved_workspace_root", lambda _root: root)
    monkeypatch.setattr(
        canonicalization,
        "_validate_no_descendant_mount_crossing",
        lambda _root, _candidate: (0, 0),
    )
    monkeypatch.setattr(
        canonicalization,
        "_validate_existing_prefix_traversal",
        lambda _root, _candidate: (),
    )

    def racing_stat(path: Path) -> tuple[int, int, str]:
        if path == root:
            return 1, 2, "directory"
        raise FileNotFoundError(path)

    def racing_is_symlink(path: Path) -> bool:
        if path == candidate:
            raise OSError("sensitive filesystem race")
        return False

    monkeypatch.setattr(canonicalization, "_stat_identity", racing_stat)
    monkeypatch.setattr(Path, "is_symlink", racing_is_symlink)

    with pytest.raises(CanonicalizationError) as caught:
        canonical_file_resource(root, "missing")

    assert "sensitive filesystem race" not in str(caught.value)


def test_oversized_workspace_text_is_bounded_before_linear_safety_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_has_surrogate = canonicalization._has_surrogate
    original_has_control = canonicalization._has_control

    def bounded_surrogate_scan(value: str) -> bool:
        if len(value) > 1_023:
            raise AssertionError("oversized path reached surrogate scan")
        return original_has_surrogate(value)

    def bounded_control_scan(value: str) -> bool:
        if len(value) > 1_023:
            raise AssertionError("oversized path reached control scan")
        return original_has_control(value)

    monkeypatch.setattr(canonicalization, "_has_surrogate", bounded_surrogate_scan)
    monkeypatch.setattr(canonicalization, "_has_control", bounded_control_scan)

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_file_resource("/workspace", "x" * 100_000)


def test_oversized_workspace_ref_is_bounded_before_linear_safety_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_has_surrogate = canonicalization._has_surrogate
    original_has_control = canonicalization._has_control

    def bounded_surrogate_scan(value: str) -> bool:
        if len(value) > 25_000:
            raise AssertionError("oversized resource reached surrogate scan")
        return original_has_surrogate(value)

    def bounded_control_scan(value: str) -> bool:
        if len(value) > 25_000:
            raise AssertionError("oversized resource reached control scan")
        return original_has_control(value)

    monkeypatch.setattr(canonicalization, "_has_surrogate", bounded_surrogate_scan)
    monkeypatch.setattr(canonicalization, "_has_control", bounded_control_scan)
    ref = f"workspace://sha256:{'0' * 64}/" + "x" * 100_000

    with pytest.raises(CanonicalizationError, match="budget"):
        workspace_relative_path("/workspace", ref)


@pytest.mark.parametrize(
    "suffix",
    (
        "/".join("x" for _ in range(400)),
        "x" * 1_599,
    ),
)
def test_workspace_relative_path_rejects_oversized_paths_before_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    workspace = tmp_path / "workspace"
    ref = f"workspace://sha256:{'0' * 64}/{suffix}"

    def unexpected_root_resolution(_root: object) -> Path:
        raise AssertionError("oversized path reached the filesystem")

    monkeypatch.setattr(
        canonicalization,
        "_resolved_workspace_root",
        unexpected_root_resolution,
    )
    started = perf_counter()
    with pytest.raises(CanonicalizationError, match="budget") as caught:
        workspace_relative_path(workspace, ref)

    assert perf_counter() - started < 0.05
    assert str(workspace) not in str(caught.value)


@pytest.mark.parametrize(
    "suffix",
    (
        "/".join("x" for _ in range(33)),
        "x" * 256,
        _relative_path_for_candidate_bytes(Path("/workspace"), 1_024),
    ),
)
def test_workspace_relative_path_rejects_exact_budget_overflow_before_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    ref = f"workspace://sha256:{'0' * 64}/{suffix}"

    def unexpected_root_resolution(_root: object) -> Path:
        raise AssertionError("over-budget resource reached the filesystem")

    monkeypatch.setattr(
        canonicalization,
        "_resolved_workspace_root",
        unexpected_root_resolution,
    )

    with pytest.raises(CanonicalizationError, match="budget"):
        workspace_relative_path("/workspace", ref)


def test_workspace_root_only_inverse_rechecks_resolved_root_budget_before_stat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_root = Path("/" + "/".join("x" * 200 for _ in range(6)))
    assert len(os.fspath(resolved_root).encode()) > 1_023
    ref = f"workspace://sha256:{'0' * 64}/"

    monkeypatch.setattr(
        canonicalization,
        "_resolved_workspace_root",
        lambda _root: resolved_root,
    )

    def unexpected_stat(_path: Path) -> tuple[int, int, str]:
        raise AssertionError("over-budget resolved root reached the filesystem")

    monkeypatch.setattr(canonicalization, "_stat_identity", unexpected_stat)

    with pytest.raises(CanonicalizationError, match="budget"):
        workspace_relative_path("/short-root-alias", ref)


def test_workspace_path_budget_accepts_documented_boundaries(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    component_boundary = "/".join("x" for _ in range(32))
    segment_boundary = "x" * 255
    total_boundary = _relative_path_for_candidate_bytes(workspace, 1_023)

    for path in (component_boundary, segment_boundary, total_boundary):
        resource = canonical_file_resource(workspace, path)
        assert workspace_relative_path(workspace, resource) == PurePosixPath(path)


@pytest.mark.parametrize(
    "path_factory",
    (
        lambda _workspace: "/".join("x" for _ in range(33)),
        lambda _workspace: "x" * 256,
        lambda workspace: _relative_path_for_candidate_bytes(workspace, 1_024),
    ),
)
def test_workspace_path_budget_rejects_values_above_documented_boundaries(
    tmp_path: Path,
    path_factory: Callable[[Path], str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = path_factory(workspace)

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_file_resource(workspace, path)


def test_file_resource_requires_existing_resolved_directory_root(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    file_root = tmp_path / "file.txt"
    file_root.write_text("file", encoding="utf-8")

    for root in (missing, file_root):
        with pytest.raises(CanonicalizationError, match="root"):
            canonical_file_resource(root, "child")


def test_file_resource_rejects_transient_root_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "file.txt"
    target.write_text("hello", encoding="utf-8")
    resolved_root = workspace.resolve(strict=True)
    real_stat_identity = canonicalization._stat_identity
    root_observations = 0

    def changing_root_identity(path: Path) -> tuple[int, int, str]:
        nonlocal root_observations
        identity = real_stat_identity(path)
        if path == resolved_root:
            root_observations += 1
            if root_observations > 1:
                return identity[0], identity[1] + 1, identity[2]
        return identity

    monkeypatch.setattr(canonicalization, "_stat_identity", changing_root_identity)

    with pytest.raises(CanonicalizationError, match="identity changed"):
        canonical_file_resource(workspace, target)


def test_canonical_stored_path_exact_spelling_uses_one_no_follow_stat_per_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    target = nested / "target.txt"
    target.write_text("target", encoding="utf-8")
    for index in range(64):
        (nested / f"sibling-{index:02d}.txt").write_text("sibling", encoding="utf-8")

    real_scandir = os.scandir
    entry_stat_calls: list[bool] = []

    class CountingEntry:
        def __init__(self, entry: os.DirEntry[str]) -> None:
            self._entry = entry

        @property
        def name(self) -> str:
            return self._entry.name

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            entry_stat_calls.append(follow_symlinks)
            return self._entry.stat(follow_symlinks=follow_symlinks)

    @contextmanager
    def counting_scandir(path: Path) -> object:
        with real_scandir(path) as entries:
            yield (CountingEntry(entry) for entry in entries)

    monkeypatch.setattr(canonicalization.os, "scandir", counting_scandir)

    stored = canonicalization._canonical_stored_path(target.resolve(strict=True))

    assert stored == target.resolve(strict=True)
    assert entry_stat_calls == [False] * (len(target.resolve(strict=True).parts) - 1)


def test_canonical_stored_path_rejects_excessive_physical_depth_before_stat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = Path("/" + "/".join("x" for _ in range(65)))

    def unexpected_stat(_path: Path) -> tuple[int, int, str]:
        raise AssertionError("over-depth path reached the filesystem")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", lambda self, strict=True: self)
        patch.setattr(canonicalization, "_stat_identity", unexpected_stat)

        with pytest.raises(CanonicalizationError, match="budget"):
            canonicalization._canonical_stored_path(target)


def test_canonical_stored_path_caps_sibling_scan_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = Path("/target")
    requested_identity = (11, 22, "other")

    class Entry:
        def __init__(self, index: int) -> None:
            self.name = f"unrelated-{index}"

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            raise AssertionError("unrelated entries must not be statted")

    @contextmanager
    def excessive_scandir(_path: Path) -> object:
        yield (Entry(index) for index in range(4_097))

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", lambda self, strict=True: self)
        patch.setattr(
            canonicalization,
            "_stat_identity",
            lambda _path: requested_identity,
        )
        patch.setattr(canonicalization.os, "scandir", excessive_scandir)

        with pytest.raises(CanonicalizationError, match="budget"):
            canonicalization._canonical_stored_path(target)


def test_file_resource_stored_spelling_scans_scale_linearly_with_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace
    for index in range(24):
        target /= f"level-{index:02d}"
        target.mkdir()
    (target / "target.txt").write_text("target", encoding="utf-8")
    target /= "target.txt"

    real_scandir = os.scandir
    scandir_calls = 0

    @contextmanager
    def counting_scandir(path: Path) -> object:
        nonlocal scandir_calls
        scandir_calls += 1
        with real_scandir(path) as entries:
            yield entries

    monkeypatch.setattr(canonicalization.os, "scandir", counting_scandir)

    canonical_file_resource(workspace, target)

    physical_depth = len(target.resolve(strict=True).parts) - 1
    assert scandir_calls <= physical_depth * 8


def test_file_resource_uses_injected_nested_mount_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    ordinary = workspace / "ordinary"
    nested_mount = workspace / "nested-mount"
    ordinary.mkdir(parents=True)
    nested_mount.mkdir()
    (ordinary / "file.txt").write_text("ordinary", encoding="utf-8")
    (nested_mount / "file.txt").write_text("mounted", encoding="utf-8")
    nested_mount = nested_mount.resolve(strict=True)
    boundary_checks: list[Path] = []

    def injected_boundary(_root: Path, resolved: Path) -> None:
        boundary_checks.append(resolved)
        if resolved == nested_mount / "file.txt":
            raise CanonicalizationError("path crosses a descendant mount boundary")

    monkeypatch.setattr(
        canonicalization,
        "_validate_no_descendant_mount_crossing",
        injected_boundary,
        raising=False,
    )

    canonical_file_resource(workspace, ordinary / "file.txt")
    with pytest.raises(CanonicalizationError, match="mount"):
        canonical_file_resource(workspace, nested_mount / "file.txt")
    assert ordinary / "file.txt" in boundary_checks
    assert nested_mount / "file.txt" in boundary_checks


def test_linux_mount_boundary_checks_original_walk_and_rejects_nested_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NamespaceStat:
        st_dev = 7
        st_ino = 11

    lexical_parts = ("alias", "..", "real", "file.txt")
    observed_paths: list[str] = []

    def rejecting_openat2(
        _root_fd: int,
        relative: str,
        *,
        resolve_flags: int,
    ) -> int:
        _ = resolve_flags
        observed_paths.append(relative)
        raise OSError(errno.EXDEV, "nested bind mount")

    monkeypatch.setattr(canonicalization.os, "O_PATH", 0, raising=False)
    monkeypatch.setattr(canonicalization.os, "stat", lambda _path: NamespaceStat())
    monkeypatch.setattr(canonicalization.os, "open", lambda *_args: 91)
    monkeypatch.setattr(canonicalization.os, "close", lambda _fd: None)
    monkeypatch.setattr(
        canonicalization,
        "_parts_after_physical_root",
        lambda _root, _candidate: lexical_parts,
    )
    monkeypatch.setattr(canonicalization, "_linux_openat2", rejecting_openat2)

    with pytest.raises(CanonicalizationError, match="mount"):
        canonicalization._validate_linux_mount_boundary(
            Path("/workspace"),
            Path("/workspace/alias/../real/file.txt"),
        )

    assert observed_paths == [os.path.join(*lexical_parts)]


def test_linux_mount_boundary_uses_beneath_for_resolved_existing_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NamespaceStat:
        st_dev = 7
        st_ino = 11

    calls: list[tuple[str, int]] = []
    next_fd = iter((101, 102))

    def recording_openat2(
        _root_fd: int,
        relative: str,
        *,
        resolve_flags: int,
    ) -> int:
        calls.append((relative, resolve_flags))
        return next(next_fd)

    with monkeypatch.context() as patch:
        patch.setattr(canonicalization.os, "O_PATH", 0, raising=False)
        patch.setattr(canonicalization.os, "stat", lambda _path: NamespaceStat())
        patch.setattr(canonicalization.os, "open", lambda *_args: 91)
        patch.setattr(canonicalization.os, "close", lambda _fd: None)
        patch.setattr(Path, "resolve", lambda self, strict=True: self)
        patch.setattr(
            canonicalization,
            "_parts_after_physical_root",
            lambda _root, _candidate: ("real", "file.txt"),
        )
        patch.setattr(canonicalization, "_linux_openat2", recording_openat2)

        binding = canonicalization._validate_linux_mount_boundary(
            Path("/workspace"),
            Path("/workspace/real/file.txt"),
        )

    assert binding == (7, 11)
    assert calls == [
        (
            "real/file.txt",
            canonicalization._LINUX_RESOLVE_NO_XDEV
            | canonicalization._LINUX_RESOLVE_NO_MAGICLINKS,
        ),
        (
            "real/file.txt",
            canonicalization._LINUX_RESOLVE_BENEATH
            | canonicalization._LINUX_RESOLVE_NO_XDEV
            | canonicalization._LINUX_RESOLVE_NO_MAGICLINKS,
        ),
    ]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux bind mounts are required")
def test_file_resource_linux_bind_mount_alias_converges_or_fails_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    alias = tmp_path / "workspace-bind"
    workspace.mkdir()
    alias.mkdir()
    target = workspace / "nested" / "target.txt"
    target.parent.mkdir()
    target.write_text("target", encoding="utf-8")

    try:
        subprocess.run(
            ("mount", "--bind", os.fspath(workspace), os.fspath(alias)),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("bind mount privilege is unavailable")

    try:
        original_ref = canonical_file_resource(workspace, target)
        try:
            alias_ref = canonical_file_resource(alias, alias / "nested" / "target.txt")
        except CanonicalizationError:
            return
        assert alias_ref != original_ref
        with pytest.raises(CanonicalizationError, match="different root"):
            workspace_relative_path(alias, original_ref)
    finally:
        subprocess.run(
            ("umount", os.fspath(alias)),
            check=False,
            capture_output=True,
            text=True,
        )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux bind mounts are required")
def test_file_resource_linux_nested_bind_mount_fails_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    real = workspace / "real"
    alias = workspace / "alias"
    real.mkdir(parents=True)
    alias.mkdir()
    target = real / "target.txt"
    target.write_text("target", encoding="utf-8")

    try:
        subprocess.run(
            ("mount", "--bind", os.fspath(real), os.fspath(alias)),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("bind mount privilege is unavailable")

    try:
        canonical_file_resource(workspace, target)
        with pytest.raises(CanonicalizationError, match="mount"):
            canonical_file_resource(workspace, alias / "target.txt")
    finally:
        subprocess.run(
            ("umount", os.fspath(alias)),
            check=False,
            capture_output=True,
            text=True,
        )


def test_workspace_relative_path_round_trips_to_safe_pure_posix_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "nested" / "한 글.txt"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")
    resource = canonical_file_resource(workspace, target)

    relative = workspace_relative_path(workspace, resource)

    assert relative == PurePosixPath("nested/한 글.txt")
    assert type(relative) is PurePosixPath
    assert not relative.is_absolute()
    assert ".." not in relative.parts


def test_workspace_relative_path_round_trips_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resource = canonical_file_resource(workspace, ".")

    assert resource == _workspace_prefix(workspace)
    assert workspace_relative_path(workspace, resource) == PurePosixPath(".")


@pytest.mark.parametrize("replacement_kind", ("directory", "symlink"))
def test_workspace_relative_path_rejects_root_replacement_before_root_only_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resource = canonical_file_resource(workspace, ".")
    resolved_root = workspace.resolve(strict=True)
    displaced_root = tmp_path / "displaced-workspace"
    real_workspace_digest = canonicalization._workspace_digest
    replaced = False

    def replacing_workspace_digest(
        root: Path,
        *,
        identity: tuple[int, int, str] | None = None,
    ) -> str:
        nonlocal replaced
        digest = real_workspace_digest(root, identity=identity)
        if root == resolved_root and not replaced:
            resolved_root.rename(displaced_root)
            if replacement_kind == "directory":
                resolved_root.mkdir()
            else:
                resolved_root.symlink_to(displaced_root, target_is_directory=True)
            replaced = True
        return digest

    monkeypatch.setattr(canonicalization, "_workspace_digest", replacing_workspace_digest)

    with pytest.raises(CanonicalizationError, match="root.*identity changed"):
        workspace_relative_path(workspace, resource)


def test_workspace_relative_path_revalidates_root_digest_before_root_only_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resource = canonical_file_resource(workspace, ".")
    real_workspace_digest = canonicalization._workspace_digest
    digest_calls = 0

    def changing_workspace_digest(
        root: Path,
        *,
        identity: tuple[int, int, str] | None = None,
    ) -> str:
        nonlocal digest_calls
        digest_calls += 1
        digest = real_workspace_digest(root, identity=identity)
        if digest_calls == 1:
            return digest
        replacement = "0" if digest[-1] != "0" else "1"
        return digest[:-1] + replacement

    monkeypatch.setattr(canonicalization, "_workspace_digest", changing_workspace_digest)

    with pytest.raises(CanonicalizationError, match="root.*identity changed"):
        workspace_relative_path(workspace, resource)

    assert digest_calls == 2


@pytest.mark.parametrize("swap_timing", ("before_entry_stat", "after_entry_stat"))
def test_workspace_relative_path_rejects_same_inode_symlink_swap_during_root_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap_timing: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resource = canonical_file_resource(workspace, ".")
    displaced_workspace = tmp_path / "displaced-workspace"
    real_scandir = os.scandir
    parent_scans = 0
    replaced = False

    def replace_root() -> None:
        nonlocal replaced
        workspace.rename(displaced_workspace)
        workspace.symlink_to(displaced_workspace, target_is_directory=True)
        replaced = True

    class ReplacingEntry:
        def __init__(self, entry: os.DirEntry[str]) -> None:
            self._entry = entry
            self._name = entry.name

        @property
        def name(self) -> str:
            if not replaced and swap_timing == "before_entry_stat":
                replace_root()
            return self._name

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            result = self._entry.stat(follow_symlinks=follow_symlinks)
            if not replaced and swap_timing == "after_entry_stat":
                replace_root()
            return result

    @contextmanager
    def replacing_scandir(path: Path) -> object:
        nonlocal parent_scans
        with real_scandir(path) as entries:
            if Path(path) != workspace.parent:
                yield entries
                return
            parent_scans += 1
            if parent_scans != 2:
                yield entries
                return
            yield (
                ReplacingEntry(entry) if entry.name == workspace.name else entry
                for entry in entries
            )

    monkeypatch.setattr(canonicalization.os, "scandir", replacing_scandir)

    with pytest.raises(CanonicalizationError, match="root.*identity changed"):
        workspace_relative_path(workspace, resource)

    assert replaced
    assert workspace.is_symlink()


def test_workspace_relative_path_rejects_same_inode_symlink_swap_after_spelling_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resource = canonical_file_resource(workspace, ".")
    displaced_workspace = tmp_path / "displaced-workspace"
    real_canonical_stored_path = canonicalization._canonical_stored_path
    spelling_checks = 0

    def replacing_canonical_stored_path(path: Path) -> Path:
        nonlocal spelling_checks
        stored = real_canonical_stored_path(path)
        spelling_checks += 1
        if spelling_checks == 2:
            workspace.rename(displaced_workspace)
            workspace.symlink_to(displaced_workspace, target_is_directory=True)
        return stored

    monkeypatch.setattr(
        canonicalization,
        "_canonical_stored_path",
        replacing_canonical_stored_path,
    )

    with pytest.raises(CanonicalizationError, match="root.*identity changed"):
        workspace_relative_path(workspace, resource)

    assert spelling_checks == 2
    assert workspace.is_symlink()


def test_workspace_relative_path_round_trips_posix_backslash_filename(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "literal\\name.txt"
    target.write_text("hello", encoding="utf-8")

    resource = canonical_file_resource(workspace, target)

    assert resource == _workspace_prefix(workspace) + "literal%5Cname.txt"
    assert workspace_relative_path(workspace, resource) == PurePosixPath("literal\\name.txt")


@pytest.mark.parametrize(
    "suffix",
    (
        "nested//file.txt",
        "nested/",
        "/nested/file.txt",
        "nested/%2Fetc",
        "nested/%2fetc",
        "nested/%2E%2E/file.txt",
        "nested/%7efile.txt",
        "nested/%FF",
        "nested/%00",
        "nested/%",
    ),
)
def test_workspace_relative_path_rejects_noncanonical_or_unsafe_reference(
    tmp_path: Path,
    suffix: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ref = _workspace_prefix(workspace) + suffix

    with pytest.raises(CanonicalizationError, match="workspace resource"):
        workspace_relative_path(workspace, ref)


def test_workspace_relative_path_rejects_reference_for_other_root(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    ref = _workspace_prefix(first) + "file.txt"

    with pytest.raises(CanonicalizationError, match="root"):
        workspace_relative_path(second, ref)


def test_workspace_relative_path_rejects_forged_symlink_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("outside", encoding="utf-8")
    (workspace / "alias").symlink_to(outside, target_is_directory=True)
    forged_ref = _workspace_prefix(workspace) + "alias/file.txt"

    with pytest.raises(CanonicalizationError, match="canonical workspace identity"):
        workspace_relative_path(workspace, forged_ref)


def test_http_resource_matches_exact_normalization_vector() -> None:
    assert (
        canonical_http_resource("HTTPS://EXAMPLE.COM:443/a/../b?z=2&a=1")
        == "https://example.com/b?a=1&z=2"
    )


def test_http_resource_rejects_oversized_url_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_parse(_url: str) -> None:
        raise AssertionError("oversized URL reached the parser")

    monkeypatch.setattr(canonicalization, "urlsplit", unexpected_parse)

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_http_resource("https://example.com/" + "a" * 9_000)


def test_http_resource_rejects_oversized_utf8_url_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_parse(_url: str) -> None:
        raise AssertionError("oversized UTF-8 URL reached the parser")

    monkeypatch.setattr(canonicalization, "urlsplit", unexpected_parse)

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_http_resource("https://example.com/" + "🙂" * 2_050)


@pytest.mark.parametrize(
    "source",
    (
        "https://example.com/" + "/".join("a" * 900 for _ in range(5)),
        "https://example.com/" + "a" * 1_025,
        "https://example.com/" + "/".join("a" for _ in range(129)),
        "https://example.com/" + "/".join("🙂" * 225 for _ in range(5)),
        "https://example.com/" + "🙂" * 257,
        "https://example.com/?" + "&".join(f"{key}={'v' * 900}" for key in "abcde"),
        "https://example.com/?" + "a" * 1_025 + "=value",
        "https://example.com/?key=" + "v" * 1_025,
        "https://example.com/?" + "&".join("a=1" for _ in range(129)),
        "https://example.com/?" + "&".join(f"{key}={'🙂' * 225}" for key in "abcde"),
        "https://example.com/?" + "🙂" * 257 + "=value",
        "https://example.com/?key=" + "🙂" * 257,
        "https://" + "é" * 131 + "/",
    ),
)
def test_http_resource_rejects_path_query_and_component_budget_overruns(
    source: str,
) -> None:
    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_http_resource(source)


def test_http_resource_rejects_path_budget_before_component_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_component_canonicalization(
        _raw: str,
        *,
        raw_safe: frozenset[str],
    ) -> str:
        _ = raw_safe
        raise AssertionError("over-budget path reached component canonicalization")

    monkeypatch.setattr(
        canonicalization,
        "_canonical_url_component",
        unexpected_component_canonicalization,
    )

    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_http_resource("https://example.com/" + "a" * 1_025)


def test_http_resource_rejects_a_very_long_decimal_port_as_canonicalization_error() -> None:
    with pytest.raises(CanonicalizationError, match="port|budget"):
        canonical_http_resource("https://example.com:" + "9" * 5_000 + "/")


def test_http_resource_rejects_port_over_digit_budget_before_integer_conversion() -> None:
    with pytest.raises(CanonicalizationError, match="budget"):
        canonical_http_resource("https://example.com:000080/")


@pytest.mark.parametrize(
    "host",
    ("xn--a.example", "xn--0.example", "xn--abc.example", "example.xn--a"),
)
def test_http_resource_rejects_invalid_idna_alabels(host: str) -> None:
    with pytest.raises(CanonicalizationError, match="IDNA"):
        canonical_http_resource(f"https://{host}/")


@pytest.mark.parametrize("host", ("xn--fa-hia.de", "xn--bcher-kva.example"))
def test_http_resource_round_trips_valid_idna2008_alabels(host: str) -> None:
    canonical = canonical_http_resource(f"HTTPS://{host.upper()}/")

    assert canonical == f"https://{host}/"
    assert canonical_http_resource(canonical) == canonical


def test_remove_dot_segments_does_not_copy_a_quadratic_input_volume() -> None:
    class CopyTrackedString(str):
        copied_characters = 0
        copy_limit = 0

        def __getitem__(self, key: int | slice) -> str:
            value = super().__getitem__(key)
            if isinstance(key, slice):
                type(self).copied_characters += len(value)
                if type(self).copied_characters > type(self).copy_limit:
                    raise AssertionError("dot-segment removal copied a quadratic input volume")
                return type(self)(value)
            return value

    path = CopyTrackedString("/" + "/".join("a" for _ in range(2_000)))
    CopyTrackedString.copy_limit = len(path) * 4

    assert canonicalization._remove_dot_segments(path) == path


@pytest.mark.parametrize(
    ("source_path", "expected_path"),
    (
        ("/a//../c", "/a/c"),
        ("/a///../c", "/a//c"),
        ("//../c", "/c"),
        ("/../c", "/c"),
        ("/a/.", "/a/"),
        ("/a/..", "/"),
        ("/a//..", "/a/"),
        ("///..", "//"),
    ),
)
def test_http_resource_preserves_empty_segment_dot_removal_edges(
    source_path: str,
    expected_path: str,
) -> None:
    assert canonical_http_resource(f"https://example.com{source_path}") == (
        f"https://example.com{expected_path}"
    )


def test_http_resource_near_budget_work_is_bounded() -> None:
    path = "/" + "/".join("p" * 1_023 for _ in range(4))
    value_sizes = (1_024, 1_024, 1_024, 993)
    query = "&".join(
        f"{key}={'v' * size}" for key, size in zip("abcd", value_sizes, strict=True)
    )
    source = f"https://example.com{path}?{query}"
    assert len(source.encode("utf-8")) == 8_192

    started = perf_counter()
    for _ in range(5):
        assert canonical_http_resource(source) == source

    assert perf_counter() - started < 1.0


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("http://Example.COM", "http://example.com/"),
        ("http://example.com:80/", "http://example.com/"),
        ("https://xn--bcher-kva.example/", "https://xn--bcher-kva.example/"),
        ("https://127.0.0.1:443/", "https://127.0.0.1/"),
        (
            "https://[2001:0db8:0000:0000:0000:0000:0000:0001]:443/",
            "https://[2001:db8::1]/",
        ),
    ),
)
def test_http_resource_normalizes_authority(source: str, expected: str) -> None:
    assert canonical_http_resource(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "https://example.com/caf%C3%A9/%7e/%2f",
            "https://example.com/caf%C3%A9/~/%2F",
        ),
        (
            "https://example.com/a//%2e/b/%2E%2E/c/",
            "https://example.com/a//c/",
        ),
        (
            "https://example.com/a/%2e%2e/b/./c",
            "https://example.com/b/c",
        ),
        (
            "https://example.com/a%2Fb/%3A%40",
            "https://example.com/a%2Fb/%3A%40",
        ),
        (
            "https://example.com/한%20글",
            "https://example.com/%ED%95%9C%20%EA%B8%80",
        ),
    ),
)
def test_http_resource_canonicalizes_path_without_changing_reserved_structure(
    source: str,
    expected: str,
) -> None:
    assert canonical_http_resource(source) == expected


def test_http_resource_preserves_query_multiplicity_presence_and_plus_semantics() -> None:
    source = "https://example.com/?b=2&a=&a&a=+&a=%2b&b=1=2&a=+"

    assert canonical_http_resource(source) == ("https://example.com/?a=&a&a=+&a=%2B&a=+&b=2&b=1=2")


def test_http_resource_preserves_duplicate_key_order_while_sorting_unique_keys() -> None:
    victim_first = canonical_http_resource(
        "https://example.com/?z=2&a=victim&a=attacker&b=1"
    )
    attacker_first = canonical_http_resource(
        "https://example.com/?z=2&a=attacker&a=victim&b=1"
    )

    assert victim_first == "https://example.com/?a=victim&a=attacker&b=1&z=2"
    assert attacker_first == "https://example.com/?a=attacker&a=victim&b=1&z=2"
    assert victim_first != attacker_first


def test_http_resource_sorts_query_stably_after_component_canonicalization() -> None:
    source = "https://example.com/?z=%7e&%61=2&a=1&a=1"

    assert canonical_http_resource(source) == "https://example.com/?a=2&a=1&a=1&z=~"


@pytest.mark.parametrize(
    "source",
    (
        "ftp://example.com/file",
        "https:///missing-host",
        "https://user@example.com/",
        "https://user:pass@example.com/",
        "https://example.com/path#fragment",
        "https://example.com/path#",
        "https://example.com/bad path",
        "https://example.com/\npath",
        "https://example.com/%",
        "https://example.com/%0G",
        "https://example.com/%FF",
        "https://exa%mple.com/",
        "https://example.com:not-a-port/",
        "https://example.com:0/",
        "https://example.com:65536/",
        "https://[fe80::1%25eth0]/",
        "https://[not-ip]/",
        "https://256.1.1.1/",
    ),
)
def test_http_resource_rejects_ambiguous_or_malformed_urls(source: str) -> None:
    with pytest.raises(CanonicalizationError):
        canonical_http_resource(source)


@pytest.mark.parametrize(
    "host",
    (
        "0x7f000001",
        "0x7f.0.0.1",
        "2130706433",
        "017700000001",
        "0177.0.0.1",
        "127.1",
        "1.2.3.4.5",
    ),
)
def test_http_resource_rejects_legacy_numeric_ipv4_spellings(host: str) -> None:
    with pytest.raises(CanonicalizationError, match="IPv4"):
        canonical_http_resource(f"http://{host}/")


@pytest.mark.parametrize("host", ("faß.de", "BÜCHER.example"))
def test_http_resource_rejects_raw_non_ascii_hosts(host: str) -> None:
    with pytest.raises(CanonicalizationError, match="ASCII"):
        canonical_http_resource(f"https://{host}/")


def test_http_resource_keeps_ascii_idna_target_distinct_from_lookalike() -> None:
    sharp_s_target = canonical_http_resource("https://xn--fa-hia.de/")
    ascii_lookalike = canonical_http_resource("https://fass.de/")

    assert sharp_s_target == "https://xn--fa-hia.de/"
    assert ascii_lookalike == "https://fass.de/"
    assert sharp_s_target != ascii_lookalike


@pytest.mark.parametrize(
    "query_key",
    (
        "api_token",
        "client-secret",
        "Authorization",
        "session_token",
    ),
)
def test_http_resource_rejects_secret_classified_query_keys(query_key: str) -> None:
    with pytest.raises(CanonicalizationError, match="secret"):
        canonical_http_resource(f"https://example.com/?{query_key}=not-a-real-value")


@pytest.mark.parametrize(
    "raw_query",
    (
        "X-Amz-Signature=placeholder",
        "x-goog-signature=placeholder",
        "SiGnAtUrE=placeholder",
        "SIG=placeholder",
        "%58%2D%41mz%2D%53ignature=placeholder",
        "x-goog-%73ignature=placeholder",
        "%53ignature=placeholder",
        "%73ig=placeholder",
        "KeY",
        "%6bey=placeholder",
        "ACCESS-KEY",
        "%61ccess-%6bey=placeholder",
    ),
)
def test_http_resource_rejects_signature_and_exact_bare_key_queries(
    raw_query: str,
) -> None:
    with pytest.raises(CanonicalizationError, match="secret"):
        canonical_http_resource(f"https://example.com/?{raw_query}")


@pytest.mark.parametrize(
    "raw_query",
    (
        "next=x-goog-signature%3Dplaceholder",
        "next=signature%3Dplaceholder",
        "next=AK%49A01234567",
    ),
)
def test_http_resource_rejects_secret_shaped_query_values(raw_query: str) -> None:
    with pytest.raises(CanonicalizationError, match="secret"):
        canonical_http_resource(f"https://example.com/?{raw_query}")


def test_http_resource_does_not_treat_bare_key_substring_as_secret() -> None:
    assert (
        canonical_http_resource("https://example.com/?monkey=value")
        == "https://example.com/?monkey=value"
    )


@pytest.mark.parametrize(
    "source",
    (
        "https://example.com/",
        "https://example.com/a//b/?a&a=&x=+&x=%2B",
        "https://xn--bcher-kva.example/caf%C3%A9?a=1&z=2",
        "https://[2001:db8::1]/a%2Fb",
    ),
)
def test_http_resource_is_idempotent(source: str) -> None:
    canonical = canonical_http_resource(source)

    assert canonical_http_resource(canonical) == canonical
