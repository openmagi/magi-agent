"""Hosted-deployment control-stage overlay (doc 14 PR1 — observability only).

Mirrors :mod:`magi_agent.runtime.local_defaults` but for *hosted* bots
(real ``bot_id``/``user_id``/``gateway_token``). Hosted pods today run with all
ControlPlane/observability/introspection controls OFF because
:func:`apply_local_full_runtime_defaults` is gated on the local-dev identity.

This module adds an explicit, operator-driven overlay keyed on a single
``MAGI_CONTROL_STAGE`` variable so the verified-locally controls become
*flippable* on hosted without changing any code-level default. The overlay only
applies when the deployment is explicitly marked hosted
(``MAGI_DEPLOYMENT=hosted``) — never via reverse-detection — and uses
``setdefault`` so explicit operator env always wins.

PR1 scope: ``observability`` (C8) only. Stages and additional keys are added by
later PRs (resilience / full controls / hardgate). The default stage is ``off``,
which is byte-identical to today's hosted runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

CONTROL_STAGE_ENV = "MAGI_CONTROL_STAGE"
HOSTED_DEPLOYMENT_ENV = "MAGI_DEPLOYMENT"

DEFAULT_CONTROL_STAGE = "off"
CONTROL_STAGES = ("off", "resilience", "full", "hardgate")

# Hosted PVC subPath for the observability SQLite DB. The hosted rootfs is
# read-only; the workspace PVC is the only writable mount, so the observability
# events DB must live there (not the cwd ``.openmagi``).
HOSTED_OBS_HOME = "/workspace/.openmagi"

# Stage -> env overlay. PR1 only wires the observability (C8) keys; later PRs
# extend each stage with their own control flags. Stages are additive: ``full``
# implies everything in ``resilience``, etc. Because PR1 only owns observability,
# the lower stages contribute nothing here yet.
HOSTED_CONTROL_STAGE_DEFAULTS: Mapping[str, Mapping[str, str]] = {
    "off": {},
    "resilience": {},
    "full": {
        "MAGI_OBSERVABILITY_ENABLED": "1",
        "MAGI_OBS_HOME": HOSTED_OBS_HOME,
    },
    "hardgate": {
        "MAGI_OBSERVABILITY_ENABLED": "1",
        "MAGI_OBS_HOME": HOSTED_OBS_HOME,
    },
}


def is_hosted_deployment(environ: Mapping[str, str]) -> bool:
    """True only when the operator explicitly marks the deployment hosted.

    We require an explicit ``MAGI_DEPLOYMENT=hosted`` marker rather than
    reverse-detecting from the absence of the local-dev identity, which is
    fragile (see doc 14 open-decision #2).
    """

    raw = environ.get(HOSTED_DEPLOYMENT_ENV)
    return raw is not None and raw.strip().lower() == "hosted"


def resolve_control_stage(environ: Mapping[str, str]) -> str:
    """Resolve the requested control stage, failing safe to ``off``.

    Unknown / empty values fall back to ``off`` so a typo never silently flips
    a more aggressive stage.
    """

    raw = (environ.get(CONTROL_STAGE_ENV) or "").strip().lower()
    if raw in CONTROL_STAGES:
        return raw
    return DEFAULT_CONTROL_STAGE


def apply_hosted_runtime_defaults(environ: MutableMapping[str, str]) -> None:
    """Overlay the hosted control-stage defaults onto ``environ`` in place.

    No-op unless the deployment is explicitly hosted. ``setdefault`` semantics:
    explicit operator env always wins. Stage ``off`` (the default) sets nothing,
    keeping hosted byte-identical to today.
    """

    if not is_hosted_deployment(environ):
        return
    stage = resolve_control_stage(environ)
    for key, value in HOSTED_CONTROL_STAGE_DEFAULTS.get(stage, {}).items():
        environ.setdefault(key, value)
