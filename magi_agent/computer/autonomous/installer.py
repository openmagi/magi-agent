from __future__ import annotations

import hashlib

# Pinned cua-driver release. Bump deliberately; never track "latest".
# NOTE: the version + asset-URL shape below are UNVERIFIED against real trycua
# releases (upstream installs via libs/cua-driver/scripts/install.sh and ships a
# `CuaDriver.app` tarball). Before flag-on, confirm the actual tag + asset name
# at https://github.com/trycua/cua/releases and the published sha256, then update
# CUA_DRIVER_VERSION, release_asset_url(), and the bundled checksum. The unit
# test only checks substrings, so a green test does NOT prove the URL resolves.
CUA_DRIVER_VERSION = "0.5.0"
_RELEASE_BASE = "https://github.com/trycua/cua/releases/download"


class InstallError(RuntimeError):
    """Raised when the cua-driver download fails integrity verification."""


def release_asset_url(version: str, arch: str) -> str:
    """GitHub Releases URL for the cua-driver darwin tarball of ``arch``.

    UNVERIFIED shape — see module note; confirm against real releases.
    """
    return f"{_RELEASE_BASE}/cua-driver-v{version}/cua-driver-{version}-darwin-{arch}.tar.gz"


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
