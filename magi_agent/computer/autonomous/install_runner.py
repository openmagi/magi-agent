"""Real-IO orchestrator for ``magi computer-use install``.

The pure helpers in :mod:`installer` (URL builders, checksum parse + verify) are
TDD-covered with no IO. This module is the thin glue that does the network +
filesystem work: download asset + ``checksums.txt`` over HTTPS, verify sha256,
extract the tarball, copy ``CuaDriver.app`` to ``/Applications``, symlink the
``cua-driver`` binary onto PATH. Each step is a small function that takes
injectable seams (download callable, target dirs) so it is unit-testable.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from magi_agent.computer.autonomous.installer import (
    CUA_DRIVER_VERSION,
    InstallError,
    asset_name,
    checksums_url,
    parse_checksums,
    release_asset_url,
    verify_sha256,
)

Downloader = Callable[[str], bytes]


@dataclass(frozen=True)
class InstallReport:
    """User-visible result of an install run."""

    version: str
    arch: str
    app_path: str
    binary_symlink: str
    sha256: str


def host_arch() -> str:
    """Return cua-driver's darwin arch token for this Mac."""
    import platform  # noqa: PLC0415

    return "arm64" if platform.machine() == "arm64" else "x86_64"


def http_get(url: str) -> bytes:
    """Default downloader: HTTPS GET with a 60s timeout. Raises on non-2xx."""
    request = urllib.request.Request(url, headers={"User-Agent": "magi-computer-use"})
    with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310 - URL is pinned
        return resp.read()


def extract_tarball(data: bytes, dest: Path) -> Path:
    """Extract a ``.tar.gz`` into ``dest`` and return the single top-level dir.

    cua-driver tarballs contain exactly one top-level directory
    ``cua-driver-rs-<v>-darwin-<arch>/``; the caller relies on that to find the
    app bundle and binary. We refuse any tarball that doesn't fit that shape so
    a hostile archive can't write outside ``dest``.
    """
    dest.mkdir(parents=True, exist_ok=True)
    blob = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    try:
        blob.write(data)
        blob.close()
        with tarfile.open(blob.name, "r:gz") as tar:
            members = tar.getmembers()
            top_names = {m.name.split("/", 1)[0] for m in members}
            if len(top_names) != 1:
                raise InstallError(f"unexpected tarball layout: {sorted(top_names)}")
            for m in members:
                normalized = os.path.normpath(m.name)
                if normalized.startswith("..") or os.path.isabs(normalized):
                    raise InstallError(f"refusing path traversal in tarball: {m.name}")
            tar.extractall(dest)  # noqa: S202 - layout checked above
        return dest / next(iter(top_names))
    finally:
        os.unlink(blob.name)


def install_app_bundle(extracted_root: Path, applications: Path) -> Path:
    """Copy ``CuaDriver.app`` from the extracted root into ``applications``.

    Idempotent: removes any pre-existing ``CuaDriver.app`` first so a re-install
    picks up a new version cleanly.
    """
    src = extracted_root / "CuaDriver.app"
    if not src.is_dir():
        raise InstallError(f"CuaDriver.app missing from extracted tarball at {src}")
    applications.mkdir(parents=True, exist_ok=True)
    dest = applications / "CuaDriver.app"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=True)
    return dest


def symlink_binary(app_path: Path, bin_dir: Path) -> Path:
    """Symlink the daemon binary from inside ``CuaDriver.app`` onto PATH.

    We point at the app-bundle's own ``cua-driver`` (not a sibling extracted
    one) so the binary keeps its bundle-relative resources + TCC identity.
    """
    bundle_bin = app_path / "Contents" / "MacOS" / "cua-driver"
    if not bundle_bin.is_file():
        raise InstallError(f"daemon binary missing inside app bundle: {bundle_bin}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "cua-driver"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(bundle_bin)
    return link


def install(
    *,
    version: str = CUA_DRIVER_VERSION,
    arch: str | None = None,
    applications: Path = Path("/Applications"),
    bin_dir: Path = Path.home() / ".local" / "bin",
    download: Downloader = http_get,
    workdir_factory: Callable[[], "tempfile.TemporaryDirectory[str]"] = tempfile.TemporaryDirectory,
) -> InstallReport:
    """Run the full pinned + checksum-verified install. Returns an InstallReport.

    The defaults perform real IO; tests inject a fake ``download`` and temp
    directories for both ``applications`` and ``bin_dir``.
    """
    target_arch = arch or host_arch()
    name = asset_name(version, target_arch)
    expected_sha = parse_checksums(
        download(checksums_url(version)).decode("utf-8"), name
    )
    blob = download(release_asset_url(version, target_arch))
    verify_sha256(blob, expected_sha)

    with workdir_factory() as workdir:
        extracted = extract_tarball(blob, Path(workdir))
        app_path = install_app_bundle(extracted, applications)
    link = symlink_binary(app_path, bin_dir)
    return InstallReport(
        version=version,
        arch=target_arch,
        app_path=str(app_path),
        binary_symlink=str(link),
        sha256=expected_sha,
    )
