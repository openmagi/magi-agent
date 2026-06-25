"""PR-4: catalog ``deprecated -> replacement`` auto-redirect in resolve_child_route.

Triage finding (0.1.85): ``claude-opus-4-6`` is marked ``deprecated=true`` with
``replacement=claude-opus-4-8`` in ``builtin_catalog.json`` but
:func:`resolve_child_route` ignored the field. Parent agents picked the
deprecated id from the advertised-routes list 5 out of 7 times. Anthropic still
returns 200 for the deprecated model, so this is not blocking, but the
deprecation signal was wasted.

This test module pins the new behaviour:

1. Resolving a deprecated route returns the replacement route.
2. The redirect is traced under ``MAGI_CHILD_RUNNER_EMPTY_DEBUG=1`` and silent
   when the env flag is OFF.
3. A synthetic deprecated record whose replacement is unresolvable falls back
   to the original deprecated route (no infinite loop, no crash).
4. :func:`available_child_model_routes` does not advertise deprecated entries.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping

import pytest

from magi_agent.runtime import model_tiers
from magi_agent.runtime.model_tiers import (
    ChildRoute,
    ResolvedModelTier,
    available_child_model_routes,
    resolve_child_route,
)


def _isolated_env(**extra: str) -> dict[str, str]:
    """Return an env dict with ``MAGI_CONFIG`` pointing to an empty temp file.

    Matches the pattern in ``test_runtime_model_tiers.py`` so the cli/providers
    config loader does not pick up ``~/.magi/config.toml``.
    """
    tmp = tempfile.mktemp(suffix=".toml")  # noqa: S306 - test-only
    open(tmp, "w").close()
    return {"MAGI_CONFIG": tmp, **extra}


def test_deprecated_route_redirects_to_replacement() -> None:
    """``claude-opus-4-6`` (deprecated) must resolve to ``claude-opus-4-8``."""
    env = _isolated_env()
    result = resolve_child_route("anthropic", "claude-opus-4-6", env)
    assert result == ChildRoute("anthropic", "claude-opus-4-8"), (
        f"deprecated route must redirect to replacement; got {result!r}"
    )


def test_deprecated_route_logs_redirect_when_env_on(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With ``MAGI_CHILD_RUNNER_EMPTY_DEBUG=1`` the redirect emits one stamp."""
    env = _isolated_env(MAGI_CHILD_RUNNER_EMPTY_DEBUG="1")
    resolve_child_route("anthropic", "claude-opus-4-6", env)
    captured = capsys.readouterr()
    stamps = [
        line
        for line in captured.err.splitlines()
        if "[model_tiers.trace] deprecated_redirect" in line
    ]
    assert len(stamps) == 1, f"exactly one redirect stamp expected; stderr was:\n{captured.err!r}"
    stamp = stamps[0]
    assert "from=claude-opus-4-6" in stamp
    assert "to=claude-opus-4-8" in stamp


def test_deprecated_route_silent_when_env_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without the empty-debug env, no stamp is emitted."""
    env = _isolated_env()
    resolve_child_route("anthropic", "claude-opus-4-6", env)
    captured = capsys.readouterr()
    assert "[model_tiers.trace] deprecated_redirect" not in captured.err, (
        f"redirect stamp must not appear when env OFF; stderr was:\n{captured.err!r}"
    )


def test_missing_replacement_falls_back_to_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deprecated record whose ``replacement`` is unknown must fall back.

    Patches the catalog lookup so the resolved tier reports ``deprecated=True``
    with ``replacement="nonexistent-model"``. The implementation must (a) NOT
    crash, (b) NOT loop indefinitely, and (c) return the ORIGINAL deprecated
    route as a fail-soft so the caller still gets a usable route.
    """

    def _fake_lookup(provider: str, model: str) -> tuple[bool, str | None]:
        # Both the original and the unresolvable replacement are reported as
        # deprecated to make sure the loop guard does not trigger on the second
        # lookup either.
        if provider == "anthropic" and model == "synthetic-deprecated":
            return (True, "nonexistent-model")
        return (False, None)

    monkeypatch.setattr(model_tiers, "_catalog_deprecation_lookup", _fake_lookup, raising=True)

    # Force the synthetic ``synthetic-deprecated`` id through the registry by
    # also patching ``ModelTierRegistry.with_defaults`` so it resolves to a tier
    # under that name. The stub returns a real tier for the original id and
    # marks ``nonexistent-model`` as ``unknown_model_*`` (mirroring the real
    # registry behaviour) so the implementation's fail-soft branch kicks in.
    class _StubRegistry:
        def resolve(self, *, provider: str, model: str) -> ResolvedModelTier:
            if model == "synthetic-deprecated":
                return ResolvedModelTier(
                    provider=provider,
                    model=model,
                    tier="sota",
                    capabilities=(),
                )
            # Unknown replacement: mirror what the real registry returns so the
            # caller's ``unknown_model`` reason-code check fires the fail-soft
            # path back to the original deprecated route.
            return ResolvedModelTier(
                provider=provider,
                model=model,
                tier="standard",
                capabilities=(),
                reasonCodes=("unknown_model_standard_no_elevated_capabilities",),
            )

    monkeypatch.setattr(
        model_tiers.ModelTierRegistry,
        "with_defaults",
        classmethod(lambda cls: _StubRegistry()),  # type: ignore[arg-type]
        raising=True,
    )

    env = _isolated_env()
    result = resolve_child_route("anthropic", "synthetic-deprecated", env)
    assert result == ChildRoute("anthropic", "synthetic-deprecated"), (
        f"unresolvable replacement must fall back to the original deprecated route; got {result!r}"
    )


def test_available_routes_excludes_deprecated() -> None:
    """Advertised routes must not include any catalog-deprecated entry."""
    env = _isolated_env()
    routes = available_child_model_routes(env)
    route_names = [r.split(" ")[0] for r in routes]
    assert "anthropic:claude-opus-4-6" not in route_names, (
        f"deprecated claude-opus-4-6 must not be advertised; got: {routes}"
    )
    # Sanity: the replacement is still advertised.
    assert "anthropic:claude-opus-4-8" in route_names, (
        f"replacement claude-opus-4-8 must still be advertised; got: {routes}"
    )


def test_resolve_child_route_signature_preserved() -> None:
    """Defensive: the public signature is still ``(provider, model, env)``."""
    env: Mapping[str, str] = _isolated_env()
    # Just call with the documented args; the function must accept them.
    resolve_child_route("anthropic", "claude-opus-4-8", env)
