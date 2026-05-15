---
name: user-guide
description: Educate users on how to use their Open Magi bot — concepts, commands, workspace architecture, and skills. Trigger on explicit requests (tutorial, guide, how to use) or when user seems confused about bot capabilities.
user_invocable: true
triggers:
  - user asks for help, tutorial, guide, or "how to use"
  - user seems confused about bot concepts (memory, sessions, files, commands)
  - user asks "what can you do", "what are your capabilities"
  - new user's first interaction (no USER.md or empty USER.md)
---

# User Guide Skill

## Trigger Conditions

### Explicit (full guide)
- User asks: "사용법", "가이드", "튜토리얼", "how to use", "help me understand", "what can you do"

### Auto-detect (relevant section only)
- User asks about a file they don't understand → explain that file's purpose
- User is confused why you "forgot" something → explain Session & Memory
- User doesn't know how to give you long-term instructions → explain USER.md
- User asks "why are you slow" or mentions context → explain Context Window
- User wonders what you're capable of → explain Skills
- New user (USER.md is empty or missing) → offer quickstart

## Response Language

Use the user's language (from USER.md `language` field, or detect from their message).
Keep all technical terms in English as-is: Session, Context Window, Memory, Skill, /compact, /reset, MEMORY.md, etc.

---

## Quickstart Guide

When triggered explicitly, present this quickstart first. Keep it conversational — not a wall of text. Use analogies.

### 1. Session & Context Window

**Concept:** A Session is one continuous conversation. The Context Window is how much of that conversation I can "see" at once — think of it as my short-term memory or RAM.

**Key points:**
- Every message you send and every response I give takes up space in the Context Window
- When the Context Window fills up, older parts of our conversation get pushed out — I literally can't see them anymore
- A Session resets after ~20 minutes of inactivity. When it resets, the Context Window is cleared completely
- This is NOT a bug — it's how all LLM-based agents work

**Analogy:** Imagine a whiteboard with limited space. Every message we exchange gets written on it. When it's full, we have to erase the oldest parts to make room. A Session reset wipes the whole whiteboard clean.

### 2. Memory — How I Remember Across Sessions

**Concept:** Since the Context Window is temporary, I use files to persist important information — like writing notes in a notebook before the whiteboard gets erased.

**Key files:**
- **MEMORY.md** — My long-term memory. Core facts, user preferences, key decisions. Loaded every session.
- **memory/YYYY-MM-DD.md** — Daily logs. Detailed records of what we did each day. Permanent, never deleted.
- **ROOT.md** — An auto-generated summary index of recent activity + historical topics. Helps me quickly find past context.

**How it works:**
- At the end of each task, I automatically log what we did to the daily file
- Over time, daily logs get summarized into weekly → monthly summaries (auto-compaction)
- When a new Session starts, I read MEMORY.md + ROOT.md to restore context

**What you can do:**
- Ask me to "remember X" → I'll add it to MEMORY.md
- Ask me to "forget X" → I'll remove it
- Ask "what do you remember about X?" → I'll search my memory files

### 3. /compact & /reset — Commands You Can Use

**Two essential commands:**

| Command | What it does | When to use |
|---------|-------------|-------------|
| `/compact` | Summarizes the current conversation to free up Context Window space | When I start getting slow, repetitive, or "forgetting" things from earlier in our chat |
| `/reset` | Completely clears the current Session | When you want a fresh start, or the conversation has gone off track |

**Important:** `/reset` does NOT erase my Memory files — only the current conversation. I'll still remember our long-term context next time.

### 4. Skills — What I Can Do

**Concept:** Think of Skills like abilities in an RPG game. Each Skill is a specialized set of instructions that teaches me how to perform a specific task well.

**How it works:**
- I have 80+ Skills installed, each in its own folder under `/skills/`
- Before doing any task, I check if there's a relevant Skill and read it first
- Skills cover everything from web search, to Google integrations, to financial data, to coding standards

**Examples:**
- **web-search** — Search the internet and summarize results
- **google-calendar** — Read/create/modify your Google Calendar events
- **deep-research** — Multi-phase research with source verification
- **twitter** — Post, search, and manage Twitter/X
- **coding-standards** — TDD workflow, code review, git conventions

**You can ask:** "What skills do you have?" or "Can you do X?" and I'll check my skill list.

