import hashlib

import pytest

from magi_agent.computer.autonomous.installer import (
    CUA_DRIVER_VERSION,
    InstallError,
    asset_name,
    checksums_url,
    gatekeeper_note,
    parse_checksums,
    release_asset_url,
    verify_sha256,
)


def test_version_is_pinned() -> None:
    assert CUA_DRIVER_VERSION and CUA_DRIVER_VERSION[0].isdigit()


def test_release_url_matches_real_trycua_shape() -> None:
    url = release_asset_url(CUA_DRIVER_VERSION, "arm64")
    assert url.startswith("https://github.com/trycua/cua/releases/download/")
    assert f"cua-driver-rs-v{CUA_DRIVER_VERSION}/" in url
    assert url.endswith(f"cua-driver-rs-{CUA_DRIVER_VERSION}-darwin-arm64.tar.gz")


def test_asset_name_per_arch() -> None:
    assert asset_name("0.5.7", "arm64") == "cua-driver-rs-0.5.7-darwin-arm64.tar.gz"
    assert asset_name("0.5.7", "x86_64") == "cua-driver-rs-0.5.7-darwin-x86_64.tar.gz"


def test_checksums_url() -> None:
    url = checksums_url(CUA_DRIVER_VERSION)
    assert url.endswith(f"cua-driver-rs-v{CUA_DRIVER_VERSION}/checksums.txt")


def test_parse_checksums_extracts_sha() -> None:
    text = (
        "07c3fa9c32930cd7bf668527a0c07931323c533fb13c1af81a3d9f2e7b570ede  "
        "cua-driver-rs-0.5.7-darwin-arm64.tar.gz\n"
        "a1c47900b05652989d20eead907a619527f64546e029230569e478513ab91daa  "
        "cua-driver-rs-0.5.7-darwin-x86_64.tar.gz\n"
    )
    sha = parse_checksums(text, "cua-driver-rs-0.5.7-darwin-arm64.tar.gz")
    assert sha == "07c3fa9c32930cd7bf668527a0c07931323c533fb13c1af81a3d9f2e7b570ede"


def test_parse_checksums_missing_raises() -> None:
    with pytest.raises(InstallError):
        parse_checksums("abc  other.tar.gz", "cua-driver-rs-0.5.7-darwin-arm64.tar.gz")


def test_verify_sha256_ok() -> None:
    data = b"hello"
    verify_sha256(data, hashlib.sha256(data).hexdigest())  # no raise


def test_verify_sha256_mismatch_raises() -> None:
    with pytest.raises(InstallError):
        verify_sha256(b"hello", "deadbeef")


def test_gatekeeper_note_mentions_unsigned() -> None:
    note = gatekeeper_note().casefold()
    assert "gatekeeper" in note or "unidentified" in note
