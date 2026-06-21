"""I-2 PR B per-channel behaviour-parity tests for the four ``channels/*_live.py``
gates.

Before this PR, each of ``is_live_{telegram,slack,email,discord}_enabled``
used a DENYLIST check (``bool(raw) and raw.lower() not in
{"0","false","no","off"}``) which silently ENABLED the channel on any
non-empty value that wasn't an explicit falsey spelling — including typos like
``MAGI_CHANNEL_LIVE_X="disabled"`` / ``"enabled"`` / ``"random_garbage"``.
This is a stage-3 live side-effect with the worst-possible "fail open"
direction (the explicit string ``disabled`` reads as ON).

PR B converts all four to the canonical strict-allowlist semantics from
``magi_agent.config._truthy.env_bool``. This module asserts the per-channel
matrix:

* Unset → False (default-OFF, unchanged).
* Truthy spellings (``1``/``true``/``yes``/``on``, case-insensitive,
  whitespace-trimmed) → True (UNCHANGED for the spellings the denylist also
  accepted as ON).
* Explicit falsey (``0``/``false``/``no``/``off``, empty string) → False
  (UNCHANGED).
* Unknown / mis-typed values (``disabled``, ``yes please``, ``enabled``,
  ``random_garbage``) → False (CHANGED — was True under the denylist; this is
  the I-2 security correction).

Twelve inputs × four channels ≈ 48 cases (plus the four unset-reads-False
sanity cases = 52 parametrised cases). Default-OFF stays default-OFF;
operators relying on a non-truthy value to enable a channel must update to a
proper truthy value — see PR body.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Per-channel registry — (env var, reader importable from channels module).
# ---------------------------------------------------------------------------

_CHANNELS: list[tuple[str, str, str]] = [
    # (channel_id, env_name, "module.path:attr")
    ("telegram", "MAGI_CHANNEL_LIVE_TELEGRAM", "magi_agent.channels.telegram_live:is_live_telegram_enabled"),
    ("slack",    "MAGI_CHANNEL_LIVE_SLACK",    "magi_agent.channels.slack_live:is_live_slack_enabled"),
    ("email",    "MAGI_CHANNEL_LIVE_EMAIL",    "magi_agent.channels.email_live:is_live_email_enabled"),
    ("discord",  "MAGI_CHANNEL_LIVE_DISCORD",  "magi_agent.channels.discord_live:is_live_discord_enabled"),
]


# Truthy spellings that BOTH denylist and allowlist accepted as ON — UNCHANGED.
_TRUTHY_INPUTS: tuple[str, ...] = ("1", "true", "yes", "on", "TRUE", "Yes")

# Explicit falsey values — UNCHANGED.
_EXPLICIT_FALSEY_INPUTS: tuple[str, ...] = ("0", "false", "no", "off", "")

# The dangerous values: each was previously silently ENABLING the gate under
# the denylist semantic; now they correctly read as OFF. This is the I-2 PR B
# security correction — the headline behaviour change of this PR.
_BAD_VALUES_PREVIOUSLY_ENABLING: tuple[str, ...] = (
    "disabled",       # the worst — the literal string operators reach for
    "enabled",        # a typo'd alternative truthy spelling
    "random_garbage",
    "yes please",     # space-prefix / suffix variant
)


def _read(reader_ref: str) -> bool:
    """Import ``module:attr`` and invoke the zero-arg reader."""
    import importlib

    module_path, _, attr = reader_ref.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, attr)()


# ---------------------------------------------------------------------------
# Default — unset
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("channel", "env_name", "reader_ref"), _CHANNELS)
def test_unset_reads_false(
    channel: str,
    env_name: str,
    reader_ref: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four channels are strict default-OFF: unset env reads as False."""
    monkeypatch.delenv(env_name, raising=False)
    assert _read(reader_ref) is False, f"channel {channel}: unset must read False"


# ---------------------------------------------------------------------------
# Truthy spellings — UNCHANGED (allowlist still accepts these)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("channel", "env_name", "reader_ref"), _CHANNELS)
@pytest.mark.parametrize("value", _TRUTHY_INPUTS)
def test_truthy_spellings_read_true(
    channel: str,
    env_name: str,
    reader_ref: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical truthy spellings (case-insensitive) enable the channel.

    This is the parity assertion — every spelling that was True under the
    denylist semantic must still be True under the allowlist.
    """
    monkeypatch.setenv(env_name, value)
    assert _read(reader_ref) is True, f"channel {channel}: {value!r} must read True"


# ---------------------------------------------------------------------------
# Explicit falsey — UNCHANGED
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("channel", "env_name", "reader_ref"), _CHANNELS)
@pytest.mark.parametrize("value", _EXPLICIT_FALSEY_INPUTS)
def test_explicit_falsey_reads_false(
    channel: str,
    env_name: str,
    reader_ref: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit falsey spellings — and the empty string — disable the
    channel under both conventions.
    """
    monkeypatch.setenv(env_name, value)
    assert _read(reader_ref) is False, f"channel {channel}: {value!r} must read False"


# ---------------------------------------------------------------------------
# CHANGED — previously-enabling typos now correctly disable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("channel", "env_name", "reader_ref"), _CHANNELS)
@pytest.mark.parametrize("value", _BAD_VALUES_PREVIOUSLY_ENABLING)
def test_dangerous_values_now_read_false_was_true_under_denylist(
    channel: str,
    env_name: str,
    reader_ref: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The I-2 PR B security correction.

    Under the previous denylist semantic these values silently ENABLED the
    live channel — the worst direction (``MAGI_CHANNEL_LIVE_X="disabled"``
    reading as ON). Under the canonical allowlist they correctly read as
    OFF; the operator must use a proper truthy spelling (``1`` / ``true`` /
    ``yes`` / ``on``) to enable the channel.

    Per the I-2 plan: operators relying on a non-truthy value to enable a
    channel are "vanishingly rare and a latent bug itself" — but worth
    catching, hence this dedicated table.
    """
    monkeypatch.setenv(env_name, value)
    assert _read(reader_ref) is False, (
        f"channel {channel}: {value!r} previously silently ENABLED the gate "
        "under the denylist semantic. PR B converts to the canonical "
        "strict-allowlist; this value MUST now read as False."
    )
