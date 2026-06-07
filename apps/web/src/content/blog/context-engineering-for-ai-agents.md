---
title: "Context Engineering for AI Agents: Why Your Agent Gets Dumber Over Time (And How to Fix It)"
description: "AI agents don't just forget — they drown in their own context. From compaction traps to RAG limitations, we break down why context engineering is the hardest unsolved problem in agent infrastructure, and introduce Hipocampus: our open-source, multi-layer memory system built to solve it."
date: "2026-03-17"
tags: ["Context Engineering", "AI Agent", "LLM", "Memory", "Hipocampus", "Open Source"]
locale: "en"
author: "openmagi.ai"
---

There's a dirty secret in the AI agent space that nobody talks about.

Your agent doesn't just forget things. It actively gets worse the longer you use it. Not because the model degrades — but because its context does.

If you've ever run a long-lived AI agent and noticed it becoming slower, more expensive, and less accurate over time, you've experienced this firsthand. The cause isn't the model. It's everything *around* the model.

This is the problem of **context engineering** — and it's arguably the most important unsolved challenge in building production AI agents.

---

## What Is Context, Really?

When you send a message to an AI agent, your message is not the only input. The actual input looks something like this:

```
[System Prompt]
You are an AI marketing assistant...

[User Profile]
This user runs a small e-commerce business...

[Active Task State]
Currently working on Q1 ad campaign analysis...

[Conversation History]
User: Can you pull the ROAS data for January?
Agent: Here's what I found...
User: Good. Now compare it with December.

[Tool Call Results]
Google Ads API response: { "roas": 3.2, "spend": 12400, ... }
Analytics data: { "sessions": 45200, "conversion_rate": 0.032, ... }

[Current Message]
User: What should we change for February?
```

All of this gets bundled into a single input on every API call. The LLM reads everything from top to bottom and generates a response. It doesn't "remember" anything from previous calls — it references whatever is in the current context window.

Two critical implications:

1. **Everything in context costs tokens.** The system prompt, the conversation history, the tool results — all billed on every single API call.
2. **Everything in context competes for attention.** LLMs compute relationships between all tokens simultaneously (the Attention mechanism). More irrelevant information means more diluted attention. Important signals get buried in noise.

Context determines both the **cost** and the **quality** of your agent. Simultaneously. Every token you put in either helps or hurts.

---

## The Context Accumulation Problem

Here's where it gets ugly.

Most people think context accumulation means "the conversation gets longer." That's only a fraction of the problem.

Consider a real scenario: you ask your agent to research competitor pricing.

To answer that single question, the agent might:
1. Search the web for 5 competitor websites
2. Scrape pricing pages (full HTML, converted to markdown)
3. Read internal documents for your own pricing history
4. Pull data from a spreadsheet
5. Analyze the findings and write a summary

By the time it delivers its answer, the context now contains:
- 5 web pages worth of competitor data
- Your internal pricing docs
- Spreadsheet data
- The agent's analysis and reasoning
- All the intermediate tool call results

That's potentially **50,000+ tokens** of research data sitting in the session context.

Now you say: "Great, thanks. Can you draft an email to the team about tomorrow's standup?"

A completely unrelated task. But all 50,000 tokens of competitor pricing research are **still in context**. They're still being billed. They're still competing for the model's attention.

The agent is now writing a standup email while "thinking about" competitor pricing data. The email quality drops. The cost doubles. And neither you nor the agent realizes why.

**This is the fundamental problem: context is append-only by default.** Every tool call, every search result, every intermediate step — it all stays. Tasks bleed into each other. Costs compound. Quality degrades.

And it only gets worse from here.

---

## Attempt #1: Compaction

The most obvious fix is compaction — when the context gets too long, ask the LLM to summarize it.

Most agent frameworks support this. When the conversation hits a threshold (say, 80% of the context window), the entire history gets compressed into a summary. Fresh start, smaller context.

Sounds elegant. In practice, it has two fatal flaws.

### Context Drift

A summary of a summary of a summary loses information exponentially:

- **Round 1:** "User is a React developer working on a Next.js project with TypeScript, focused on server components."
- **Round 2:** "User does web development."
- **Round 3:** "User works in tech."

After just 2-3 compaction cycles, critical details evaporate.

### No Importance Discrimination

Compaction treats all information equally. But not all information is equal:

- "User has a severe peanut allergy" — life-critical, needed months later
- "User asked about the weather today" — irrelevant by tomorrow

Compaction can't distinguish between these. It applies the same compression ratio to everything. Life-critical information gets lost alongside trivial chatter.

**Compaction is lossy compression with no priority mechanism.** It buys you time, but it doesn't solve the problem.

---

## Attempt #2: Structured Context Files

A better approach: instead of keeping everything in conversation history, write important information to structured files.

This is the `.md`-based context pattern used by most serious agent setups:

- **`MEMORY.md`** — Long-term facts about the user and project (~50 lines)
- **`SCRATCHPAD.md`** — Current working state and active tasks (~100 lines)
- **`AGENTS.md`** — Behavioral rules and instructions (~500 lines)

