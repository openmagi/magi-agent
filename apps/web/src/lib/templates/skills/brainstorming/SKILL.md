---
name: brainstorming
description: "You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation."
---

# Brainstorming Ideas Into Designs

## Overview

Help turn ideas into fully formed designs and specs through natural collaborative dialogue.

Start by understanding the current project context, then ask questions one at a time to refine the idea. Once you understand what you're building, present the design and get user approval.

**HARD GATE:** Do NOT write any code, scaffold any project, or take any implementation action until you have presented a design and the user has approved it. This applies to EVERY project regardless of perceived simplicity.

## Anti-Pattern: "This Is Too Simple To Need A Design"

Every project goes through this process. A todo list, a single-function utility, a config change -- all of them. "Simple" projects are where unexamined assumptions cause the most wasted work. The design can be short (a few sentences for truly simple projects), but you MUST present it and get approval.

## Checklist

Complete these in order:

1. **Explore project context** -- check files, docs, recent commits (read-only)
2. **Ask clarifying questions** -- one at a time, understand purpose/constraints/success criteria
3. **Propose 2-3 approaches** -- with trade-offs and your recommendation
4. **Present design** -- in sections scaled to complexity, validate each section with Kevin
5. **Write design doc** -- save to `plans/YYYY-MM-DD-<topic>-design.md` and commit
6. **Transition to implementation** -- use `skills/writing-plans/SKILL.md` to create implementation plan

## The Process

**Understanding the idea:**
- Check out the current project state first (files, docs, recent changes)
- Ask questions one at a time to refine the idea
- Prefer multiple choice questions when possible
- Only one question per message
- Focus on understanding: purpose, constraints, success criteria

**Exploring approaches:**
- Propose 2-3 different approaches with trade-offs
- Present options conversationally with your recommendation and reasoning
- Lead with your recommended option and explain why

**Presenting the design:**
- Once you believe you understand what you're building, present the design
- Scale each section to its complexity: a few sentences if straightforward, up to 200-300 words if nuanced
- Ask after each section whether it looks right so far
- Cover: architecture, components, data flow, error handling, testing
- Be ready to go back and clarify if something doesn't make sense

## After the Design

**Documentation:**
- Write the validated design to `plans/YYYY-MM-DD-<topic>-design.md`
- Commit the design document to git

**Implementation:**
- Read `skills/writing-plans/SKILL.md` and follow it to create a detailed implementation plan
- Do NOT start coding. Writing-plans is the next step.

## Key Principles

- **One question at a time** -- Don't overwhelm with multiple questions
- **Multiple choice preferred** -- Easier to answer than open-ended when possible
- **YAGNI ruthlessly** -- Remove unnecessary features from all designs
- **Explore alternatives** -- Always propose 2-3 approaches before settling
- **Incremental validation** -- Present design, get approval before moving on
- **Be flexible** -- Go back and clarify when something doesn't make sense
