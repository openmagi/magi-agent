"""E-4 — single source of truth for ``_KNOWN_TOKEN_LIMITS``.

``runtime/message_builder._KNOWN_TOKEN_LIMITS`` and
``context/token_tracker._KNOWN_TOKEN_LIMITS`` were byte-identical 35-entry
dicts kept in lockstep by a comment ("Mirrors message_builder.py
_KNOWN_TOKEN_LIMITS exactly"). The two were a dedup waiting to happen:
``adk_bridge/context_compaction.py`` already imports the
``token_tracker`` copy as canonical. This test locks in the consolidation
— ``message_builder._KNOWN_TOKEN_LIMITS`` is now the same object as
``token_tracker._KNOWN_TOKEN_LIMITS`` (the interim half of E-4; the
structural half routes everyone through the catalog in a follow-up).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_message_builder_reuses_token_tracker_limits() -> None:
    from magi_agent.context import token_tracker
    from magi_agent.runtime import message_builder

    assert (
        message_builder._KNOWN_TOKEN_LIMITS is token_tracker._KNOWN_TOKEN_LIMITS
    ), (
        "message_builder._KNOWN_TOKEN_LIMITS must be the same object as "
        "context/token_tracker._KNOWN_TOKEN_LIMITS — second literal blocked"
    )


def test_message_builder_get_call_site_resolves_through_shared_dict() -> None:
    """The call site ``message_builder.py:1001 _KNOWN_TOKEN_LIMITS.get(model)``
    must resolve via the shared leaf dict (not a stale copy). Mutating the
    leaf flows through both ``token_tracker`` and ``message_builder``
    re-exports."""

    from magi_agent.context import _token_window_table, token_tracker
    from magi_agent.runtime import message_builder

    _token_window_table._KNOWN_TOKEN_LIMITS["e4-test-sentinel-model"] = 12345
    try:
        assert (
            message_builder._KNOWN_TOKEN_LIMITS.get("e4-test-sentinel-model") == 12345
        )
        assert (
            token_tracker._KNOWN_TOKEN_LIMITS.get("e4-test-sentinel-model") == 12345
        )
    finally:
        _token_window_table._KNOWN_TOKEN_LIMITS.pop(
            "e4-test-sentinel-model", None
        )


def test_only_token_tracker_defines_known_token_limits_literal() -> None:
    """Meta-test: forbid a second module from defining a
    ``_KNOWN_TOKEN_LIMITS = {`` literal. ``token_tracker.py`` is the sole
    canonical home (interim — catalog is the final home in the E-1
    follow-up).
    """

    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists(), f"package root not found near {package_root}"

    offenders = []
    # The stdlib-only leaf ``context/_token_window_table.py`` is the sole
    # canonical home for the literal (E-4). Both ``token_tracker.py`` and
    # ``message_builder.py`` re-export from it without redeclaring.
    canonical = {"_token_window_table.py"}
    for path in package_root.rglob("*.py"):
        if path.name in canonical:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "_KNOWN_TOKEN_LIMITS = {" in text:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == [], (
        "Found a second ``_KNOWN_TOKEN_LIMITS = {`` literal — "
        "context/_token_window_table.py is the sole canonical home (E-4). "
        f"Offenders: {offenders}"
    )