The agent reads these files at the start of every session. Instead of relying on conversation history (which gets compacted and degraded), core information lives in persistent files that survive across sessions.

This is a huge improvement. But it introduces new problems:

**Size pressure.** These files are loaded on every API call. 500 lines of AGENTS.md means 500 lines of tokens billed on every single message. Grow MEMORY.md to 200 lines with detailed notes? That's 200 lines of cost on every call, even when the user is just saying "hi."

**Curation burden.** Someone (the agent or the user) has to decide what goes into these files. Too much → cost explosion and attention dilution. Too little → critical information gets missed.

**Flat structure.** A single MEMORY.md file has no hierarchy. Is the information from yesterday? Last month? Still relevant? There's no way to know without reading everything.

Structured files are necessary but insufficient. They solve the "where does important stuff live" problem but not the "how do I find the right stuff at the right time" problem.

---

## Attempt #3: Adding RAG

Retrieval-Augmented Generation (RAG) addresses the search problem. Instead of loading everything into context, you store knowledge in a searchable index and retrieve only what's relevant.

Store your agent's accumulated knowledge in files. Index them with a search engine (BM25 keyword search, vector embeddings, or both). When the agent needs information, it searches the index and pulls only the relevant chunks.

This is powerful. An agent with 10,000 documents worth of knowledge only loads the 3-5 most relevant ones for each query. Cost stays flat. Attention stays focused.

But RAG has its own limitations:

**You need to know what to search for.** RAG works when you have a clear query. But what about ambient context — things the agent should "just know" without being asked? A user's timezone, communication preferences, ongoing project status. You can't search for these proactively because you don't know you need them until it's too late.

**Indexing lag.** Information written in the current session isn't immediately searchable. The agent learns something important at 2:00 PM, but the index doesn't update until the session ends. By then, the agent may have already needed that information.

**No temporal awareness.** RAG returns the most semantically relevant results, but it has no concept of recency or decay. A decision from three months ago and a decision from this morning get equal weight. In practice, recent context is almost always more relevant.

**Cold start.** A new agent with an empty knowledge base can't search for anything. RAG only works after enough knowledge has been accumulated — which requires the very context management it's supposed to provide.

---

## The Real Problem: No One Solves the Full Stack

Each approach solves one piece:

| Approach | Solves | Misses |
|----------|--------|--------|
| Compaction | Context overflow | Information loss, no priorities |
| Structured files | Persistent memory | Scaling, curation, flat structure |
| RAG | Search-based retrieval | Ambient context, temporal awareness, cold start |

But production agents need all of these working together, with something more on top. They need a system that:

1. Preserves raw information permanently (no lossy compression)
2. Creates searchable indexes at multiple time scales
3. Loads the right context at the right time
4. Works from day one (no cold start)
5. Self-maintains without human curation

This is what we built.

---

## Introducing the Compaction Tree

The core insight: **never delete originals. Build search indexes on top.**

Think of it like a library. Traditional compaction is like burning your books and keeping only the table of contents. A compaction tree keeps every book on the shelf and adds a card catalog system.

```
memory/
├── ROOT.md                 ← Always loaded (~100 lines)
│                              Topic index: "Do I know about X?"
├── monthly/
│   └── 2026-03.md          ← Monthly keyword index
│                              "In March, topics included: ..."
├── weekly/
│   └── 2026-W11.md         ← Weekly summary
│                              Key decisions, completed tasks
├── daily/
│   └── 2026-03-15.md       ← Daily compaction node
│                              Topics, decisions, outcomes
└── 2026-03-15.md            ← Raw daily log (permanent, never deleted)
                               Full details of everything that happened
```

**The traversal pattern:**

Need to find something? Start at the top:

1. **ROOT.md** — Check the Topics Index. Do I know about "competitor pricing"? Yes → noted in March.
2. **Monthly** — March index says competitor analysis happened in Week 11.
3. **Weekly** — Week 11 summary shows pricing research was on March 12.
4. **Daily** — March 12 node has key decisions and findings.
5. **Raw** — March 12 raw log has the full, uncompressed original.

This is **O(log n) search** through temporal memory. You never read more than you need, but the full detail is always available if you drill down.

### Fixed vs. Tentative Nodes

Compaction nodes have a lifecycle:

- **Tentative** — The period is still ongoing. The node gets regenerated when new data arrives. Today's daily node is tentative. This week's weekly node is tentative.
- **Fixed** — The period has ended. The node is frozen and never updated again. Last week's weekly node is fixed.

This means the tree is **usable from day one**. You don't wait for a week to pass before the weekly summary exists. It's created immediately as tentative, and updated as new data arrives.

### Smart Thresholds

Not everything needs LLM summarization. If a daily log is 50 lines, copying it verbatim to the daily node costs nothing and loses nothing. Only when content exceeds a threshold do we engage LLM summarization:

| Level | Threshold | Below | Above |
|-------|-----------|-------|-------|
| Raw → Daily | ~200 lines | Copy verbatim | LLM keyword-dense summary |
| Daily → Weekly | ~300 lines | Concat dailies | LLM summary |
| Weekly → Monthly | ~500 lines | Concat weeklies | LLM summary |

