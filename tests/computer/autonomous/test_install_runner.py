"""Tests for the install orchestrator.

Network IO is faked via an injected downloader. Filesystem IO is real but
sandboxed inside ``tmp_path``: we build a tiny fake cua-driver tarball with the
real layout (``cua-driver-rs-<v>-darwin-<arch>/{cua-driver,CuaDriver.app}``) and
run the full install pipeline against it.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest

from magi_agent.computer.autonomous.install_runner import (
    InstallError,
    InstallReport,
    extract_tarball,
    install,
    install_app_bundle,
    symlink_binary,
)
from magi_agent.computer.autonomous.installer import asset_name, checksums_url, release_asset_url


_VERSION = "0.5.7"
_ARCH = "arm64"


def _build_fake_tarball(version: str = _VERSION, arch: str = _ARCH) -> bytes:
    """Real .tar.gz mirroring the cua-driver layout, but with stub files."""
    top = f"cua-driver-rs-{version}-darwin-{arch}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def _add(name: str, payload: bytes, mode: int = 0o644) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            tar.addfile(info, io.BytesIO(payload))

        _add(f"{top}/cua-driver", b"stub-binary", mode=0o755)
        _add(f"{top}/CuaDriver.app/Contents/MacOS/cua-driver", b"stub-binary", mode=0o755)
        _add(f"{top}/CuaDriver.app/Contents/Info.plist", b"<plist/>")
    return buf.getvalue()


def _fake_downloader(version: str, arch: str, tar_bytes: bytes):
    sha = hashlib.sha256(tar_bytes).hexdigest()
    checksums_body = f"{sha}  {asset_name(version, arch)}\n".encode()
    asset_u = release_asset_url(version, arch)
    checksums_u = checksums_url(version)

    def _download(url: str) -> bytes:
        if url == checksums_u:
            return checksums_body
        if url == asset_u:
            return tar_bytes
        raise AssertionError(f"unexpected download URL: {url}")

    return _download


def test_extract_tarball_returns_top_dir(tmp_path: Path) -> None:
    tar = _build_fake_tarball()
    root = extract_tarball(tar, tmp_path / "ex")
    assert root.is_dir()
    assert root.name == f"cua-driver-rs-{_VERSION}-darwin-{_ARCH}"
    assert (root / "CuaDriver.app" / "Contents" / "MacOS" / "cua-driver").is_file()


def test_extract_tarball_rejects_path_traversal(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("../escape")
        info.size = 0
        tar.addfile(info, io.BytesIO(b""))
    with pytest.raises(InstallError):
        extract_tarball(buf.getvalue(), tmp_path / "ex")


def test_install_app_bundle_idempotent(tmp_path: Path) -> None:
    tar = _build_fake_tarball()
    root = extract_tarball(tar, tmp_path / "ex")
    apps = tmp_path / "Applications"
    first = install_app_bundle(root, apps)
    # Tamper with the install, then re-run; the new content must replace it.
    (first / "Contents" / "tamper.txt").write_text("dirty")
    second = install_app_bundle(root, apps)
    assert first == second
    assert not (second / "Contents" / "tamper.txt").exists()


def test_install_app_bundle_missing_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InstallError):
        install_app_bundle(empty, tmp_path / "Applications")


def test_symlink_binary_idempotent_and_points_inside_app(tmp_path: Path) -> None:
    tar = _build_fake_tarball()
    root = extract_tarball(tar, tmp_path / "ex")
    apps = tmp_path / "Applications"
    app = install_app_bundle(root, apps)
    bin_dir = tmp_path / "bin"
    link = symlink_binary(app, bin_dir)
    assert link.is_symlink()
    assert os.readlink(link) == str(app / "Contents" / "MacOS" / "cua-driver")
    # Re-symlinking does not raise even though the link exists.
    second = symlink_binary(app, bin_dir)
    assert second == link


def test_install_end_to_end_with_fake_downloader(tmp_path: Path) -> None:
    tar = _build_fake_tarball()
    report = install(
        version=_VERSION,
        arch=_ARCH,
        applications=tmp_path / "Applications",
        bin_dir=tmp_path / "bin",
        download=_fake_downloader(_VERSION, _ARCH, tar),
    )
    assert isinstance(report, InstallReport)
    assert report.version == _VERSION
    assert report.arch == _ARCH
    assert Path(report.app_path).is_dir()
    assert Path(report.binary_symlink).is_symlink()
    assert report.sha256 == hashlib.sha256(tar).hexdigest()


def test_install_rejects_corrupt_download(tmp_path: Path) -> None:
    tar = _build_fake_tarball()
    download = _fake_downloader(_VERSION, _ARCH, tar)

    def _corrupted(url: str) -> bytes:
        data = download(url)
        if url == release_asset_url(_VERSION, _ARCH):
            return data + b"\x00CORRUPT"
        return data

    with pytest.raises(InstallError):
        install(
            version=_VERSION,
            arch=_ARCH,
            applications=tmp_path / "Applications",
            bin_dir=tmp_path / "bin",
            download=_corrupted,
        )


def test_install_rejects_missing_checksum_line(tmp_path: Path) -> None:
    tar = _build_fake_tarball()

    def _download(url: str) -> bytes:
        if url == checksums_url(_VERSION):
            # checksums.txt for a *different* asset only — no entry for ours.
            return b"deadbeef  some-other.tar.gz\n"
        if url == release_asset_url(_VERSION, _ARCH):
            return tar
        raise AssertionError(url)

    with pytest.raises(InstallError):
        install(
            version=_VERSION,
            arch=_ARCH,
            applications=tmp_path / "Applications",
            bin_dir=tmp_path / "bin",
            download=_download,
        )
