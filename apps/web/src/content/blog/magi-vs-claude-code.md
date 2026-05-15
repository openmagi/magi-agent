---
title: "Magi vs Claude Code: Prompts You Hope vs Rules You Enforce"
description: "Claude Code assumes a human is watching. Magi assumes no one is. One relies on prompt instructions. The other enforces rules in code. Here's why that matters."
date: "2026-05-13"
tags: ["Magi Agent Runtime", "Claude Code", "AI Agent Architecture", "Programmable Agents", "Open Magi Agent"]
locale: "en"
author: "openmagi.ai"
---

Claude Code is one of the best tools for working *with* an AI agent. Magi is built for when the agent works *without* you. They solve different problems — and understanding the difference matters if you're choosing between them.

## The core difference

With Claude Code, you control the agent through instructions: CLAUDE.md files, system prompts, rules written in natural language. The model reads them and tries to follow them. When it drifts, you catch it and correct it. You are the enforcement layer.

With Magi, you control the agent through programmable rules: hooks, gates, and classifiers written in code. The runtime enforces them on every response before it reaches the user. The model cannot skip them. You are the rule author, not the reviewer.

**Instructions are suggestions.** The model decides whether to follow them.

**Rules are code.** The runtime decides whether the output ships.

## When instructions work

Instructions work well when someone is watching. You write "always run tests before claiming a fix" in CLAUDE.md. The agent usually follows it. When it doesn't — when it says "tests pass" without running them — you see it, call it out, and the agent corrects itself.

This is Claude Code's design, and it's the right one for interactive development. The developer is fast, accurate, and already looking at the output. Adding automated verification would just slow things down.

Claude Code also has real enforcement mechanisms beyond prompts: permission gates that control file access, command execution, and network calls. These are genuine runtime controls. The agent cannot bypass them. But they answer a different question — "is this action allowed?" rather than "is this output correct?"

## When instructions break

Instructions break when no one is watching.

The same agent running a scheduled task at 4 AM follows the same CLAUDE.md. But now there's no human to catch the skipped test run. The "tests pass" claim goes straight to the user or the team channel. The instruction was clear. The model understood it. It just chose not to follow it — because instructions are not binding.

This happens more often than you'd expect. In our production bots, we've seen every one of these patterns repeatedly:

**Citing without reading.** The agent references "config.yaml" in its response without having opened the file. The cited values come from training data or context from a previous turn, not from the actual file. The response sounds authoritative. The data is stale or wrong.

**Phantom delivery.** The agent promises "I'll send you the results when the analysis is complete" and ends the turn. No background task was created. No cron job was scheduled. No mechanism exists to deliver anything after the turn ends. The user waits. Nothing arrives. The agent sounded helpful. It produced nothing.

**Plausible fabrication.** A scheduled report says "revenue was approximately 32 million won." The actual figure from the database is 28 million. The agent generated a plausible-sounding number instead of querying the source. The format is correct. The confidence is high. The number is wrong.

**Partial completion.** The agent spawns three sub-tasks for a document analysis. Two complete. The third fails silently — a timeout, a parse error, a missing file. The agent reports "analysis complete" and delivers a summary based on two out of three sources. Nothing in the output indicates anything was missing.

These aren't rare edge cases. They're the predictable failure modes of language models operating without enforcement. Every one of them passes all permission checks. The agent had access to every tool it needed. It just didn't use them correctly — and nothing in the pipeline caught it.

## How Magi enforces rules — four layers deep

Magi doesn't rely on a single mechanism. It enforces rules across four independent layers, each addressing a different class of failure.

**Classifier.** A fast LLM call (Haiku-class) classifies every turn at two phases — when the request arrives and when the final answer is ready. It detects intent, deferral patterns, completion claims, and whether the task requires deterministic data (dates, calculations, database queries). This classification is shared across all hooks — one call serves the entire verification stack. You can add custom classifier dimensions in YAML for your domain.

**Hooks.** 74 hooks run at every point in the agent lifecycle: before the model is called, before and after each tool use, before the response commits, after it delivers. Blocking hooks reject a response and force the model to retry with specific feedback about what went wrong. You write custom hooks in TypeScript — same interface as built-in hooks, no adapters needed. A hook can be as simple as a string check or as sophisticated as an LLM judge call.

**Policy engine.** The PolicyKernel compiles your rules — from markdown files, dashboard safeguards, or harness-rules — into typed objects. The ExecutionContract tracks what actually happened during the turn: which tools ran, which files were read, which claims were made. Hooks query this structured state instead of parsing raw transcripts. This is what makes checks like "did the agent actually read the file it's citing?" possible — the runtime knows which files were read, not just what the agent says it read.

**Meta-agent architecture.** The main agent focuses on planning, verification, and steering. Actual execution — file operations, searches, code changes — is delegated to sub-agents. The controller never generates the output it verifies. This structural separation is more reliable than self-policing, where the same model that produced a claim also judges whether the claim is accurate.

## What a rule looks like

A hook that uses an LLM judge to evaluate whether a financial response needs compliance review:

```typescript
const hook: Hook = {
  name: "compliance-review-gate",
  point: "beforeCommit",
  priority: 100,

  async execute(ctx: HookContext): Promise<HookResult> {
    const verdict = await ctx.callJudge({
      prompt: "Does this response provide specific investment advice or return projections without disclaimers?",
      input: ctx.pendingResponse,
      schema: { needsReview: "boolean", reasoning: "string" },
    });

    if (verdict.needsReview) {
      return { action: "block", reason: verdict.reasoning };
    }
    return { action: "pass" };
  },
};
```

This hook calls a fast model to make a judgment call — not a string match, not a regex, but an actual assessment of whether the content crosses a compliance boundary. The main agent never sees this check. It just knows its draft was rejected and why.

You can also write purely deterministic hooks — cross-referencing cited files against tool calls, checking that numeric claims match database results, verifying that promised artifacts were actually produced. The hook interface supports both.

## The tradeoffs

Verification adds latency — roughly half a second to a second per response for the classifier and hook pipeline. For a developer watching a cursor blink in a terminal, that matters. For an autonomous agent answering a customer at 3 AM, it's invisible.

The hooks aren't perfect. LLM-based judges can miss subtle errors. Rule-based checks can produce false positives. Every hook in Magi is designed to fail open — if a check times out or returns an ambiguous result, the response ships. A stuck agent that never responds is worse than an occasionally imperfect one, especially when no human is around to unstick it.

The cost is roughly 3% of LLM spend — the classifier call plus occasional judge calls from individual hooks. For a bot handling 500 turns per day, that's $2–6 daily.

## Which one to use

**Use Claude Code** if a human reviews every response. It's faster, lower friction, and the human catches errors better than any automated check. Claude Code's permission model is excellent for controlling what the agent *can* do. The developer controls whether the output is *correct*.

**Use Magi** if the agent runs without supervision — scheduled tasks, customer-facing bots, document pipelines, background workflows. When no human is watching, you need rules that enforce output correctness, not just permissions that control access. Rules written in code — deterministic or LLM-judged — enforced by a runtime, that the model cannot skip.

They are not competitors. They are designed for different operating assumptions. The best coding agents assume a human is watching. The best work agents assume no one is.

---

Magi is open source under Apache 2.0 at [github.com/openmagi/magi-agent](https://github.com/openmagi/magi-agent).
