# Open-Source Magi App Plan

## Goal

Open-source a self-hostable frontend for Magi without publishing the
hosted Magi Cloud control plane.

The target product is a Codex-like personal agent app that anyone can run with
their own LLM provider, workspace, memory, schedules, tools, and harness rules.
The hosted Magi product can then compete on managed uptime, provisioning,
credential handling, social/browser infrastructure, billing, and support rather
than on hiding client-side UI code.

## Positioning

Magi is the open-source runtime. Magi App should become the
open-source workbench for that runtime.

Marketing frame:

- Build your own personal agent app.
- Bring your own LLM provider.
- Run it locally, self-host it, or upgrade to managed Magi Cloud.
- Inspect the work: transcript, tools, tasks, files, memory, schedules, and
  verification evidence are visible runtime state.

This should not be positioned as a plain coding-agent clone. OpenCode, Codex,
Claude Code, and similar tools already own the terminal coding surface. Magi's
angle is broader: always-on personal agents with durable memory, scheduled
workflows, multi-channel delivery, background agents, generated files, and
runtime discipline gates.

## Open-Source Scope

The open-source app should include:

- Local-first chat UI for a running Magi instance. Initial shell: done.
- Runtime connection setup for local HTTP/SSE or a self-hosted endpoint. Initial
  server-token flow: done.
- Installable desktop PWA shell for the self-hosted app. Initial manifest,
  service worker, and install prompt: done.
- Buildable native desktop shell in `apps/desktop` using Tauri. Initial local
  runtime wrapper and runtime URL launcher: done.
- Provider configuration UI for API-key based providers and local provider
  adapters. Initial sanitized config editor: done.
- Session transcript viewer with tool calls, tool results, thinking blocks,
  background task events, delivery events, and commit checkpoints.
- Workspace file and artifact panels. Initial artifact index: done.
- Task board and child-agent task inspector. Initial task index: done.
- Memory inspector for Hipocampus root/daily/weekly/monthly/qmd state.
- Cron and scheduled workflow inspector. Initial cron index: done.
- User Harness Rules editor that writes Markdown rule files into the workspace.
  Initial Markdown CRUD editor: done.
- Skills viewer/reload control for workspace `skills/`. Initial skill state:
  done; reload control remains future work.
- Docker Compose starter that runs the app and agent together. Done.
- Clear upgrade path to Magi Cloud for managed hosting.

The app can reuse concepts and UI patterns from the hosted frontend, but should
be packaged as a separate self-hostable app surface with no default dependency
on Magi production services.

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
- Hosted desktop download page, binary signing, auto-update channel,
  entitlement checks, and managed desktop telemetry.
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
  billing state, production API routes, or Magi Cloud tenancy.
- Any social-browser UI that assumes the managed credential broker. The OSS app
  should expose local/session adapter configuration instead.
- Any analytics, admin, invite, entitlement, or production growth surface.

## Target Architecture

