"""SSRF preflight for remote media (video/audio URL) acquisition.

This module gates user-supplied media URLs (e.g. YouTube/page URLs handed to
``video_frames`` and the audio URL transcribe path) using the SAME static
string-level classifier (``classify_network_url``) that ``process.py`` and
``browser.py`` already rely on.

SECURITY SCOPE — READ BEFORE EXTENDING:
``assert_media_url_allowed()`` validates ONLY the user-supplied URL string. It
parses the URL and flags literal private/metadata IPs (including legacy-encoded
forms), userinfo/query credentials, and nested-query URL smuggling. It does
NOT perform DNS resolution and does NOT inspect yt-dlp's redirect chain or the
resolved CDN/extractor URLs. Therefore a public hostname that resolves to a
private/metadata IP (DNS rebinding), an HTTP 30x redirect to a private host, or
an extractor that fetches an internal URL will PASS this preflight. This is a
string-level preflight only — matching the existing process.py/browser.py
guards — NOT complete SSRF protection. DNS/redirect resolution is intentionally
out of scope here to avoid diverging from the shared helper (see design doc).
"""

from __future__ import annotations

from magi_agent.sandbox.network import classify_network_url


class MediaEgressBlocked(Exception):
    """Raised when a media URL is blocked by the static egress preflight.

    ``reason_code`` is the first blocking ``classify_network_url`` reason and is
    intended to be surfaced as the ``message`` arg of
    ``spreadsheet_tools._blocked_result``.
    """

    def __init__(self, reason_code: str, reasons: tuple[str, ...]) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.reasons = reasons


def assert_media_url_allowed(url: str) -> None:
    """Validate a user-supplied media URL string and raise on a policy block.

    NOTE: This validates ONLY the supplied URL string — not post-resolution or
    redirect targets (no DNS resolution, no redirect-chain inspection). See the
    module docstring SECURITY SCOPE section.
    """
    _host, reasons = classify_network_url(url)
    if reasons:
        raise MediaEgressBlocked(reasons[0], reasons)


__all__ = ["MediaEgressBlocked", "assert_media_url_allowed"]
