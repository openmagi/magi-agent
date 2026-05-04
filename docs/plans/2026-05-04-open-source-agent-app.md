# Open-Source Clawy Agent App Plan

## Goal

Open-source a self-hostable frontend for Clawy Agent without publishing the
hosted Clawy Cloud control plane.

The target product is a Codex-like personal agent app that anyone can run with
their own LLM provider, workspace, memory, schedules, tools, and harness rules.
The hosted Clawy product can then compete on managed uptime, provisioning,
credential handling, social/browser infrastructure, billing, and support rather
than on hiding client-side UI code.

## Positioning

Clawy Agent is the open-source runtime. Clawy Agent App should become the
open-source workbench for that runtime.

Marketing frame:

- Build your own personal agent app.
- Bring your own LLM provider.
- Run it locally, self-host it, or upgrade to managed Clawy Cloud.
- Inspect the work: transcript, tools, tasks, files, memory, schedules, and
  verification evidence are visible runtime state.

This should not be positioned as a plain coding-agent clone. OpenCode, Codex,
Claude Code, and similar tools already own the terminal coding surface. Clawy's
angle is broader: always-on personal agents with durable memory, scheduled
workflows, multi-channel delivery, background agents, generated files, and
runtime discipline gates.

## Open-Source Scope

The open-source app should include:

- Local-first chat UI for a running Clawy Agent instance.
- Runtime connection setup for local HTTP/SSE or a self-hosted endpoint.
- Provider configuration UI for API-key based providers and local provider
  adapters.
- Session transcript viewer with tool calls, tool results, thinking blocks,
  background task events, delivery events, and commit checkpoints.
- Workspace file and artifact panels.
- Task board and child-agent task inspector.
- Memory inspector for Hipocampus root/daily/weekly/monthly/qmd state.
- Cron and scheduled workflow inspector.
- User Harness Rules editor that writes Markdown rule files into the workspace.
- Skills viewer/reload control for workspace `skills/`.
- Docker Compose starter that runs the app and agent together.
- Clear upgrade path to Clawy Cloud for managed hosting.

The app can reuse concepts and UI patterns from the hosted frontend, but should
be packaged as a separate self-hostable app surface with no default dependency
on Clawy production services.

## Hosted-Only Scope

Do not open-source these as part of the self-hosted app:

- Privy production auth and wallet flows.
- Stripe billing, credits, plans, invoices, or subscription enforcement.
- Supabase production schema, migrations, RLS policies, service role flows, and
  hosted data contracts.
- Hetzner, k3s, image rollout, fleet provisioning, and health monitor
  operations.
- Production Telegram/Discord fleet orchestration.
- Hosted chat proxy, API proxy, provisioning worker, admin dashboards, growth
  analytics, and operator backoffice.
- Managed social-browser credential broker and session claiming backend.
- Secrets, production endpoints, customer data, telemetry keys, and internal
  operational runbooks.

The self-hosted app may expose compatible extension points for auth, billing,
remote hosting, and browser credentials, but those should be adapter interfaces
or disabled placeholders unless a user configures them.

## Provider Policy

The first public version should support ordinary provider adapters such as API
keys, local model endpoints, or explicitly supported local CLI bridges.

For Codex or OAuth-style integrations, documentation and UI text must be careful:
support them only where the upstream provider policy permits it. Avoid marketing
claims that imply third-party OAuth embedding is generally allowed before that is
confirmed.

## Candidate Source Material

Private hosted frontend pieces that are likely reusable after decoupling:

- `src/components/chat/*`: message rendering, thinking blocks, tool/timeline UI,
  artifact surfaces, task panels, and input ergonomics.
- `src/lib/chat/*`: event normalization, message state, active snapshot, task
  and artifact client shapes.
- `src/components/knowledge/*`: knowledge and memory panel patterns.
- `src/components/dashboard/*`: useful layout ideas, but not the hosted
  dashboard shell as-is.

Pieces that should be rewritten or replaced:

- Any component that assumes Privy, Supabase, Stripe, hosted bot IDs, hosted
  billing state, production API routes, or Clawy Cloud tenancy.
- Any social-browser UI that assumes the managed credential broker. The OSS app
  should expose local/session adapter configuration instead.
