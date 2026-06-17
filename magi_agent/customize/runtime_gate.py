"""Runtime-side query for Customize verification preset state.

Engine satisfiers call :func:`preset_enabled` to honor a dashboard opt-in toggle
(e.g. ``fact-grounding``) in addition to that satisfier's own ``MAGI_*`` env flag,
so enabling the preset turns the existing enforcement path on for the runtime.

Self-contained (loads ``~/.magi/customize.json`` directly, like the assembly-side
wiring) and flag-gated by ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED``. Fail-CLOSED to
``False`` on any error so a bad overrides file can never spuriously enable a gate.
"""

from __future__ import annotations


def preset_enabled(preset_id: str, *, default: bool) -> bool:
    """True if the Customize tab resolves this verification preset enabled.

    ``default`` is the preset's runtime default (used when the user never set an
    explicit override). Returns ``False`` unless
    ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` is on.
    """
    from magi_agent.config.flags import flag_profile_bool

    if not flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED"):
        return False
    try:
        from magi_agent.customize.store import load_overrides
        from magi_agent.customize.verification_policy import CustomizeVerificationPolicy

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        return policy.resolve_enabled(preset_id, default=default)
    except Exception:
        return False
