# Execution Authority Product Wiring Design

## Goal

Turn the dormant `magi_agent.execution_authority` integrity components into a truthful first-party product feature: visible and configurable in Customize, persisted through the existing local override store, enforced at the live tool boundary, and reflected in completion evidence.

## Decisions

- Reuse `~/.magi/customize.json`; no database migration or second settings store.
- Add one first-party policy, `execution_integrity`, with an `off | audit | enforce` gate mode.
- Existing installs default to `audit`; a newly created Customize store records `enforce`. An explicit user choice always wins.
- The policy card exposes its component coverage (read-before-write, exact action admission, one-shot authority, durable journal/recovery, evidence lineage, and verification-before-completion) and the effective mode.
- The shared OS sandbox and universal broker remain explicitly marked `available`, not `live`; the UI never implies that these broader paths are enforced by this adapter.

## Runtime Architecture

`ToolDispatcher` is the single live admission boundary. An execution-integrity adapter binds the resolved manifest, exact canonical argument digest, call identity, effect class, and permission policy's read-ledger verdict into a signed one-call grant, then records admission and observation events. In `enforce`, mutating/effect-capable calls require that exact authority grant and fail closed when its identity, evidence, replay, or journal checks fail. In `audit`, the same decision and reason codes are recorded but dispatch continues.

The adapter owns process-scoped broker construction and a SQLite journal under the Magi state directory. Startup performs only the execution-authority journal's idempotent local schema migration. This is not a production Supabase migration.

Completion verification consumes journal/evidence closure for the current session and turn. `audit` annotates an unsupported completion claim; `enforce` blocks final completion until required observations exist.

## Customize Contract

- `gate_modes.execution_integrity` stores the explicit mode.
- The existing generalized gate-mode PATCH route persists and projects it.
- The unified policy catalog returns a builtin `execution_integrity` card with its mode options and component metadata.
- The existing Policies UI renders the card and selector; a small details section lists live coverage and the state/journal location without exposing secrets.

## Compatibility and Failure Semantics

- Existing stores have no explicit key and resolve to `audit`.
- Store creation writes `enforce`, making new installations safe by default.
- Corrupt settings resolve to `audit`, not `off`.
- Audit/journal failures fail open only in `audit`; they fail closed before effects in `enforce`.
- Read-only tools remain available when authority infrastructure is degraded.

## Verification

Focused unit tests cover mode resolution, persistence, catalog/UI projection, classification, audit/enforce behavior, journal initialization, read-before-write, and completion closure. Integration tests exercise Customize PATCH -> runtime decision -> audit projection. Existing Customize, dispatcher, and authority suites must remain green.