- Any analytics, admin, invite, entitlement, or production growth surface.

## Target Architecture

```
apps/web
  -> AgentConnection               local/self-hosted HTTP + SSE client
  -> ProviderSettings              BYOK/local provider adapter config
  -> ChatWorkbench                 messages, tool calls, thinking, input
  -> RuntimeTimeline               hook events, checkpoints, evidence
  -> WorkspaceExplorer             files, generated outputs, artifacts
  -> TaskInspector                 background tasks and child-agent results
  -> MemoryInspector               Hipocampus + qmd browsing
  -> CronInspector                 scheduled workflows and delivery targets
  -> HarnessRuleEditor             Markdown rules stored in workspace
  -> SkillManager                  workspace skill list + reload
```

The app should communicate with Clawy Agent through documented local APIs rather
than importing runtime internals directly. That keeps the frontend replaceable
and makes third-party apps possible.

## Runtime API Needed

Before the app is useful, the runtime should expose or stabilize:

- Session list/create/resume endpoints.
- Message send endpoint with streaming SSE.
- Transcript replay endpoint.
- Workspace artifact/file list and download endpoints.
- Background task list/get/output endpoints.
- Cron list/create/update/delete endpoints.
- Memory browse/search endpoints.
- Harness rule list/read/write/delete endpoints.
- Skill list/reload endpoints.
- Provider config read/write endpoints that never leak secret values back to the
  browser.

Where an endpoint does not exist yet, the app plan should drive small,
documented runtime API additions instead of coupling the app to private hosted
routes.

## Milestones

### M0: Boundary And Marketing

- Keep this boundary document in the OSS repo.
- Link it from the README.
- Decide package layout: `apps/web` inside this repo versus a sibling
  `clawy-app` repo. Default recommendation: start inside this repo so runtime
  contracts and UI evolve together.
- Add a secrets and hosted-service audit checklist before importing private UI
  code.

### M1: Local Workbench

- Add a minimal web app shell.
- Connect to a local Clawy Agent HTTP/SSE endpoint.
- Render messages, tool calls, tool results, thinking blocks, and commit events.
- Send user messages and stream responses.
- No auth, billing, Supabase, or hosted Clawy dependency.

### M2: Provider And Workspace Setup

- Add first-run setup for workspace path, model provider, model name, and
  provider credentials.
- Store secrets locally or hand them to the runtime for storage.
- Never echo raw secrets into browser-readable config responses.
- Support at least Anthropic/OpenAI/Google-compatible provider settings if the
  runtime adapters are configured.

### M3: Runtime Visibility

- Add artifact/file panel.
- Add background task inspector.
- Add execution evidence and checkpoint timeline.
- Add delivery event visibility so users can see when generated files are only
  written locally versus actually delivered.

### M4: Automation And Rules

- Add cron list/create/update/delete UI.
- Add User Harness Rules editor backed by Markdown files.
- Add memory and qmd inspector.
- Add skills list/reload UI.

### M5: Self-Host Bundle

- Add Docker Compose for app + agent.
- Add sample `.env.example` with non-secret placeholders.
- Add production hardening notes for reverse proxy, TLS, auth, and provider key
  handling.
- Add hosted Clawy Cloud upgrade copy without making the OSS app dependent on
  cloud services.

## Security And Release Gates

Before publishing the frontend:

- Run a secrets scan on imported files.
- Search for production URLs, service role usage, private Supabase tables,
  Stripe identifiers, Privy app IDs, analytics keys, and internal admin routes.
- Confirm every browser-exposed API can be safely called by a self-hosted user.
- Ensure generated configs include placeholders, not real values.
- Keep hosted-only features behind explicit adapter interfaces.
- Document that self-hosting operators are responsible for their own provider
  keys, reverse proxy, TLS, storage, and access control.

## README Narrative

The README should describe the current repo as the runtime first, then point to
the app roadmap:

> Clawy Agent is the open-source runtime. The next layer is Clawy Agent App: a
> self-hostable workbench for running your own Codex-like personal agent app with
> your own provider and workspace.

That keeps the public promise coherent: the runtime already exists, the app is a
planned open-source surface, and hosted Clawy Cloud remains the managed version.
