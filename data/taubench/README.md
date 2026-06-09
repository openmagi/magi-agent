# τ-bench setup (live runs only)

tau-bench is clone-only (MIT), not vendored into this repo. Tests use a fake env
and need none of this. For a LIVE run:

    git clone https://github.com/sierra-research/tau-bench /path/to/tau-bench
    pip install -e /path/to/tau-bench   # into the same env as magi-agent

Set `TAUBENCH_PATH=/path/to/tau-bench` (or install it importable as `tau_bench`).
Keys: ANTHROPIC_API_KEY (agent = current Anthropic provider default),
OPENAI_API_KEY (user-sim = gpt-4o).
Enable the harness: MAGI_TAUBENCH_ENABLED=1.
