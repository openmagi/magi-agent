# magi_agent/benchmarks/taubench/cli.py
"""τ-bench CLI entry-point: gate + provider binding + live run_eval.

The gate (ensure_enabled) and GateDisabledError are pure and unit-tested.
run_eval is the live wiring path: it imports tau_bench and litellm lazily
inside the function body and raises a clean ImportError message when they are
missing, mirroring the legalbench guard pattern.
"""
from __future__ import annotations

import contextlib
import json
import os

_GATE_ENV = "MAGI_TAUBENCH_ENABLED"


@contextlib.contextmanager
def _apply_flags(config):  # type: ignore[no-untyped-def]
    """Set control-plane env flags for *config* and restore them afterward.

    This prevents flag leakage across run_eval calls when multiple configs are
    evaluated in the same process (e.g. full sweep followed by vanilla sweep).
    """
    from magi_agent.benchmarks.taubench.config import flags_for  # noqa: PLC0415
    flags = flags_for(config)
    saved = {k: os.environ.get(k) for k in flags}
    os.environ.update(flags)
    try:
        yield
    finally:
        for k, prev in saved.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


class GateDisabledError(RuntimeError):
    pass


def ensure_enabled() -> None:
    """Raise GateDisabledError unless MAGI_TAUBENCH_ENABLED=1 is set.

    This is the default-OFF gate for the τ-bench harness. All entry points that
    run the harness against real data must call this before doing any work.
    """
    if os.environ.get(_GATE_ENV) != "1":
        raise GateDisabledError(
            f"τ-bench harness is gated off. Set {_GATE_ENV}=1 to run."
        )


def run_eval(
    *,
    domain: str = "airline",
    max_tasks: int | None = None,
    trials: int = 4,
    config: str = "full",
    api_key: str | None = None,
) -> None:
    """Run the τ-bench harness end-to-end and print a TauReport as JSON.

    Gate: MAGI_TAUBENCH_ENABLED=1 required.
    Agent model: claude-sonnet-4-5 (Anthropic).
    User-sim model: gpt-4o (OpenAI, tau-bench default).

    Infra-error handling: if run_episode returns infra_error=True for a
    (task, trial), retry that pair once. On persistent infra-error, count as
    non-success and increment infra_error_count so the noise is visible rather
    than silently attributed to model failure. trials remains uniform across
    tasks so pass^k formula holds.

    Args:
        domain: tau-bench environment domain (e.g. "airline", "retail").
        max_tasks: evaluate only the first N tasks (None = all tasks in
            the test split).
        trials: number of independent trials per task (tau-bench paper uses 4).
        config: "full" (all six control-plane flags enabled) or "vanilla" (none).
        api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
    """
    ensure_enabled()

    # Guard tau_bench import — not vendored; must be installed separately.
    try:
        from tau_bench.envs import get_env  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "tau_bench is not installed. "
            "Clone https://github.com/sierra-research/tau-bench and "
            "`pip install -e /path/to/tau-bench` into the magi-agent env, "
            "then set MAGI_TAUBENCH_ENABLED=1 and retry."
        ) from exc

    # Guard litellm import — needed by build_cli_model_runner -> LiteLlm.
    try:
        import litellm  # noqa: PLC0415  # type: ignore[import-untyped]
        litellm.suppress_debug_info = True
    except ImportError as exc:
        raise ImportError(
            "litellm is not installed. "
            "Run `pip install litellm` (or `uv pip install litellm`) "
            "into the magi-agent env and retry."
        ) from exc

    from magi_agent.benchmarks.taubench.agent import build_magi_tau_agent  # noqa: PLC0415
    from magi_agent.benchmarks.taubench.episode import EpisodeResult  # noqa: PLC0415
    from magi_agent.benchmarks.taubench.harness import run_subset, run_with_retry  # noqa: PLC0415
    from magi_agent.cli.providers import ProviderConfig  # noqa: PLC0415
    from magi_agent.cli.real_runner import build_cli_model_runner  # noqa: PLC0415

    effective_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not effective_api_key:
        raise ValueError("No Anthropic API key: pass api_key= or set ANTHROPIC_API_KEY")

    # Wrap the entire run in _apply_flags so control-plane flags are live while
    # runners are built/run, then restored — prevents leakage across run_eval calls.
    with _apply_flags(config):
        # Build the tau-bench env for the domain (user-sim uses gpt-4o).
        env = get_env(domain, user_strategy="llm", user_model="gpt-4o", split="test")
        task_indices = list(range(len(env.tasks)))
        if max_tasks is not None:
            task_indices = task_indices[:max_tasks]

        infra_error_count = 0

        def solve_one(task_index: int, trial: int) -> bool:
            """Run one (task_index, trial); retry once on infra-error."""
            nonlocal infra_error_count

            def _attempt() -> EpisodeResult:
                # Fresh env + state per (task, trial) — tau-bench envs are stateful.
                trial_env = get_env(
                    domain, user_strategy="llm", user_model="gpt-4o", split="test"
                )
                provider_config = ProviderConfig(
                    provider="anthropic",
                    model="claude-sonnet-4-5",
                    api_key=effective_api_key,
                )
                session_id = f"taubench-{domain}-t{task_index}-r{trial}"

                def runner_factory(*, instruction: str, tools: list) -> object:
                    return build_cli_model_runner(
                        provider_config,
                        instruction=instruction,
                        tools=tools,
                        user_id="taubench-agent",
                        session_id=session_id,
                    )

                agent = build_magi_tau_agent(runner_factory=runner_factory)
                solve_result = agent.solve(trial_env, task_index=task_index, max_num_steps=30)
                # Reconstruct an EpisodeResult from the SolveResult info dict.
                info = getattr(solve_result, "info", {}) or {}
                return EpisodeResult(
                    reward=getattr(solve_result, "reward", 0.0),
                    done=getattr(solve_result, "reward", 0.0) >= 1.0,
                    turns=info.get("turns", 0),
                    infra_error=bool(info.get("infra_error", False)),
                )

            success, infra_failed = run_with_retry(_attempt)
            if infra_failed:
                infra_error_count += 1
            return success

        report = run_subset(task_indices, trials=trials, solve_one=solve_one)

    output = {
        **report.model_dump(),
        "config": config,
        "domain": domain,
        "infra_error_count": infra_error_count,
    }
    print(json.dumps(output, indent=2))


__all__ = ["GateDisabledError", "ensure_enabled", "run_eval"]
