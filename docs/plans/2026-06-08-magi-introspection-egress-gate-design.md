# Magi Agent — Self-Introspection Tool + Evidence-Grounded Egress Gate

**Date:** 2026-06-08
**Status:** Design (validated, pre-implementation)
**Scope:** OSS canonical `openmagi/magi-agent` (Python ADK runtime)

## Problem

The agent records rich runtime evidence (tool calls, file reads, workflow
phases, verifier verdicts) in `evidence/ledger.py`, but that evidence is only
consumed by **external** parties — verifiers, dashboards (`observability`), and
code-level gates. The agent itself has **no access to its own execution trace**:

1. **No metacognition tool.** When a user asks mid-conversation "did you
   actually read document X just now?" or "did you really follow the A→B→C
   workflow?", the agent answers from conversation history alone and can
   hallucinate. There is no `inspect_self_evidence`-style tool in
   `tools/catalog.py`.
2. **No evidence-grounded egress check.** `verifier_bus.py` (931 lines, staged
   deterministic → semantic_critic) and the `verifier_evidence_status` field
   (`transport/chat.py:293`) exist, but are wired into `cli/engine.py` /
   `cross_review` / `workflow_executor` — **not** the user-visible chat egress
   path. No lean pre-response check confirms the answer matches the query and is
   grounded in evidence for fact-critical turns.

Both gaps share one root: the evidence ledger is never **projected back** in a
form the model (pull) or a gate (push) can consume leanly — without reading raw
history.

## Core Insight

A self-introspection tool and an egress gate are the **same evidence substrate
viewed two ways**:

- **pull** → the model calls a tool to read its own evidence → metacognition
- **push** → a gate auto-checks the draft answer against the same evidence → egress verification

So the real design work is **one** lean projection layer; both features are thin
consumers of it.

## Design Decisions (validated)

| Decision | Choice |
|----------|--------|
| Verification approach | **A (structured claim channel) primary + B (process invariants) reinforcement** |
| Evidence scope/source | **Current session, in-memory ledger** (persisted store = YAGNI, follow-up) |
| Tool surface | **Single tool** with `query_type` param |
| Gate failure action | **Annotate + 1 regeneration**, then **fail-open** with status marking |
| Fact-critical trigger | **Deterministic ledger signals** + Haiku semantic classify on ambiguity. **No regex / pattern-matching on user text** (per `feedback_no_regex_labels`) |
| Defaults | All **default-OFF** behind independent flags |

## Architecture

```
evidence/ledger.py  (existing — already records everything)
        │  read-only accessor
        ▼
introspection/projection.py            ← SHARED CORE
   project_session_evidence(session, turn_filter=None) -> SessionEvidenceView
        │                                  │
        ▼ (pull)                           ▼ (push)
introspection/tool.py               introspection/egress_gate.py
 InspectSelfEvidence                 run_egress_check(draft, claims, view, task_contract)
 (catalog-registered, flagged)        ├─ deterministic A (claims ↔ view)
                                      ├─ deterministic B (process invariants)
                                      └─ conditional critic (introspection/fact_critical.py)
```

Principles:
- Projection is **pure read / deterministic** — summarizes ledger entries into a
  compact dict, zero side effects.
- **Raw history is never emitted** — always a summary view ("lean" guarantee).
- Tool and gate take the **same `SessionEvidenceView`** → the truth the model
  sees == the truth the gate sees (consistency by construction).
- All behind **default-OFF flags**; existing `ledger.py` / `verifier_bus.py` /
  `chat.py` get **additive seams only** (off ⇒ byte-identical egress).

## A — Structured Claim Channel (primary)

When the agent produces a fact-critical answer it emits, alongside prose, a
machine-readable claim list (reusing the existing `tool_evidence_contract`
input shape in `verifier_bus`, not a new tool):

```python
claims = [
  {"type": "file_read",   "ref": "X.pdf",    "turn": 4},
  {"type": "phase_done",  "ref": "B",        "turn": 5},
  {"type": "tool_called", "ref": "Grep:foo", "turn": 5},
]
```

The gate matches each claim 1:1 against `SessionEvidenceView`:
- `file_read X` → does the view contain a matching path (+ sha)? else `unsupported_claim`
- `phase_done B` → is phase B `reached=true` in the trace? else `unsupported_claim`

System-prompt convention (one line): "fact-critical claims (file read / action
performed / numeric source) must be registered in the claim channel." Omission is
safe because B backs it up.

## B — Process Invariants (reinforcement, claim-independent)

Independent of draft/claims, inspect the trace itself:
- Task contract required workflow A→B→C → are all three `reached` in the trace?
  else `phase_skipped`.
- An evidence-bearing tool failed (`status=error`) but the answer reads as success
  → escalate flag to critic (not a hard deterministic verdict).

A covers "what the answer claims"; B covers "what the process required". A
omission is caught by B's net. Both read only the view → deterministic, lean.

## Egress Gate Flow

`run_egress_check(draft, claims, view, task_contract)`:

