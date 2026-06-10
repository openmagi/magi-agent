# Recipes

This package defines **recipe packs** (composition contracts) and **reliability
policies** for Magi Agent task execution.

---

## Conservative Orchestration Defaults (Principle 3)

**Advanced orchestration is default-OFF and enabled only with measured evidence
for the task class.**

GAIA measurement runs (2026-06) confirmed that enabling all orchestration
simultaneously *lowered* benchmark scores — the answer-verifier over-corrected,
deep-web added latency that caused timeout-EMPTYs, and ledger decomposition lost
direct tool-use flow.  All three were net-negative when on without evidence they
help.

### Affected capabilities

| Capability | Default-OFF gate | Enable signal |
|---|---|---|
| Ledger Orchestrator | `MAGI_LEDGER_ORCHESTRATOR_ENABLED` absent / `false` | Set env var to `true` |
| Deep-Web Research | `DeepResearchConfig(enabled=False)` / `MAGI_DEEP_WEB_RESEARCH_ENABLED` absent | Set env var to `1` or construct with `enabled=True` |
| Answer Verifier | `MAGI_ANSWER_VERIFIER_MODE` absent → `"off"` | Set env var to `"audit"` or `"enforce"` |

### Policy rules

1. **Never default-enable speculative capabilities.** Prefer the simplest path:
   strong model + direct tools.  Add complexity only where it demonstrably helps
   the **specific task class** (measured A/B, not assumed).

2. **Add complexity additive-but-conservative.** Each enabled orchestration step
   must have a documented evidence record (commit ref, benchmark delta) showing
   it improves the target task class.

3. **Audit before enforce.** Self-correction (verifier/reflection) must be
   validated in `audit` mode before `enforce` mode is used in any recipe.
   `enforce` requires strong over-correction guards (see `answer_verifier_checks.py`).

4. **Default-OFF is enforced by tests.** `tests/test_conservative_orchestration_defaults.py`
   asserts all three flags are OFF in a clean environment.  Any PR that changes
   a default must update and pass that test.

---

## Fail-Soft Provider Contract (Principle 7)

Every external provider must absorb its own errors and **return a short error
string or structured `{"status": "..."}` mapping** rather than raising an
exception.  A provider failure must never abort the task — the router falls back
to the next provider.

Requirements for every provider in `web_acquisition/providers/`:

- All public methods catch the broadest `except Exception` as an absolute
  backstop so no exception propagates to the caller.
- Transient failures (network timeout, connection error) return
  `{"status": "timeout"}`.
- Permanent failures (auth, policy denial, bad input) return
  `{"status": "denied"}`.
- `WebAcquisitionProviderRouter.run()` wraps its own routing logic in a
  try/except to return `repair_required` on any unexpected internal error.

Fail-soft compliance is verified by `tests/test_failsoft_providers.py`.
