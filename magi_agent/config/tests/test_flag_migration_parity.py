"""Parity tests for the I-1 batches 1+2+3 flag migration (21 ``is_*_enabled`` flags).

Batches 1+2 (15 strict default-OFF flags): each ``is_*_enabled`` function
under test had its inline body ``_is_true(source.get(NAME))`` replaced with
a one-line delegation to ``config.flags.flag_bool``. Because both code paths
route through the same shared truthy parser (``config._truthy.is_true``) and
the new ``FlagSpec`` entries register ``default=False``, the behaviour MUST
be byte-identical for every input the legacy form produced.

Batch 3 (6 profile-aware default-ON flags): each
``is_*_enabled`` function had its inline body
``_runtime_feature_enabled(source, NAME)`` replaced with
``config.flags.flag_profile_bool(NAME, env=source)``. Both delegate to the
same ``config._truthy.runtime_feature_enabled`` primitive — the profile-aware
resolution order (explicit true wins → explicit false wins → unset/unknown
falls back to ``runtime_profile_default_enabled``) MUST match byte-for-byte
across the unset / explicit-truthy / explicit-falsey / full-profile /
safe-profile / unknown-value matrix below. The plan-feedback note pins this
loud because flag-promotion verification (``MEMORY.md`` →
``feedback_flag_promotion_verification.md``) explicitly warned that
default-ON flags must be exercised under the ON path before flipping.

The 13-value strict-OFF table covers:

* unset (``None`` key in the env mapping),
* the canonical lower-case true literals (``1``/``true``/``on``/``yes``),
* mixed-case true literals (``TRUE``/``Yes``),
* the explicit falsey literals (``0``/``false``/``off``),
* the empty string,
* an unknown word (``garbage``) — strict opt-in: must remain ``False``,
* a whitespace-padded true literal (``  true  ``) — the truthy parser trims
  and case-folds, so this must round-trip to ``True``.

For profile-aware flags the same 13 inputs are exercised against three
profile contexts: full (unset profile → default ON), ``safe`` (→ default
OFF), ``eval`` (→ default OFF), and ``dogfood`` (→ unknown profile, treated
as full = default ON). Plus a final pair of override assertions per flag
showing explicit ``"0"`` beats both the lab and dogfood profile defaults.

If any case fails the migration is NOT byte-identical and the regression
must be rolled back before the batch ships. The plan recommends batches and
the inventory meta-test ratchets the remaining inline ``is_*_enabled`` count
down, so this file grows by one row per migrated flag in each subsequent
batch.

Reference: ``docs/plans/2026-06-18-magi-agent-oss-main-remediation/
ws-I-config-quality.md`` §I-1 (Tests / Backward-compat / migration strategy).
"""

from __future__ import annotations

import pytest

from magi_agent.config.env import (
    MAGI_AUTOMATION_METHODOLOGY_ENABLED_ENV,
    MAGI_CODING_CONTEXT_ENABLED_ENV,
    MAGI_DASHBOARD_PACK_AUTHORING_ENABLED_ENV,
    MAGI_EDIT_FORMAT_ON_WRITE_ENABLED_ENV,
    MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED_ENV,
    MAGI_FACTS_REPLAN_ENABLED_ENV,
    MAGI_GOAL_NUDGE_ENABLED_ENV,
    MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV,
    MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED_ENV,
    MAGI_MESSAGE_CACHE_ENABLED_ENV,
    MAGI_PROMPT_EXAMPLES_ENABLED_ENV,
    MAGI_PROMPT_REDFLAGS_ENABLED_ENV,
    MAGI_PROMPT_SEARCH_RULES_ENABLED_ENV,
    MAGI_READ_LEDGER_ENABLED_ENV,
    MAGI_READ_QUALITY_ENABLED_ENV,
    MAGI_RESEARCH_FACT_GUIDANCE_ENABLED_ENV,
    MAGI_RESEARCH_METHODOLOGY_ENABLED_ENV,
    MAGI_SELF_INTROSPECTION_ENABLED_ENV,
    MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED_ENV,
    MAGI_TOOL_USAGE_GUIDANCE_ENABLED_ENV,
    MAGI_USER_HOOKS_ENABLED_ENV,
    is_automation_methodology_enabled,
    is_coding_context_enabled,
    is_dashboard_pack_authoring_enabled,
    is_evidence_ledger_lifecycle_enabled,
    is_facts_replan_enabled,
    is_format_on_write_enabled,
    is_goal_nudge_enabled,
    is_grounded_answer_guard_enabled,
    is_key_aware_model_routes_enabled,
    is_message_cache_enabled,
    is_prompt_examples_enabled,
    is_prompt_redflags_enabled,
    is_prompt_search_rules_enabled,
    is_read_ledger_enabled,
    is_read_quality_enabled,
    is_research_fact_guidance_enabled,
    is_research_methodology_enabled,
    is_self_introspection_enabled,
    is_tool_synthesis_nudge_enabled,
    is_tool_usage_guidance_enabled,
    is_user_hooks_enabled,
)


