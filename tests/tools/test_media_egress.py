"""Tests for the remote-media SSRF preflight (media_egress).

The preflight is a STRING-LEVEL classification of a user-supplied media URL,
reusing the shared ``classify_network_url`` helper. It is NOT a full SSRF
guard: it does NOT resolve DNS and does NOT inspect yt-dlp redirect/extractor
chains. These tests pin the static-classification contract only.
"""

from __future__ import annotations

import pytest

from magi_agent.tools.media_egress import (
    MediaEgressBlocked,
    assert_media_url_allowed,
)


class TestAssertMediaUrlAllowed:
    def test_public_youtube_url_passes(self) -> None:
        # Should not raise for a normal public video URL.
        assert_media_url_allowed("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_private_network_url_blocked(self) -> None:
        with pytest.raises(MediaEgressBlocked) as exc:
            assert_media_url_allowed("http://192.168.0.1/video.mp4")
        assert exc.value.reason_code == "private_network_blocked"

    def test_localhost_blocked(self) -> None:
        with pytest.raises(MediaEgressBlocked) as exc:
            assert_media_url_allowed("http://localhost:8080/x.mp4")
        assert exc.value.reason_code == "private_network_blocked"

    def test_metadata_endpoint_blocked(self) -> None:
        with pytest.raises(MediaEgressBlocked) as exc:
            assert_media_url_allowed("http://169.254.169.254/latest/meta-data/")
        # 169.254.169.254 is both link-local (private) and the metadata host;
        # the classifier emits private_network_blocked first then metadata.
        assert "metadata_endpoint_blocked" in exc.value.reasons
        assert "private_network_blocked" in exc.value.reasons

    def test_metadata_hostname_blocked(self) -> None:
        with pytest.raises(MediaEgressBlocked) as exc:
            assert_media_url_allowed("http://metadata.google.internal/x.mp4")
        assert exc.value.reason_code == "metadata_endpoint_blocked"

    def test_legacy_encoded_private_ip_blocked(self) -> None:
        # 0x7f.0.0.1 -> 127.0.0.1 (loopback) via legacy IPv4 decoding.
        with pytest.raises(MediaEgressBlocked):
            assert_media_url_allowed("http://0x7f.0.0.1/x.mp4")

    def test_non_http_scheme_blocked(self) -> None:
        with pytest.raises(MediaEgressBlocked) as exc:
            assert_media_url_allowed("file:///etc/passwd")
        assert exc.value.reason_code == "invalid_url_blocked"

    def test_credentialed_signed_media_url_blocked(self) -> None:
        # CONSCIOUS TRADEOFF: legitimate signed CDN media URLs carrying a
        # ?token=/?secret= query are classified as credential_url_blocked.
        # This protects the primary YouTube/page-URL use case at the cost of
        # blocking some legitimate signed direct-media URLs. Documented in the
        # design SSRF section.
        with pytest.raises(MediaEgressBlocked) as exc:
            assert_media_url_allowed(
                "https://cdn.example.com/audio.mp3?token=abc123&expires=999"
            )
        assert exc.value.reason_code == "credential_url_blocked"

    def test_userinfo_credentials_blocked(self) -> None:
        with pytest.raises(MediaEgressBlocked) as exc:
            assert_media_url_allowed("https://user:pass@cdn.example.com/a.mp4")
        assert exc.value.reason_code == "credential_url_blocked"

    def test_block_carries_message_for_block_result(self) -> None:
        # The reason_code is suitable as the _blocked_result message arg.
        try:
            assert_media_url_allowed("http://10.0.0.5/x.mp4")
        except MediaEgressBlocked as exc:
            assert isinstance(exc.reason_code, str)
            assert exc.reason_code
        else:  # pragma: no cover
            pytest.fail("expected MediaEgressBlocked")
