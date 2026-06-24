"""J-2 step 1 — ``build_headless_runtime`` / ``build_tui_app`` ``model``
parameter docstrings remain honest about the plumbing.

REVIEW-A ``review/cli-tui.md`` M9 (J-2) flagged that
``cli/wiring.py``'s ``build_headless_runtime`` docstring used to claim
the ``model`` parameter was "accepted but not yet forwarded (no Stream
F model plumbing yet)" — when in fact the value reached
``resolve_provider_config(model_override=model)`` through
``_build_default_runner`` and ``_build_runner_policy_assembly``. The
lie was already corrected; this module is the honesty ratchet so a
future docstring change cannot re-introduce it.

Locked invariants:

1. ``build_headless_runtime.__doc__`` does not carry the stale
   ``"not yet forwarded"`` / ``"future model-selection wiring"`` /
   ``"no Stream F model plumbing"`` claims about the ``model``
   parameter.
2. The docstring affirmatively names the plumbing — at minimum, the
   ``model`` parameter must be described as "forwarded" through the
   provider-resolution call chain.
3. ``build_tui_app`` carries the same honesty (its ``model`` parameter
   shares the headless plumbing per its docstring).
"""

from __future__ import annotations

import re

from magi_agent.cli.wiring import build_headless_runtime, build_tui_app


_STALE_CLAIMS: tuple[str, ...] = (
    "not yet forwarded",
    "future model-selection wiring",
    "no Stream F model plumbing",
)


def _docstring_for(fn: object) -> str:
    doc = getattr(fn, "__doc__", None)
    assert isinstance(doc, str) and doc, (
        f"{fn!r} must have a non-empty docstring describing its parameters"
    )
    return doc


def test_headless_runtime_docstring_no_stale_model_claims() -> None:
    doc = _docstring_for(build_headless_runtime)
    offenders = [claim for claim in _STALE_CLAIMS if claim in doc]
    assert offenders == [], (
        "build_headless_runtime docstring re-introduced a stale "
        f"``model`` claim ({offenders}). The model is forwarded into "
        "``resolve_provider_config(model_override=...)`` through the "
        "default runner build path — the docstring must say so."
    )


def test_headless_runtime_docstring_describes_model_forwarding() -> None:
    doc = _docstring_for(build_headless_runtime)
    # Find the ``model:`` parameter block (numpydoc-style) and confirm
    # its body affirms the forwarding.
    m = re.search(
        r"^\s*model:\s*\n(?P<body>(?:\s+[^\n]+\n)+)", doc, re.MULTILINE
    )
    assert m is not None, (
        "build_headless_runtime docstring is missing a ``model:`` "
        "parameter block under its Parameters section"
    )
    body = m.group("body").lower()
    assert "forwarded" in body or "passed" in body or "reaches" in body, (
        "build_headless_runtime's ``model:`` parameter block must "
        "describe forward-plumbing into the provider config; got: "
        f"{body!r}"
    )


def test_tui_app_docstring_no_stale_model_claims() -> None:
    doc = _docstring_for(build_tui_app)
    offenders = [claim for claim in _STALE_CLAIMS if claim in doc]
    assert offenders == [], (
        "build_tui_app docstring re-introduced a stale ``model`` claim "
        f"({offenders}). The model is forwarded through the same "
        "plumbing as ``build_headless_runtime`` — the docstring must "
        "say so."
    )