Below the threshold: zero information loss. Above: keyword-dense compression optimized for search recall, not narrative readability.

---

## Hipocampus: The Full System

The compaction tree is the data structure. [**Hipocampus**](https://github.com/kevin-hs-sohn/hipocampus) is the full system built around it — a 3-tier agent memory protocol that we developed, battle-tested in production, and open-sourced.

### Three Layers

```
Layer 1 — System Prompt (always loaded, every API call)
  ├── ROOT.md          ~100 lines   Topic index from compaction tree
  ├── SCRATCHPAD.md    ~150 lines   Active working state
  ├── WORKING.md       ~100 lines   Current tasks
  └── TASK-QUEUE.md    ~50 lines    Pending items

Layer 2 — On-Demand (read when the agent decides it needs them)
  ├── memory/YYYY-MM-DD.md    Raw daily logs (permanent)
  ├── knowledge/*.md           Detailed knowledge files
  └── plans/*.md               Task plans

Layer 3 — Search (via compaction tree + keyword/vector search)
  ├── memory/daily/            Daily compaction nodes
  ├── memory/weekly/           Weekly compaction nodes
  └── memory/monthly/          Monthly compaction nodes
```

**Layer 1** answers "what am I working on right now?" — always in context, always paid for, kept ruthlessly small.

**Layer 2** answers "what do I know in detail?" — free until accessed, loaded on-demand when the agent recognizes it needs more context.

**Layer 3** answers "have I seen this before?" — ROOT.md's Topics Index tells the agent at a glance whether information exists in memory, without loading anything. If it does, tree traversal or keyword search retrieves it.

### Session Protocol

Hipocampus defines two mandatory rituals:

**Session Start:** Before responding to anything, the agent loads Layer 1 files and runs the compaction chain (Daily → Weekly → Monthly → Root). This ensures the tree is fresh and ROOT.md reflects the latest state.

**End-of-Task Checkpoint:** After completing any task, the agent writes a structured log to the raw daily file:

```markdown
## Competitor Pricing Analysis
- request: Compare our pricing with top 5 competitors
- analysis: Scraped pricing pages, pulled internal data
- decisions: Recommended 15% reduction on starter tier
- outcome: Report delivered, shared with team
- references: knowledge/pricing-strategy.md
```

This is the source of truth. Everything else — compaction nodes, ROOT.md, the Topics Index — is derived from these raw logs through the compaction chain.

### The ROOT.md Advantage

The most powerful feature is ROOT.md's Topics Index. It solves the "search for what?" problem:

```markdown
## Topics Index
- pricing: competitor-analysis, Q1-review, starter-tier-reduction
- infrastructure: k8s-migration, redis-upgrade, node-scaling
- marketing: ad-campaign-Q1, landing-page-redesign, SEO-audit
```

When a user asks about pricing, the agent doesn't need to search blindly. It checks the Topics Index, sees that pricing information exists, and knows exactly which time period to drill into. If a topic isn't in the index, the agent knows to search externally rather than wasting time searching empty memory.

**This eliminates the "loading to decide whether to load" problem** — the single biggest efficiency drain in RAG-based memory systems.

### Proactive Dumps

Hipocampus doesn't wait for task completion to persist context. The protocol encourages proactive dumps — when the conversation has been going for 20+ messages, when significant decisions are made, or when the agent senses context is getting large.

This protects against a subtle but devastating failure mode: **context compression by the platform.** When the hosting platform compresses conversation history (as most do for long sessions), any undumped details are lost permanently. Write early, write often. The raw log is append-only, so multiple dumps in a session are harmless.

---

## Why This Matters for Agent Platforms

Most agent platforms focus on deployment. Click a button, your bot is live.

But deployment is maybe 5% of the problem. The other 95% is **operations** — keeping the agent useful, accurate, and cost-efficient over weeks and months of continuous use.

Without proper context engineering:
- Your agent's costs grow linearly with usage
- Quality degrades as context accumulates irrelevant information
- Critical knowledge gets lost in compaction cycles
- The agent can't distinguish between what it knew yesterday and what it knew three months ago

At [Open Magi](https://openmagi.ai), we built [Hipocampus](https://github.com/kevin-hs-sohn/hipocampus) because we needed it ourselves. We run hundreds of agents in production, and we watched them all hit the same wall: they'd work great for a few days, then gradually become expensive, slow, and forgetful.

Hipocampus is now the default memory system for every agent on our platform. When you deploy an agent on Open Magi, you're not just getting a chatbot with an API key — you're getting the full context engineering stack: hierarchical compaction, multi-layer memory, RAG search, and session protocols that keep the agent sharp over months of continuous operation.

Because deploying an agent is easy. *Keeping it useful* is the hard part.

---

*Hipocampus is open source. Check out the [GitHub repository](https://github.com/kevin-hs-sohn/hipocampus) to use it in your own agent setup.*

*This is the first in a series on the infrastructure behind production AI agents. Next up: what an AI Agent OS actually looks like — and why agents need operating systems just like apps do.*
