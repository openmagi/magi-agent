import hashlib

import pytest

from magi_agent.computer.autonomous.installer import (
    CUA_DRIVER_VERSION,
    InstallError,
    gatekeeper_note,
    release_asset_url,
    verify_sha256,
)


def test_version_is_pinned() -> None:
    assert CUA_DRIVER_VERSION and CUA_DRIVER_VERSION[0].isdigit()


def test_release_url_contains_version_and_arch() -> None:
    url = release_asset_url(CUA_DRIVER_VERSION, "arm64")
    assert url.startswith("https://github.com/trycua/cua/releases/download/")
    assert CUA_DRIVER_VERSION in url
    assert "arm64" in url


def test_verify_sha256_ok() -> None:
    data = b"hello"
    verify_sha256(data, hashlib.sha256(data).hexdigest())  # no raise


def test_verify_sha256_mismatch_raises() -> None:
    with pytest.raises(InstallError):
        verify_sha256(b"hello", "deadbeef")


def test_gatekeeper_note_mentions_unsigned() -> None:
    note = gatekeeper_note().casefold()
    assert "gatekeeper" in note or "unidentified" in note