```
apps/web
  -> AgentConnection               local/self-hosted HTTP + SSE client
  -> DesktopShell                  installable PWA + buildable Tauri wrapper
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

The app should communicate with Magi through documented local APIs rather
than importing runtime internals directly. That keeps the frontend replaceable
and makes third-party apps possible.

## Runtime API Needed

The first read-only runtime API is now exposed under `GET /v1/app/*` and is
bearer-token gated by the server token:

| Endpoint | Status | Purpose |
| --- | --- | --- |
| `/v1/app/runtime` | shipped | Aggregate sessions, tasks, crons, artifacts, tools, and skills snapshot. |
| `/v1/app/sessions` | shipped | Live session metadata, permission posture, and budget counters. |
| `/v1/app/transcript?sessionKey=...` | shipped | Bounded committed transcript replay. |
| `/v1/app/tasks` | shipped | Background child-agent task list. |
| `/v1/app/crons` | shipped | Scheduled workflow list with internal cron visibility for operators. |
| `/v1/app/artifacts` | shipped | Generated artifact index. |
| `/v1/app/skills` | shipped | Skill load state, issues, and runtime skill hooks. |
| `/v1/app/config` | shipped | Sanitized config read/write using environment variable references for secrets. |
| `/v1/app/harness-rules` | shipped | Markdown harness rule list/read/write/delete in the workspace. |

Remaining API work:

- Session create/resume controls beyond the chat-completions session-key header.
- Workspace file list and download endpoints.
- Background task get/output/stop controls for the app.
- Cron create/update/delete controls for the app.
- Memory browse/search endpoints for Hipocampus root/daily/weekly/monthly/qmd state.
- Skill reload endpoint exposure in the app.
- Runtime config reload/restart control after provider config writes.

Where an endpoint does not exist yet, the app plan should drive small,
documented runtime API additions instead of coupling the app to private hosted
routes.

## Milestones

Status on 2026-05-07: M0 is complete, M1 has a dependency-free shell at `/app`,
the first M3 read-only runtime inspector is wired through documented
`/v1/app/*` HTTP APIs, and the app has both an installable PWA desktop shell and
a buildable Tauri wrapper under `apps/desktop`. M2 has an initial sanitized
provider/local-model config editor. M4 has an initial Markdown Harness Rules
editor. M5 has a Docker Compose starter.

### M0: Boundary And Marketing

- Keep this boundary document in the OSS repo.
- Link it from the README.
- Decide package layout: `apps/web` inside this repo versus a sibling
  `magi-app` repo. Default recommendation: start inside this repo so runtime
  contracts and UI evolve together.
- Add a secrets and hosted-service audit checklist before importing private UI
  code.

### M1: Local Workbench

- Add a minimal web app shell. Done.
- Connect to a local Magi HTTP/SSE endpoint. Done.
- Send user messages and stream responses. Done.
- Show runtime event stream. Done.
- Install as a desktop PWA from supported browsers. Done.
- Build a native Tauri shell around the same local runtime. Done.
- No auth, billing, Supabase, or hosted Magi dependency. Done.
- Render richer first-class message parts, thinking blocks, and tool cards.

### M2: Provider And Workspace Setup

- Add first-run setup for workspace path, model provider, model name, and
  provider credentials. Initial config editor: done.
- Store secrets locally or hand them to the runtime for storage. Initial env-var
  reference model: done.
- Never echo raw secrets into browser-readable config responses. Done.
- Support at least Anthropic/OpenAI/Google-compatible provider settings if the
  runtime adapters are configured. Done.

### M3: Runtime Visibility

- Add artifact panel. Initial index: done.
- Add workspace file panel.
- Add background task inspector. Initial index: done.
- Add cron and skill inspector. Initial index: done.
- Add execution evidence and checkpoint timeline.
- Add delivery event visibility so users can see when generated files are only written locally versus actually delivered.

### M4: Automation And Rules

- Add cron list/create/update/delete UI.
- Add User Harness Rules editor backed by Markdown files. Initial editor: done.
- Add memory and qmd inspector.
- Add skills list/reload UI.

### M5: Self-Host Bundle

- Add Docker Compose for app + agent. Done.
- Add sample `.env.example` with non-secret placeholders. Done.
- Add production hardening notes for reverse proxy, TLS, auth, and provider key
  handling.
- Add hosted Magi Cloud upgrade copy without making the OSS app dependent on
  cloud services.

### M6: Native Desktop Packaging

- Keep native packaging in this monorepo while it is only a local wrapper.
  Initial Tauri shell: done.
- Wrap the same `/app` surface in a minimal Tauri/Electron/WebView shell. Done
  for Tauri.
- Keep provider keys in the local runtime process or OS credential storage, not
  in browser-readable frontend state.
- Add signed release, auto-update, and notarization only after the hosted-only
  infrastructure boundary is audited.
- Keep the PWA shell as the zero-dependency desktop path even after native
  packaging exists.

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

The README should describe the current repo as the runtime first, then the app:

> Magi is the open-source runtime. Magi App is the included
> self-hostable workbench for running your own Codex-like personal agent app with
> your own provider and workspace.

That keeps the public promise coherent: the runtime already exists, the app is
the open-source surface, and hosted Magi Cloud remains the managed version.
