"""Hypothesis profile registration for the authoring QA harness T0 tier.

CI determinism (design section 10.2): the property fuzz must be reproducible and
xdist-safe. The pytest suite runs under ``-n auto --dist loadfile`` (.github/
workflows/ci.yml:44), so a Hypothesis example DATABASE shared across workers
would be a flakiness / write-contention hazard. We therefore register a ``ci``
profile that:

- ``derandomize=True`` — the input stream is seeded deterministically from each
  test's identity, so a green run today is a green run tomorrow (no wall-clock
  entropy, no "passed 999 times then found a failure on run 1000" surprise in
  CI). Shrinking still works; only the top-level RNG is fixed.
- ``database=None`` — no on-disk example DB, so nothing to write-contend under
  xdist and nothing to leak between machines. The failing-example replay cache
  is a local-dev nicety, not a CI dependency.
- ``max_examples`` bounded (default 50 per property, design 10.2) to keep the
  whole T0 tier in low tens of seconds. Override for a local deep run with
  ``MAGI_AUTHORING_FUZZ_EXAMPLES=1000``.
- ``deadline=None`` — individual examples that touch the ASGI TestClient
  (fuzzed-envelope-through-``step_compile``) can exceed Hypothesis's default
  200ms per-example deadline on a cold import; a per-example timeout is not the
  property under test, so we disable it rather than let CI go flaky.

The profile is registered AND loaded here, unconditionally, so plain
``pytest tests/authoring_harness`` (exactly how CI invokes it) uses it with no
extra flags. ``settings.load_profile`` is idempotent and process-wide, which is
what we want: every T0 test inherits the same deterministic budget.
"""
from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

#: Per-property example budget. Design 10.2: 50 in CI, override up for a deep run.
CI_MAX_EXAMPLES = int(os.environ.get("MAGI_AUTHORING_FUZZ_EXAMPLES", "50"))

settings.register_profile(
    "ci",
    max_examples=CI_MAX_EXAMPLES,
    derandomize=True,
    database=None,
    deadline=None,
    # The store-side properties re-read/rewrite a tmp JSON store between
    # examples; that is intentional I/O, not "too slow", so silence the
    # data-generation-slowness heuristic which would otherwise flake on a
    # loaded CI box.
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile(os.environ.get("MAGI_AUTHORING_FUZZ_PROFILE", "ci"))