```
[1] DETERMINISTIC (always, free)
    A: claims ↔ view        → unsupported_claim?
    B: invariants           → phase_skipped?
    violations? → [violation handling]
    clean?      → [2]

[2] FACT-CRITICAL DECISION (deterministic signals + Haiku on ambiguity)
    evidence-bearing activity present AND verification-style query?
      no  → PASS (skip critic, emit)
      yes → [3]

[3] CRITIC (conditional, 1 Haiku call)
    "Does the answer correspond to the query AND not contradict the view?"
      ok → PASS ; ng → accumulate violation

[violation handling] annotate + regenerate (hard cap = 1)
    feed violation back to model → regenerate once
    still deterministic-violating → fail-open emit, verifier_evidence_status="failed"
    only critic-failing           → emit, verifier_evidence_status="missing_evidence"
```

Cost profile:
- Ordinary turn (no evidence-bearing tools): passes [1], **0 critic calls, ≈0 added cost**.
- Fact-critical turn: [1]+[3] = ≤1–2 Haiku calls + (on violation) 1 regeneration.
- No infinite loop: regeneration hard cap = 1.

Flag: `MAGI_EGRESS_GATE_ENABLED` (default-off). Off ⇒ `chat.py` egress
byte-identical (seam is no-op).

## Self-Introspection Tool

`introspection/tool.py` → `InspectSelfEvidence` (registered in `catalog.py`,
flag `MAGI_SELF_INTROSPECTION_ENABLED`, default-off).

Input:
```
inspect_self_evidence(
  query_type: "files_read" | "tools_called" | "phases" | "verifier_verdicts" | "summary",
  turn:  int | None,    # specific turn; None = whole session
  ref:   str | None,    # optional filter (path / phase name)
)
```

Return (compact, no raw transcript):
```
{
  "scope": {"session_id": "...", "turns_covered": [1, 6]},
  "files_read":  [{"path": "X.pdf", "sha256": "a1b2…", "turn": 4, "bytes": 1234}],
  "tool_calls":  [{"name": "Grep", "status": "ok", "turn": 5}],
  "phases":      [{"name": "B", "reached": true, "turn": 5}],
  "verdicts":    [{"stage": "tool_evidence_contract", "result": "passed", "turn": 5}],
  "note": "projection of session ledger; not raw transcript"
}
```

Internally calls `project_session_evidence()` — the **same core** as the gate.

Scenario (hallucination blocked):
> User: "did you really read X.pdf just now?"
> → model calls `inspect_self_evidence(query_type="files_read", ref="X.pdf")`
> → `{files_read: [{path:"X.pdf", sha256:"a1b2…", turn:4}]}` or `{files_read: []}`
> → grounded answer: "yes, turn 4 (sha a1b2…)" / "no — not in the record, I
>   almost misspoke."

Prompt convention (one line): "for questions about your own actions, do not
guess — confirm with this tool."

## Fact-Critical Classification

`introspection/fact_critical.py` — reuse the `cli/readonly_classifier.py`
manifest-first → cache → LLM pattern. **No regex / text pattern matching.**

- **Primary (deterministic):** read from the ledger — was there evidence-bearing
  tool activity this turn? does the task contract demand a workflow? These come
  straight from code/ledger, zero text matching.
- **"Is this a verification / fact-critical query?":** resolved by **Haiku
  semantic classification** only when ambiguous (not regex). Result cached.

## File Changes

New (module — zero intrusion):
- `introspection/projection.py` — `project_session_evidence()`, `SessionEvidenceView`
- `introspection/tool.py` — `InspectSelfEvidence`
- `introspection/egress_gate.py` — `run_egress_check()`
- `introspection/fact_critical.py` — deterministic signals + Haiku fallback

Existing (additive seams only):
- `tools/catalog.py` — register 1 tool (flag-gated)
- `transport/chat.py` — 1 call site before egress (off ⇒ no-op, byte-identical)
- `runtime/message_builder.py` — 2 system-prompt convention lines
- `evidence/ledger.py` — expose read accessors only (recording logic unchanged)

Flags (default-off, independent): `MAGI_SELF_INTROSPECTION_ENABLED`,
`MAGI_EGRESS_GATE_ENABLED`

## Tests (TDD)

- projection: ledger fixture → view consistency, turn filter
- tool: per-`query_type` returns, empty-result (unread file) case
- gate: `unsupported_claim` caught / `phase_skipped` caught / ordinary turn 0
  critic calls / regeneration capped at 1 / fail-open + status marking
- fact_critical: decided by deterministic signals alone (0 Haiku) + ambiguous case
- **off-state byte-identical egress** test (project convention)

## PR Split (each independently mergeable, default-off)

1. **PR1** — shared core (`projection` + ledger accessors) + tests. No consumers.
2. **PR2** — self-introspection tool (on PR1).
3. **PR3** — egress gate deterministic A+B (on PR1, no critic).
4. **PR4** — fact-critical classification + conditional critic (on PR3).

## Out of Scope (YAGNI / follow-up)

- Persisted-store / cross-session-restart introspection (`gate2_durable_evidence`
  read path).
- Hosted (Vercel) multi-bot aggregation of egress verdicts.
- Free-form ledger DSL query mode.

## References

- `evidence/ledger.py`, `evidence/gate2_durable_evidence.py`
- `harness/verifier_bus.py` (staged deterministic → semantic_critic model)
- `transport/chat.py:293` (`verifier_evidence_status` field, reused)
- `cli/readonly_classifier.py` (manifest → cache → LLM pattern, reused)
- `tools/catalog.py` (tool registry)
