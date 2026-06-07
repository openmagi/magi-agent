---
title: "Rules You Write Beat Prompts You Hope It Follows"
description: "AI agents break in predictable ways — claiming completion without proof, citing files they never read, promising future delivery. Better prompts cannot fix this. Programmable rules can."
date: "2026-04-27"
tags: ["AI Agent Rules", "Programmable Agents", "AI Agent Reliability", "AI Automation", "AI Agent", "Open Magi Agent", "Runtime Enforcement", "Agent Trust"]
locale: "en"
author: "openmagi.ai"
---

Every AI agent team hits the same wall.

The agent works well enough in demos. It reads documents, writes reports, searches the web, delivers files. Then you put it in front of a real workflow, and it starts breaking in ways that feel familiar.

It says it created a report. No file exists. It cites a spreadsheet. It never opened one. It tells the user "I will follow up with the analysis tomorrow." There is no mechanism to make that happen. It says "done" after three tool calls, but the fourth — the one that actually mattered — silently failed.

These are not rare edge cases. They are the default failure modes of prompt-driven agents.

And the usual response makes them worse.

---

## The Prompting Treadmill

When agents break, the first instinct is to add more instructions.

"Always verify your work before saying you are done."

"If a tool call fails, retry it."

"Never claim you created a file unless you confirm it exists."

"Do not promise future actions you cannot execute."

This works, briefly. The agent becomes more careful for a while. Then the context window shifts, or the task gets complex, or a new model version rolls out, and the same failures return.

The problem is structural. A prompt is a suggestion. The model can follow it, partially follow it, or ignore it entirely. There is no enforcement. There is no record of whether the behavior happened. There is no consequence when it does not.

You are not defining rules. You are writing wishes.

---

## Agents Break in Predictable Ways

The failures are not random. They fall into a small number of patterns that repeat across every agent framework.

**Completion without proof.** The agent says "done" but produced no artifact. No file, no saved record, no verifiable output. The user has to ask "where is it?" and the cycle restarts.

**Citation without reading.** The agent references a document, quotes a number, or summarizes a file it never actually opened. The tool call log shows no read operation. The model hallucinated the reference from its training data or from a filename it saw in a directory listing.

**Phantom delivery.** The agent says "I will send this to you shortly" or "I have scheduled this for later." There is no scheduling system. There is no delivery mechanism. The promise evaporates after the turn ends.

**Silent tool failure.** A tool call returns an error. The agent continues as if it succeeded, incorporating non-existent results into its response. The user sees a confident answer built on a failed foundation.

Every one of these can be caught. None of them can be caught by prompting alone.

---

## What Changes When Rules Are Programmable

The fix is not better prompts. It is giving the operator — the person who understands the domain — the ability to define rules that the runtime enforces.

Not "please verify your work." Instead: "The agent must produce a file artifact before any turn that claims task completion. If no artifact exists, the turn is blocked."

Not "please read the file before citing it." Instead: "If the agent references a document by name, it must have called a read operation on that document within the current task. If it has not, flag the response."

Not "do not promise future delivery." Instead: "The agent cannot use future-tense delivery language unless a verified async handoff exists — a background task ID, a cron job, a scheduled operation. If none exists, the statement is blocked as an unbacked promise."

These are not prompt instructions the model might follow. They are runtime checks the system performs before the response reaches the user.

The difference matters because it changes who is responsible for correctness.

With prompting, the model decides whether to comply. With rules, the operator decides what compliance means, and the runtime decides whether it happened.

---

## The Operator Knows What Correct Looks Like

Framework vendors cannot anticipate every domain. A legal team needs different correctness rules than an accounting team. A customer support workflow has different completion criteria than a research workflow.

When rules are programmable, the person closest to the work defines what "done" means.

An accounting operator might write: "Any financial report must include a source reference for every number. The agent must have retrieved the source within this session."

A legal operator might write: "Document drafts require explicit user approval before the agent marks the task complete. Approval must be a specific confirmation phrase, not just 'okay.'"

A sales operator might write: "The agent must not send any external communication without showing the user a preview first."

None of these rules require the operator to understand model architecture, prompt engineering, or token limits. They require the operator to understand their own domain. The runtime translates domain knowledge into enforcement.

This is the real shift. Not "we built a smarter agent." Instead: "you define what correct means, and the agent proves it before delivering."

---

## Before and After

Consider a simple task: "Analyze last quarter's revenue data and give me a summary."

**Prompt-only agent.** The agent receives the instruction. It calls a search tool, gets partial results, and writes a summary. The search actually returned an error on one of three queries, but the agent continues. The summary references "Q4 revenue of $2.3M" — a number from the model's training data, not from the user's actual data. The user reads it, trusts it, and forwards it to their team.

**Rule-enforced agent.** Same instruction. Same search error. But now a rule exists: "Revenue figures must trace to a retrieved data source within the current session. Ungrounded financial numbers are flagged." The runtime checks the agent's response against the tool call log. The $2.3M figure has no backing retrieval. The response is held. The agent is told to either re-retrieve the data or state explicitly that the number could not be confirmed. The user gets an honest answer instead of a confident wrong one.

The model is identical in both cases. The intelligence is the same. The difference is whether anyone defined what "correct" means and whether anything checked.

---

## Trust Is a Verification Problem

People do not trust agents because agents are smart. People trust agents when agents can prove what they did.

Did the agent read the file it cited? Check the tool log. Did the agent produce the artifact it claimed? Check the workspace. Did the agent complete all steps? Check the execution record. Did the agent get approval before acting? Check the consent log.

When these checks are programmable — when the operator defines them, and the runtime enforces them — trust stops being a function of hope.

It becomes a function of evidence.

This is what Open Magi Agent is building toward. Not an agent that follows instructions more reliably through better prompting. An agent where the operator writes the rules, the runtime enforces the rules, and the model operates within boundaries it cannot talk its way out of.

The programmable agent that runs on rules you write — not prompts you pray it follows.
