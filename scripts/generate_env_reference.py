#!/usr/bin/env python3
"""Generate the flag inventory section of ``docs/env-reference.md``.

Single source of truth: ``magi_agent/config/flags.py`` (the PR2 ``FLAGS``
registry). This script extracts every ``scope == "public"`` flag (name / kind /
default / summary) and renders it between sentinel markers in
``docs/env-reference.md``, leaving the hand-written preamble (provider keys,
egress proxy, etc.) untouched.

Motivation (15-flag-governance.md §E4): the reference was hand-maintained and
drifted — only ~9.7% of flags were documented and the memory master switch
``MAGI_MEMORY_ENABLED`` was missing entirely. Autogeneration keeps the public
operator reference in lockstep with the registry: a new public flag is one line
in ``FLAGS`` and a regenerate.

Usage (from ``magi-agent/``)::

    python3 scripts/generate_env_reference.py          # rewrite the doc in place
    python3 scripts/generate_env_reference.py --check  # exit 1 if the doc would change

Mirrors the "script + idempotent rewrite" pattern of
``scripts/generate_module_map.py``. The committed-doc drift CI gate is a separate
follow-up (15-PR4) and is intentionally NOT wired here.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a standalone script (scripts/ is not on sys.path).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from magi_agent.config.flags import FLAGS, FlagSpec  # noqa: E402

BEGIN_MARKER = "<!-- BEGIN GENERATED FLAGS (scripts/generate_env_reference.py) -->"
END_MARKER = "<!-- END GENERATED FLAGS -->"

ENV_REFERENCE_PATH = _ROOT / "docs" / "env-reference.md"


def _default_text(spec: FlagSpec) -> str:
    """Human-readable default description for one flag.

    ``profile_bool`` flags carry no flat default (their default is a function of
    ``MAGI_RUNTIME_PROFILE``); they are described as profile-aware default-ON
    rather than being flattened to a misleading ``true``/``false``.
    """
    if spec.kind == "profile_bool":
        return "default-ON (full runtime profile; OFF under safe/eval)"
    if spec.kind == "bool":
        return "default on" if bool(spec.default) else "default off"
    if spec.kind in ("str", "int"):
        if spec.default in (None, ""):
            return "no default"
        return f"default `{spec.default}`"
    return "default off"


def _render_flag_line(spec: FlagSpec) -> str:
    """Render one flag as a Markdown bullet."""
    return f"- `{spec.name}` ({_default_text(spec)}) — {spec.summary}"


def render_flags_section(flags: tuple[FlagSpec, ...]) -> str:
    """Render the public-flag inventory as a Markdown body (no markers).

    Only ``scope == "public"`` flags appear — ``hosted``/``internal``/``dev``
    flags are deliberately excluded from this operator-facing public reference.
    Flags are sorted by name for stable, idempotent output.
    """
    public = sorted(
        (spec for spec in flags if spec.scope == "public"),
        key=lambda s: s.name,
    )
    lines: list[str] = [
        "## Feature flags (auto-generated)",
        "",
        (
            "Generated from the `FLAGS` registry in `magi_agent/config/flags.py` "
            "by `scripts/generate_env_reference.py`. Do not edit this section by "
            "hand; register the flag in the registry and regenerate."
        ),
        "",
    ]
    lines.extend(_render_flag_line(spec) for spec in public)
    lines.append("")
    return "\n".join(lines)


def apply_to_document(document: str, body: str) -> str:
    """Replace the content between the markers in ``document`` with ``body``.

    The markers themselves are preserved (so the rewrite is idempotent), as is
    everything outside them. Raises ``ValueError`` if either marker is absent.
    """
    begin = document.find(BEGIN_MARKER)
    end = document.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise ValueError(
            f"env-reference document missing markers {BEGIN_MARKER!r} / {END_MARKER!r}"
        )
    before = document[: begin + len(BEGIN_MARKER)]
    after = document[end:]
    return f"{before}\n{body.rstrip()}\n\n{after}"


def build_document(document: str, flags: tuple[FlagSpec, ...]) -> str:
    """Return ``document`` with its generated section rebuilt from ``flags``."""
    return apply_to_document(document, render_flags_section(flags))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero (1) if the doc is out of sync instead of rewriting it",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=ENV_REFERENCE_PATH,
        help="path to env-reference.md (default: docs/env-reference.md)",
    )
    args = parser.parse_args(argv)

    path: Path = args.path
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        return 2

    try:
        regenerated = build_document(current, FLAGS)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if regenerated == current:
        print(f"{path} is up to date.", file=sys.stderr)
        return 0

    if args.check:
        print(
            f"{path} is out of sync with the flag registry; "
            "run `python scripts/generate_env_reference.py`.",
            file=sys.stderr,
        )
        return 1

    path.write_text(regenerated, encoding="utf-8")
    print(f"Wrote {path}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
