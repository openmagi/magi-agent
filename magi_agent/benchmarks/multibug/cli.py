"""Default-OFF gate + entrypoint for the multi-problem discovery harness.

Mirrors the discovery package's env-flag gate (``magi_agent/discovery/gate.py``):
the harness is opt-in behind ``MAGI_MULTIBUG_HARNESS_ENABLED``.
``run_eval`` calls :func:`ensure_enabled` before doing any work.

App wiring note
---------------
The repo's Typer app (``magi_agent/cli/app.py``) does NOT register any benchmark
subcommand to mirror — GAIA is invoked as a library / standalone runner, not via
``magi`` subcommands. Per the PR brief, registering a new subcommand was only
permitted by mirroring an existing one exactly; since none exists, this
self-contained module with a :func:`main` is the documented fallback.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence

from magi_agent.benchmarks.multibug.dataset import load_instances
from magi_agent.benchmarks.multibug.run import run_benchmark
from magi_agent.discovery.grounding import GroundingMode

#: Environment flag controlling the multi-problem harness.
MULTIBUG_HARNESS_ENABLED_ENV: str = "MAGI_MULTIBUG_HARNESS_ENABLED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class GateDisabledError(RuntimeError):
    """Raised when the multi-problem harness is invoked while the gate is OFF."""


def is_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return ``True`` when ``MAGI_MULTIBUG_HARNESS_ENABLED`` is truthy."""
    resolved = os.environ if env is None else env
    return (
        resolved.get(MULTIBUG_HARNESS_ENABLED_ENV, "0").strip().lower()
        in _TRUE_VALUES
    )


def ensure_enabled(env: Mapping[str, str] | None = None) -> None:
    """Raise :class:`GateDisabledError` unless the harness gate is enabled."""
    if not is_enabled(env):
        raise GateDisabledError(
            f"multi-problem discovery harness is disabled; set "
            f"{MULTIBUG_HARNESS_ENABLED_ENV}=1 to enable it (default-OFF)."
        )


def run_eval(
    instances_path: str,
    *,
    output_dir: str,
    mode: str = "tide",
    grounding: GroundingMode = "audit",
    model: str = "claude-opus-4-7",
    env: Mapping[str, str] | None = None,
) -> dict:
    """Gate, load instances from ``instances_path``, and run the benchmark."""
    ensure_enabled(env)
    instances = load_instances(instances_path)
    return run_benchmark(
        instances,
        output_dir=output_dir,
        mode=mode,
        grounding=grounding,
        model=model,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone CLI entrypoint (``python -m magi_agent.benchmarks.multibug.cli``)."""
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="magi-multibug",
        description="Multi-problem discovery benchmark harness (default-OFF).",
    )
    parser.add_argument("instances", help="path to a JSON/JSONL instances file")
    parser.add_argument(
        "--output-dir", required=True, help="directory for results + manifest"
    )
    parser.add_argument(
        "--mode",
        default="tide",
        choices=("tide", "single_agent", "multi_agent"),
    )
    parser.add_argument(
        "--grounding", default="audit", choices=("audit", "strict")
    )
    parser.add_argument("--model", default="claude-opus-4-7")
    args = parser.parse_args(argv)

    try:
        result = run_eval(
            args.instances,
            output_dir=args.output_dir,
            mode=args.mode,
            grounding=args.grounding,
            model=args.model,
        )
    except GateDisabledError as exc:
        parser.exit(1, f"{exc}\n")

    print(json.dumps(result, indent=2))
    return 0


__all__ = [
    "GateDisabledError",
    "MULTIBUG_HARNESS_ENABLED_ENV",
    "ensure_enabled",
    "is_enabled",
    "main",
    "run_eval",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
