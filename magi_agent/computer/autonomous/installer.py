from __future__ import annotations

import hashlib

# Pinned cua-driver release. Bump deliberately; never track "latest".
# Verified against real trycua releases (cua-driver 0.5.7): the Rust driver ships
# as tag `cua-driver-rs-v<version>` with darwin assets
# `cua-driver-rs-<version>-darwin-{arm64,x86_64}.tar.gz` plus a `checksums.txt`
# (`<sha256>  <asset>` lines). The asset extracts to `cua-driver` + `CuaDriver.app`.
CUA_DRIVER_VERSION = "0.5.7"
_RELEASE_BASE = "https://github.com/trycua/cua/releases/download"


class InstallError(RuntimeError):
    """Raised when the cua-driver download fails integrity verification."""


def _release_tag(version: str) -> str:
    return f"cua-driver-rs-v{version}"


def asset_name(version: str, arch: str) -> str:
    """The darwin tarball asset name for ``arch`` (``arm64`` or ``x86_64``)."""
    return f"cua-driver-rs-{version}-darwin-{arch}.tar.gz"


def release_asset_url(version: str, arch: str) -> str:
    """GitHub Releases URL for the cua-driver darwin tarball of ``arch``."""
    return f"{_RELEASE_BASE}/{_release_tag(version)}/{asset_name(version, arch)}"


def checksums_url(version: str) -> str:
    """GitHub Releases URL for the release's ``checksums.txt``."""
    return f"{_RELEASE_BASE}/{_release_tag(version)}/checksums.txt"


def parse_checksums(text: str, asset: str) -> str:
    """Return the sha256 hex for ``asset`` from a ``checksums.txt`` body.

    Lines are ``<sha256>  <asset-name>``. Raises InstallError if ``asset`` is
    absent (never silently skip integrity verification).
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == asset:
            return parts[0]
    raise InstallError(f"no checksum for {asset!r} in checksums.txt")


def verify_sha256(data: bytes, expected_hex: str) -> None:
    """Raise InstallError unless ``data`` hashes to ``expected_hex``."""
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_hex:
        raise InstallError(
            f"cua-driver checksum mismatch: expected {expected_hex}, got {actual}"
        )


def gatekeeper_note() -> str:
    return (
        "cua-driver is not Apple-notarized. macOS Gatekeeper may block it on first "
        "run as from an 'unidentified developer'. Approve it in System Settings > "
        "Privacy & Security, and grant the controlling terminal Accessibility + "
        "Screen Recording permissions, or the tool cannot capture or click."
    )