# Single source of truth for the 13 inputs and their expected legacy semantics
# (strict-truthy opt-in: only ``1``/``true``/``on``/``yes`` after trim+lower
# resolve to ``True``).
_PARITY_CASES: tuple[tuple[str | None, bool], ...] = (
    # (env_value_or_None, expected_bool)
    (None, False),  # unset
    ("1", True),
    ("true", True),
    ("on", True),
    ("yes", True),
    ("TRUE", True),
    ("Yes", True),
    ("0", False),
    ("false", False),
    ("off", False),
    ("", False),
    ("garbage", False),
    ("  true  ", True),  # whitespace-trim + case-fold
)


# Reader fn + env-name pair for each migrated flag. Adding a row here in a
# future batch automatically extends the parametrized parity suite.
_MIGRATED_FLAGS = (
    # I-1 batch 1 (remaining strict default-OFF; the 6 guidance flags were
    # promoted to profile-aware default-ON `_pb` and now live in
    # _PROFILE_MIGRATED_FLAGS below, per the no-default-off policy):
    (is_key_aware_model_routes_enabled, MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED_ENV),
    (is_prompt_search_rules_enabled, MAGI_PROMPT_SEARCH_RULES_ENABLED_ENV),
    # I-1 batch 2 (7 strict default-OFF master-switch flags):
    (is_dashboard_pack_authoring_enabled, MAGI_DASHBOARD_PACK_AUTHORING_ENABLED_ENV),
    (is_grounded_answer_guard_enabled, MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV),
    (is_research_fact_guidance_enabled, MAGI_RESEARCH_FACT_GUIDANCE_ENABLED_ENV),
)


@pytest.mark.parametrize(
    ("reader", "env_name"),
    _MIGRATED_FLAGS,
    ids=[fn.__name__ for fn, _ in _MIGRATED_FLAGS],
)
@pytest.mark.parametrize(("raw", "expected"), _PARITY_CASES)
def test_flag_bool_parity_with_legacy_inline_body(
    reader, env_name: str, raw: str | None, expected: bool
) -> None:
    """Each migrated ``is_*_enabled`` matches the legacy ``_is_true(get(NAME))``.

    The new body delegates to ``flag_bool(NAME, env=source)`` — semantically
    identical because ``FlagSpec(..., kind="bool", default=False)`` plus the
    shared ``_truthy.is_true`` parser collapses to the same decision tree as
    the inline ``return _is_true(source.get(NAME))`` form for every input above.
    """

    env: dict[str, str] = {} if raw is None else {env_name: raw}
    assert reader(env) is expected


