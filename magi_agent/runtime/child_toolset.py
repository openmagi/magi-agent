"""Child-runner toolset profile resolution (PR1, doc 07).

A SMALL, import-clean module that maps the ``MAGI_CHILD_RUNNER_TOOLSET`` env
gate to a profile literal and the corresponding read-only tool allowlist.

Why a second gate (in addition to ``MAGI_CHILD_RUNNER_LIVE_ENABLED``)?
----------------------------------------------------------------------
``MAGI_CHILD_RUNNER_LIVE_ENABLED`` decides whether a *live* child runs at all.
Even when live is on, the historical default is a TEXT-ONLY child with an empty
toolset (``tools=[]``). Forwarding a real toolset is a SEPARATE opt-in so the
existing text-only behaviour is preserved byte-for-byte unless an operator
explicitly opts into ``readonly`` (or, post-permissions-unification, ``full``).
This mirrors the project's twin-gate safety philosophy (e.g. the local-search
prefer gate) — capability and activation are decoupled.

Profiles
--------
* ``none``     — empty toolset (default; text-only child, byte-identical to v1).
* ``readonly`` (non-mutating source-inspection tools FileRead/Glob/Grep/
                 GitDiff, plus pure side-effect-free helpers like Calculation,
                 a deterministic AST expression evaluator). Safe to enable
                 without the child-sandbox/permissions decision (doc 09)
                 because nothing in the allowlist mutates the workspace or
                 makes a network call. ``Calculation`` was added (PR-N) after
                 Kevin's 0.1.91 SOTA-spawn debug showed 6/9 children
                 (opus/haiku/gemini-flash variants, some gpt-5.5) crashing
                 with ``Tool 'Calculation' not found`` on simple arithmetic.
* ``full``     — the whole CLI core toolset (Write/Edit/Bash/...). GATED behind
                 the child sandbox + permission-unification follow-up (doc 09);
                 this module only RESOLVES the literal, it does not authorise it.

Anything unrecognised (typo, ``1``, ``true``, ``all``, ...) degrades to the
safe ``none`` profile — fail-closed, never escalate by accident.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from magi_agent.tools.local_readonly import LOCAL_READONLY_TOOL_NAMES

#: Env gate name (the SECOND, toolset-specific gate; see module docstring).
CHILD_TOOLSET_ENV = "MAGI_CHILD_RUNNER_TOOLSET"

ChildToolsetProfile = Literal["none", "readonly", "full"]

#: The recognised profile literals. The default (and fail-closed) value is the
#: FIRST entry (``none``).
_KNOWN_PROFILES: tuple[ChildToolsetProfile, ...] = ("none", "readonly", "full")

#: Pure, side-effect-free tools that are SAFE to expose to a readonly child
#: even though they are NOT source-inspection tools. Each entry MUST be
#: deterministic, never touch the filesystem, never make a network call, and
#: never spawn a subprocess. Adding a tool here is a security decision; the
#: bar is the same as for ``LOCAL_READONLY_TOOL_NAMES`` (zero side effects).
#:
#: * ``Calculation``: gate1a's AST-based arithmetic evaluator
#:   (:func:`magi_agent.gates.gate1a_readonly_tools._evaluate_expression`).
#:   Only ``ast.Constant``/``ast.UnaryOp``/``ast.BinOp`` nodes are honoured;
#:   any other AST node raises ``ValueError``. No fs/net/subprocess surface.
_PURE_NON_INSPECTION_TOOL_NAMES: tuple[str, ...] = ("Calculation",)

#: Tools the readonly child profile forwards to the model. Built as the
#: source-inspection set (:data:`LOCAL_READONLY_TOOL_NAMES`, the single
#: canonical definition for ``SourceInspection`` projection) UNIONED with
#: :data:`_PURE_NON_INSPECTION_TOOL_NAMES`. Keeping the two sources separate
#: preserves the SourceInspection contract (``LOCAL_READONLY_TOOL_NAMES`` is
#: the authoritative source-projection set; ``Calculation`` does NOT project
#: a source) while letting the child profile expose more pure helpers.
READONLY_TOOL_NAMES: tuple[str, ...] = (
    tuple(LOCAL_READONLY_TOOL_NAMES) + _PURE_NON_INSPECTION_TOOL_NAMES
)


def resolve_child_toolset_profile(
    env: Mapping[str, str] | None = None,
) -> ChildToolsetProfile:
    """Resolve the child toolset profile from ``MAGI_CHILD_RUNNER_TOOLSET``.

    Evaluated at call time (not import time) so callers/tests can patch the env
    without a module reload. The value is stripped and lower-cased; only the
    exact literals ``none``/``readonly``/``full`` are recognised. Any other
    value (unset, empty, typo, ``1``, ``true``, ...) degrades to ``none``.

    :param env: Optional explicit env mapping; defaults to ``os.environ``.
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    raw = str(source.get(CHILD_TOOLSET_ENV, "")).strip().lower()
    if raw in _KNOWN_PROFILES:
        return raw  # type: ignore[return-value]
    return "none"


def toolset_allowlist(profile: ChildToolsetProfile) -> tuple[str, ...] | None:
    """Map a profile to a tool-name allowlist used to FILTER the core toolset.

    * ``none``     → ``()`` (empty allowlist → no tools are forwarded).
    * ``readonly`` → :data:`READONLY_TOOL_NAMES` (filter to inspection tools).
    * ``full``     → ``None`` sentinel meaning "no name filter" (forward the
                     whole core toolset). Authorisation of ``full`` is the
                     caller's responsibility (doc 09 permissions).

    Any unrecognised profile is treated as ``none`` (fail-closed).
    """
    if profile == "readonly":
        return READONLY_TOOL_NAMES
    if profile == "full":
        return None
    return ()


__all__ = [
    "CHILD_TOOLSET_ENV",
    "READONLY_TOOL_NAMES",
    "ChildToolsetProfile",
    "resolve_child_toolset_profile",
    "toolset_allowlist",
]