### 5. Workspace Files — My Brain Structure

**Concept:** My workspace is a set of files that define who I am, how I behave, and what I know. Think of it as my brain's architecture.

**Files you should know about:**

| File | Purpose | Can you modify? |
|------|---------|----------------|
| **USER.md** | Everything I know about you — name, language, preferences, expertise | Yes — tell me to update it |
| **IDENTITY.md** | Who I am — my name, personality, role | Set at creation |
| **MEMORY.md** | Long-term memory — key facts and decisions | Yes — ask me to remember/forget |
| **SCRATCHPAD.md** | Temporary working notes for the current task | Auto-managed |
| **WORKING.md** | List of tasks I'm currently working on | Auto-managed |
| **AGENTS.md** | My behavioral rules — how I operate | Frozen (not modifiable) |
| **TOOLS.md** | Reference for external services I can access | Frozen |

**The key insight:** USER.md and MEMORY.md are the two files that shape how I interact with you over time. The more accurate they are, the better I serve you.

---

## Deep Dive Topics

When a user asks about a specific topic, provide the relevant section below.

### Task Management

**For multi-step or multi-session work, I use a task tracking system:**

- **TASK-QUEUE.md** — Queue of pending tasks, each with status (pending/in-progress/done), context, and success criteria
- **WORKING.md** — What I'm actively working on right now
- **CURRENT-PLAN.md** (in plans/) — Step-by-step plan for complex tasks

**How to use it:**
- "Add X to the task queue" → I'll queue it with context
- "What's on your task list?" → I'll show pending tasks
- "Work on task X" → I'll pick it up and track progress
- `/bulk [tasks]` → Give me multiple tasks at once — I'll queue them and execute one-by-one without losing focus
- For big projects, I'll create a plan with checkpoints before starting

### Heartbeat — Autonomous Behavior

**I can act autonomously through a Heartbeat system:**

- Periodically, I wake up and check my state (even without your message)
- I review SCRATCHPAD, WORKING.md, TASK-QUEUE, and memory
- If there's pending work or maintenance needed, I handle it proactively

**What this means for you:** I'm not purely reactive. I can continue working on queued tasks, maintain my memory, and keep things organized between our conversations.

### Channels

**You can talk to me through multiple channels:**

- **Web** — openmagi.ai dashboard chat
- **Mobile** — iOS/Android app
- **Telegram** — Via BotFather-connected bot

All channels share the same memory and workspace. A conversation in Telegram and one on web both update the same MEMORY.md.

**Named channels** (like #general, #work, #personal) let you organize different conversation topics — each channel maintains its own session independently.

### AGENTS.md — My Operating Rules

**AGENTS.md is my "constitution" — the rules I always follow:**

- I run in the cloud (not on your device)
- I must check memory before starting any task
- I must verify before claiming work is done
- I follow specific file permission rules (some files I can't modify)
- I have safety boundaries (won't execute dangerous operations without confirmation)

This file is frozen — neither you nor I can modify it. It ensures consistent, safe behavior.

### File Attachments

**You can send me files:**

- **Web:** Drag-and-drop or file picker in chat
- **Mobile:** Media picker or document picker
- **Supported:** PDF, images, documents (up to 10MB)
- PDFs are automatically converted to text so I can read and discuss them

### Knowledge Base

**The `knowledge/` folder is my reference library:**

- Contains domain-specific documents I can search
- You can ask me to save important references there
- I search it automatically when relevant to your questions
- Think of it as my "bookshelf" — Memory is what I've learned, Knowledge is my reference material

---

## Auto-detect Response Templates

### User seems to have forgotten context was lost
> "It looks like you're referring to something from an earlier part of our conversation that may have been pushed out of my Context Window. Let me check my Memory files to see if I logged it..."
> [Search memory, then explain Context Window concept briefly if needed]

### User doesn't understand why you're "different" today
> "Each Session starts fresh — I rebuild my context from Memory files. If something feels off, it might be because a detail wasn't saved to my long-term memory. Want me to explain how my memory system works?"

### New user first interaction
> After greeting, offer: "Since this is our first conversation, would you like a quick tour of how I work? It'll take about 2 minutes and help you get the most out of me."
> If yes → deliver Quickstart sections 1-5 conversationally, one at a time.
> If no → proceed normally, but proactively explain concepts as they come up.
