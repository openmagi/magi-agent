# Execution Authority Product Wiring Implementation Plan

**Goal:** Ship execution integrity as a visible, persisted, live first-party policy.

**Architecture:** Extend the existing generalized gate-mode policy system, then attach a narrow adapter at the central dispatcher and completion boundaries. Reuse the authority SQLite journal and Customize file store.

**Tech Stack:** Python 3.11+, Pydantic, SQLite, pytest, Next.js/React/TypeScript, Vitest.

### Task 1: Gate mode and first-party catalog

- Add failing tests for legacy/new-install defaults, persistence, env projection, and policy catalog metadata.
- Register `execution_integrity` in `GATE_MODE_POLICIES` and `BUILTIN_POLICIES`.
- Add normalized component metadata to the unified catalog.

### Task 2: Customize API and dashboard

- Add failing API tests for PATCH round-trip and invalid modes.
- Extend TypeScript catalog types and policy-card rendering tests.
- Render the live component coverage and mode selector through the existing policy surface.

### Task 3: Runtime authority adapter

- Add failing classifier/resource-derivation and audit/enforce tests.
- Implement process-scoped local journal/bootstrap and a dispatcher admission adapter.
- Wire the adapter into `ToolDispatcher` without changing read-only behavior.

### Task 4: Read-before-write and evidence closure

- Add failing mutation tests proving an unread target is audited/blocked by mode.
- Feed existing read-ledger observations into authority preconditions and journal decisions.
- Record post-dispatch observations and evidence lineage.

### Task 5: Verification-before-completion

- Add failing pre-final tests for unclosed mutating attempts.
- Attach the execution-integrity completion check to the existing policy boundary.
- Audit or block according to mode with deterministic reason codes.

### Task 6: End-to-end verification and PR

- Run focused Python and frontend suites after each slice.
- Run authority, Customize, dispatcher, CLI runtime-policy, and frontend Customize regression suites.
- Run lint/type checks proportional to touched code, review diff, commit, push, and open the OSS PR.