def test_default_env_resolves_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: passing ``env=None`` (≡ ``os.environ`` with the flag unset) is False.

    Each migrated flag is registered with a ``False`` default, so reading via
    ``env=None`` (the convenience form) on a hermetic env without the flag set
    must yield ``False``. Uses ``monkeypatch.delenv`` to scrub any operator env
    that may have leaked into the test process.
    """

    for reader, env_name in _MIGRATED_FLAGS:
        monkeypatch.delenv(env_name, raising=False)
        assert reader() is False, reader.__name__
        assert reader(None) is False, reader.__name__


def test_migrated_flags_are_registered_as_bool() -> None:
    """Each migrated env-name is registered in ``FLAGS`` with ``kind='bool'``.

    The migration relies on the registry contract: ``flag_bool`` raises a
    ``TypeError`` for non-``bool`` kinds and ``KeyError`` for unregistered
    names, so this test pins both the membership and the kind so a future
    rename can never silently downgrade a flag onto the profile-aware path.
    """

    from magi_agent.config.flags import FLAGS_BY_NAME

    for _, env_name in _MIGRATED_FLAGS:
        spec = FLAGS_BY_NAME[env_name]
        assert spec.kind == "bool", spec
        assert spec.default is False, spec


# ---------------------------------------------------------------------------
# I-1 batch 3 — profile-aware default-ON parity.
#
# Each function below had its inline body replaced from
#   ``return _runtime_feature_enabled(env, NAME)``
# to
#   ``return flag_profile_bool(NAME, env=env)``
# Both delegate to ``config._truthy.runtime_feature_enabled`` so the migration
# is structural, not semantic — but the parity table exercises the full
# explicit-truthy / explicit-falsey / profile-default matrix to verify the
# delegation chain has not silently introduced any divergence.
#
# Per MEMORY.md → feedback_flag_promotion_verification.md: a
# default-ON-under-profile flag's ON path must be exercised under realistic
# profile conditions BEFORE the flag flips. The full-profile branch below
# does exactly that for all 6 batch-3 flags.
# ---------------------------------------------------------------------------

# Same 13 inputs as the strict-OFF table above but with PER-PROFILE expected
# values. Resolution order under ``_runtime_feature_enabled``:
#   1. explicit truthy → True
#   2. explicit falsey (including empty string) → False
#   3. unset / unknown value → ``runtime_profile_default_enabled(env)``
#      (True under full profile, False under safe profile)
_PROFILE_PARITY_INPUTS: tuple[str | None, ...] = (
    None,           # unset → profile default
    "1",            # explicit truthy → True
    "true",
    "on",
    "yes",
    "TRUE",
    "Yes",
    "0",            # explicit falsey → False
    "false",
    "off",
    "",             # empty string is in FALSE_VALUES → False
    "garbage",      # unknown → profile default
    "  true  ",     # whitespace+case-fold → True
)

# Profile contexts. ``None`` ≡ MAGI_RUNTIME_PROFILE unset (full profile).
# ``safe`` / ``eval`` are safe profiles per config._truthy.SAFE_RUNTIME_PROFILES.
# ``dogfood`` / ``lab`` are unknown (not in the safe set) so they resolve as
# full profile = default ON — explicitly covered to honour the memory feedback
# about flag-promotion verification under realistic operator profiles.
_PROFILE_CASES: tuple[tuple[str | None, bool], ...] = (
    (None, True),         # full profile (unset)
    ("safe", False),      # safe profile
    ("eval", False),      # eval profile
    ("dogfood", True),    # unknown profile → falls back to full (default ON)
    ("lab", True),        # unknown profile → falls back to full (default ON)
)


_PROFILE_MIGRATED_FLAGS = (
    (is_facts_replan_enabled, MAGI_FACTS_REPLAN_ENABLED_ENV),
    (is_goal_nudge_enabled, MAGI_GOAL_NUDGE_ENABLED_ENV),
    (is_tool_synthesis_nudge_enabled, MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED_ENV),
    (is_user_hooks_enabled, MAGI_USER_HOOKS_ENABLED_ENV),
    # Promoted _b -> _pb under the no-default-off policy (default-ON in the full
    # profile; OFF only under safe/eval or an explicit "0").
    (is_automation_methodology_enabled, MAGI_AUTOMATION_METHODOLOGY_ENABLED_ENV),
    (is_coding_context_enabled, MAGI_CODING_CONTEXT_ENABLED_ENV),
    (is_prompt_examples_enabled, MAGI_PROMPT_EXAMPLES_ENABLED_ENV),
    (is_prompt_redflags_enabled, MAGI_PROMPT_REDFLAGS_ENABLED_ENV),
    (is_research_methodology_enabled, MAGI_RESEARCH_METHODOLOGY_ENABLED_ENV),
    (is_tool_usage_guidance_enabled, MAGI_TOOL_USAGE_GUIDANCE_ENABLED_ENV),
    (is_read_ledger_enabled, MAGI_READ_LEDGER_ENABLED_ENV),
    (is_self_introspection_enabled, MAGI_SELF_INTROSPECTION_ENABLED_ENV),
    (is_evidence_ledger_lifecycle_enabled, MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED_ENV),
    (is_format_on_write_enabled, MAGI_EDIT_FORMAT_ON_WRITE_ENABLED_ENV),
    (is_read_quality_enabled, MAGI_READ_QUALITY_ENABLED_ENV),
    (is_message_cache_enabled, MAGI_MESSAGE_CACHE_ENABLED_ENV),
)


def _expected_profile_bool(raw: str | None, profile_default: bool) -> bool:
    """Mirror of ``config._truthy.runtime_feature_enabled`` (legacy inline body).

    Implementing this here pins the expected behaviour at the parity-test
    layer so the parity assertions cannot tautologically pass even if both
    sides reach into the same helper. If the helper ever diverges from this
    spec the parity table fires.
    """

    if raw is None:
        return profile_default
    normalized = raw.strip().lower()
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return profile_default


@pytest.mark.parametrize(
    ("reader", "env_name"),
    _PROFILE_MIGRATED_FLAGS,
    ids=[fn.__name__ for fn, _ in _PROFILE_MIGRATED_FLAGS],
)
@pytest.mark.parametrize("raw", _PROFILE_PARITY_INPUTS)
@pytest.mark.parametrize(("profile", "profile_default"), _PROFILE_CASES)
def test_flag_profile_bool_parity_with_legacy_inline_body(
    monkeypatch: pytest.MonkeyPatch,
    reader,
    env_name: str,
    raw: str | None,
    profile: str | None,
    profile_default: bool,
) -> None:
    """Each migrated profile-aware ``is_*_enabled`` matches the legacy inline.

    For every combination of (13 inputs) × (5 profile contexts) the new body
    ``flag_profile_bool(NAME, env=source)`` MUST return the same boolean as
    the legacy ``_runtime_feature_enabled(source, NAME)`` form. Implements
    the legacy semantics independently in :func:`_expected_profile_bool` so
    the assertion is grounded in the spec, not the implementation under test.

    Six flags × 13 inputs × 5 profiles = 390 parametrized cases.
    """

    # Scrub the live process env so it cannot leak the flag/profile.
    monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)

    env: dict[str, str] = {}
    if profile is not None:
        env["MAGI_RUNTIME_PROFILE"] = profile
    if raw is not None:
        env[env_name] = raw

    assert reader(env) is _expected_profile_bool(raw, profile_default)


@pytest.mark.parametrize(
    ("reader", "env_name"),
    _PROFILE_MIGRATED_FLAGS,
    ids=[fn.__name__ for fn, _ in _PROFILE_MIGRATED_FLAGS],
)
@pytest.mark.parametrize("profile", ["lab", "dogfood"])
@pytest.mark.parametrize("falsey", ["0", "false", "off", "no", ""])
def test_explicit_false_overrides_profile_default(
    reader, env_name: str, profile: str, falsey: str
) -> None:
    """Explicit falsey value beats the profile-default ON for unknown profiles.

    ``lab`` and ``dogfood`` are not in the SAFE_RUNTIME_PROFILES set so they
    resolve as full profile (default ON) — the explicit-false override MUST
    still win. Twelve flags × 2 profiles × 5 falsey values = 60 cases,
    covering the most operator-visible override path: "I'm running under
    --profile lab but I want this one specific gate off".
    """

    env = {"MAGI_RUNTIME_PROFILE": profile, env_name: falsey}
    assert reader(env) is False


@pytest.mark.parametrize(
    ("reader", "env_name"),
    _PROFILE_MIGRATED_FLAGS,
    ids=[fn.__name__ for fn, _ in _PROFILE_MIGRATED_FLAGS],
)
@pytest.mark.parametrize("profile", ["safe", "eval"])
@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on"])
def test_explicit_true_overrides_safe_profile(
    reader, env_name: str, profile: str, truthy: str
) -> None:
    """Explicit truthy value beats the safe-profile default OFF.

    Mirror of the override above for the opposite direction: an operator
    running ``MAGI_RUNTIME_PROFILE=safe`` who wants to opt one specific gate
    back on. Six flags × 2 safe profiles × 4 truthy values = 48 cases.
    """

    env = {"MAGI_RUNTIME_PROFILE": profile, env_name: truthy}
    assert reader(env) is True


def test_profile_migrated_flags_are_registered_as_profile_bool() -> None:
    """Each profile-migrated env-name is registered with ``kind='profile_bool'``.

    Mirrors :func:`test_migrated_flags_are_registered_as_bool` for the
    profile-aware family. ``flag_profile_bool`` raises ``TypeError`` for any
    non-``profile_bool`` kind so this pins both membership and kind to keep a
    future rename from silently routing a profile gate onto the strict
    ``flag_bool`` path (which would mute the profile-default-ON behaviour).
    """

    from magi_agent.config.flags import FLAGS_BY_NAME

    for _, env_name in _PROFILE_MIGRATED_FLAGS:
        spec = FLAGS_BY_NAME[env_name]
        assert spec.kind == "profile_bool", spec
        assert spec.default is None, spec


# ---------------------------------------------------------------------------
# I-1 batch 4 — tri-state ``MAGI_DOCUMENT_AUTHORING_COVERAGE`` parity.
#
# Batch 4 promotes the historically ``kind="bool"`` ``MAGI_DOCUMENT_AUTHORING_COVERAGE``
# flag to ``kind="str"`` with default ``"off"`` and routes the raw read in
# :func:`magi_agent.config.env.resolve_document_authoring_coverage_mode`
# through :func:`magi_agent.config.flags.flag_str`. The 3-mode parsing
# (off/advisory/block, with legacy-truthy ``1``/``true``/``yes``/``on`` →
# ``block`` for back-compat, anything else → ``off``) stays in the resolver
# layer; the registry only owns the env-name and the inert default. The table
# below pins every documented outcome so any divergence in the typed-reader
# path is loud — including the explicit ``advisory`` and ``block`` modes plus
# the "unknown typo falls safe to ``off``" rule that protects operators from
# accidentally hard-blocking via a misspelled value.
# ---------------------------------------------------------------------------

_DOCUMENT_AUTHORING_COVERAGE_CASES: tuple[tuple[str | None, str], ...] = (
    # (env_value_or_None, expected_mode)
    (None, "off"),            # unset → registry default
    ("", "off"),              # empty string → off (resolver strips)
    ("off", "off"),
    ("OFF", "off"),            # case-insensitive
    ("  off  ", "off"),        # whitespace-insensitive
    ("advisory", "advisory"),
    ("ADVISORY", "advisory"),
    (" Advisory ", "advisory"),
    ("block", "block"),
    ("BLOCK", "block"),
    (" Block ", "block"),
    # Legacy truthy values → block (back-compat with the historical bool flag).
    ("1", "block"),
    ("true", "block"),
    ("yes", "block"),
    ("on", "block"),
    ("TRUE", "block"),
    # Legacy falsey values → off.
    ("0", "off"),
    ("false", "off"),
    ("no", "off"),
    # Unknown values fail safe to off (never silently hard-block on a typo).
    ("bogus", "off"),
    ("blockk", "off"),
    ("advisroy", "off"),
)


@pytest.mark.parametrize(("raw", "expected"), _DOCUMENT_AUTHORING_COVERAGE_CASES)
def test_document_authoring_coverage_tri_state_parity(
    raw: str | None, expected: str
) -> None:
    """Every documented mode resolves through the new ``flag_str`` path.

    Pins the resolver contract end-to-end: registry default ``"off"`` plus the
    3-mode parsing (off/advisory/block, legacy-truthy → block, unknown → off).
    Any future migration that misreads the registry (e.g. uses ``flag_bool``
    by mistake) would collapse advisory/block onto False and fire this table.
    """
    from magi_agent.config.env import resolve_document_authoring_coverage_mode

    env: dict[str, str] = (
        {} if raw is None else {"MAGI_DOCUMENT_AUTHORING_COVERAGE": raw}
    )
    assert resolve_document_authoring_coverage_mode(env) == expected


def test_document_authoring_coverage_is_enabled_wrapper_parity() -> None:
    """The bool wrapper matches ``mode != "off"`` over the full mode space.

    ``is_document_authoring_coverage_enabled`` is the bool surface above the
    tri-state resolver. Pinning it against the same table guarantees the
    advisory and block modes both report ``True`` (enabled) and every other
    outcome reports ``False`` (off / unknown).
    """
    from magi_agent.config.env import (
        is_document_authoring_coverage_enabled,
        resolve_document_authoring_coverage_mode,
    )

    for raw, expected_mode in _DOCUMENT_AUTHORING_COVERAGE_CASES:
        env: dict[str, str] = (
            {} if raw is None else {"MAGI_DOCUMENT_AUTHORING_COVERAGE": raw}
        )
        assert resolve_document_authoring_coverage_mode(env) == expected_mode
        assert is_document_authoring_coverage_enabled(env) is (expected_mode != "off")


def test_document_authoring_coverage_is_registered_as_str() -> None:
    """The flag is registered with ``kind="str"`` and default ``"off"``.

    Pins the I-1 batch 4 change: the registration was promoted from
    ``kind="bool"`` / ``default=False`` to ``kind="str"`` / ``default="off"``
    so the typed reader returns the raw mode string and the resolver maps it
    to the 3-mode space. A future rename that silently downgrades the kind to
    ``bool`` would collapse advisory/block onto a strict-truthy True and fire
    here.
    """
    from magi_agent.config.flags import FLAGS_BY_NAME

    spec = FLAGS_BY_NAME["MAGI_DOCUMENT_AUTHORING_COVERAGE"]
    assert spec.kind == "str"
    assert spec.default == "off"
    assert spec.scope == "public"


def test_profile_default_env_resolves_to_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: ``env=None`` (≡ live process env unset) reads ON under full profile.

    Honours the flag-promotion-verification memory feedback: every
    profile-aware default-ON gate must demonstrably resolve ON under the
    realistic operator profile (unset MAGI_RUNTIME_PROFILE) when called via
    the ``env=None`` convenience form.
    """

    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    # Two of the six historically take a required ``env`` argument
    # (``is_read_ledger_enabled`` and ``is_format_on_write_enabled``) — the
    # batch intentionally preserves the public signature, so call them with
    # an explicit empty mapping. The other four accept ``env=None`` and read
    # the live ``os.environ`` (which the monkeypatch has scrubbed of both the
    # flag and ``MAGI_RUNTIME_PROFILE``).
    _REQUIRED_ENV_READERS = {
        MAGI_READ_LEDGER_ENABLED_ENV,
        MAGI_EDIT_FORMAT_ON_WRITE_ENABLED_ENV,
    }
    for reader, env_name in _PROFILE_MIGRATED_FLAGS:
        monkeypatch.delenv(env_name, raising=False)
        if env_name in _REQUIRED_ENV_READERS:
            assert reader({}) is True, reader.__name__
        else:
            assert reader() is True, reader.__name__
            assert reader(None) is True, reader.__name__
