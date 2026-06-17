# Why the Harness, Not Just Hooks

Two worked arguments for why durable guarantees need a runtime that owns state
transitions, not a prompt and not an external hook. The first grows one bot up
the layer stack until a requirement forces runtime machinery. The second shows
why the same guarantees cannot be bolted on as a third-party hook.

## One Task, Up the Stack

The clearest way to see how the layers connect is to grow one bot until each new
requirement forces the next layer. A lawyer wants a contract-review bot. Watch
the *kind* of each request decide which layer answers it, and where it stops
being "ask the model nicely" and becomes a runtime guarantee.

1. **Skill: "review contracts our firm's way."**
   A `review-contract` skill encodes the toxic-clause checklist, house style, and
   review order. No code; pure model-visible guidance. The need is a *procedure*,
   so a skill is enough.

2. **Tool: "you have to actually look up case law."**
   Register a `search_case_law` tool in the registry. A skill cannot add an
   ability; this is one new *capability* the model can call. Execution is
   otherwise unchanged.

3. **Recipe: "for contract review, always assemble this."**
   A `contract_review` pack declares `tool_refs=(search_case_law, read_pdf, …)`,
   a model, a review→verify phase split, and the few-shot and rule-injection for
   the domain. The pack *references* primitives; it does not implement them.
   Selecting it per task is how one runtime becomes a contract-review specialist
   without forking the agent.

4. **Evidence: "a legal opinion should cite its source."**
   The pack adds `evidence_refs=("citation:case-law-source",)`. Every
   `search_case_law` call is appended to the ledger at the dispatch seam
   (default-on), and a gate compares the final answer against the recorded
   sources so unsupported claims can be repaired, weakened, flagged, or blocked.
   This is the same receipts pattern coding uses (read receipts, stale-edit
   rejection, diff/test evidence). The point of this rung is that the requirement
   is *declared and recorded by the runtime*, not left to the prompt.

5. **Harness: "client PII must never leak to that external API."**
   Same `search_case_law` call, but a different *kind* of requirement. Not "cite
   your source" (cooperation) but "leaking must be *impossible*, even under a
   prompt injection or a model mistake." No skill, tool, recipe ref, or workflow
   can guarantee this; they all run *as*, or *through*, the (bypassable) model. It
   needs a new non-bypassable mechanism: an egress gate at the dispatch boundary
   that inspects every outbound payload and blocks PII before it leaves. That is
   harness work: a new runtime primitive, authored as a pack
   ([Extending the runtime](../README.md#extending-the-runtime)), and a recipe
   then references the new gate to switch it on.

The line is sharp. Rungs 1–4 are "tell the model, or declare from parts that
already exist." Rung 5 is the only one that requires building runtime machinery,
and you reach it only when you need a guarantee that holds **whether or not the
model cooperates, even across turns, even where the model cannot see it.** Most
domain work (legal, finance, research, operations) lives in rungs 1–4.

## Why Hooks Alone Are Not Enough

Hooks are useful. They can observe lifecycle events, add context, block a step,
or run checks before and after tool calls.

But strong deterministic guarantees usually require owning runtime state
transitions, not just seeing lifecycle payloads.

For example, take the rung-4 and rung-5 guarantees above ("cite the source" and
"PII can never leave") and try to build them as a third-party hook around an
existing agent. A `before_reply` hook may see the draft answer, but it may not
know which intermediate summaries were fed into the next model call. An
`after_tool` hook may see a tool result, but it usually cannot define a
structured source ledger, decide which claims become verified runtime state,
prevent unsupported claims from entering future context, or block an outbound
payload before it is sent. Even if the hook can inspect raw logs, it has to
reconstruct the whole run after the fact, which is expensive and imprecise.

First-party coding agents can be reliable because their core loop owns state
such as file reads, edits, diffs, test runs, stale-edit checks, and final commit
gates. If that behavior is not built into the agent core, a hook-based extension
can only approximate it from the outside.

Magi exposes that first-party level of control as configurable runtime surfaces:

- model-visible context;
- runtime-only evidence and claim state;
- tool and activity boundaries;
- transition gates;
- repair and fallback behavior;
- governed output projection;
- append-only audit/checkpoint state.

So the harness is not merely "a hook that checks the final answer." It can
declare the state it needs, the evidence it requires, the boundaries where
validation runs, and the transitions that are allowed to continue.
