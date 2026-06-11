# Environment Variable Reference

provider key or `~/.magi/config.toml`.

This page lists the environment variables a local user or self-hosted operator
needs. Platform-specific deployment variables are intentionally outside this
public local reference.

## Local CLI — required: one provider key

The local `magi` CLI needs exactly ONE of the following to talk to a real model.
Set a provider API key in your environment, or point the CLI at a
`~/.magi/config.toml`. With none configured, `magi` still launches but uses a
model-free stub runner.

Provider keys (the CLI auto-detects the first one present, in this order):

- `ANTHROPIC_API_KEY` — selects the `anthropic` provider. Default model `claude-sonnet-4-6`.
- `OPENAI_API_KEY` — selects the `openai` provider. Default model `gpt-5.5`.
- `GEMINI_API_KEY` — selects the `gemini` provider. Default model `gemini-3.5-flash`.
- `GOOGLE_API_KEY` — alias accepted for the `gemini` provider (used when `GEMINI_API_KEY` is unset).
- `FIREWORKS_API_KEY` — selects the `fireworks` provider. Default model `accounts/fireworks/models/kimi-k2-instruct`.

> Default model ids drift as providers retire names; override with `MAGI_MODEL`
> or `[model].model`. The authoritative defaults live in `magi_agent/cli/providers.py`.

Provider / model selection:

- `MAGI_PROVIDER` — force a specific provider (`anthropic`, `openai`, `gemini`, `fireworks`) instead of auto-detecting.
- `MAGI_MODEL` — override the model id for the selected provider.

Config file alternative (instead of, or in addition to, env keys):

- `MAGI_CONFIG` — path to the TOML config file. Defaults to `~/.magi/config.toml`.
  The file may set `[model].provider`, `[model].model`, `[model].api_key`, and
  per-provider keys under `[providers.<name>].api_key`.

Useful local toggles:

- `MAGI_CLI_ENABLED` (default on) — set to `0`/`false`/`no`/`off` to disable the CLI (it then exits with code 2).
- `MAGI_FIRST_PARTY_TOOLS_ENABLED` (default on) — set to `0`/`false`/`no`/`off` to disable Magi's first-party local tools once a real model runner is configured.
- `MAGI_TOOL_CONCURRENCY_ENABLED` (default `0`) — set to `1` to allow concurrent tool execution within a turn.
- `MAGI_MAX_TOOL_CONCURRENCY` (default `8`) — maximum concurrent tool executions per turn.
- `MAGI_RUNTIME_PROFILE` — selects a runtime profile (`magi_agent/config/env.py`).
- `MAGI_MEMORY_WRITE_ENABLED` (default off) — gates the `MemoryWrite` tool; memory is read-only unless enabled.
- `MAGI_EDIT_FUZZY_MATCH_ENABLED` — enables fuzzy matching for the edit tool.
- `MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT` — enables edit-match evidence enforcement.
- `MAGI_CHILD_RUNNER_LIVE_ENABLED` — enables real model-backed `SpawnAgent` /
  workflow child execution. The base gate is safety-default-off, but the
  installed full runtime profile sets it to `1` unless explicitly overridden or
  a safe/eval profile is selected.
- `MAGI_CHILD_RUNNER_TOOLSET` — child tool profile. The installed full runtime
  profile defaults to `readonly`, forwarding non-mutating inspection tools.
  `none` keeps child turns text-only; `full` is reserved for explicitly trusted
  sandbox/permission deployments.
- `MAGI_ANSWER_POLICY` (`abstain` | `commit`, default `abstain`) — best-effort
  finalization policy consumed by
  `magi_agent/runtime/best_effort_answer.py::finalize_answer`. Under `commit`,
  a bounded run that ends with an empty/abstaining answer synthesizes one
  best-effort answer from the gathered evidence (labeled with an uncertainty
  note); the default `abstain` keeps honest "I don't know" behavior and is a
  byte-identical pass-through. Unset/empty/unknown values fall back to
  `abstain`. The GAIA benchmark harness opts into `commit`.

<!-- BEGIN GENERATED FLAGS (scripts/generate_env_reference.py) -->
## Feature flags (auto-generated)

Generated from the `FLAGS` registry in `magi_agent/config/flags.py` by `scripts/generate_env_reference.py`. Do not edit this section by hand; register the flag in the registry and regenerate.

- `MAGI_APPLY_PATCH_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Enable the apply-patch tool for multi-file edits (default-ON full profile).
- `MAGI_BROWSER_TOOL_ENABLED` (default off) — Expose the browser-use autonomous vision BrowserTask tool.
- `MAGI_CHANNEL_WORKFLOWS_ENABLED` (default off) — Enable bot-user dynamic channel workflows (classifier-driven).
- `MAGI_CLI_ENABLED` (default on) — Enable the magi CLI surface (headless NDJSON + Textual TUI); flat default-ON.
- `MAGI_CODE_ACTION_ENABLED` (default off) — Expose the persistent PythonExec code-execution tool.
- `MAGI_CODE_ACTION_MAX_OUTPUT_BYTES` (default `8192`) — Head+tail output cap per stream (bytes) for PythonExec results; clamped to 1024-65536.
- `MAGI_CODE_ACTION_TIMEOUT_MS` (default `30000`) — Per-call wall-clock timeout (ms) for the PythonExec tool; clamped to 1000-120000.
- `MAGI_CODING_REPAIR_LOOP_ENABLED` (default off) — Enable the iterative coding repair loop on failing edits.
- `MAGI_COMPUTE_VIA_CODE_ENABLED` (default off) — Append a general prompt directive to compute arithmetic, conversions, statistics, and checksums by running code instead of mental math.
- `MAGI_CONTEXT_COMPACTION_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Compact the working context when the token threshold is hit (default-ON full profile).
- `MAGI_CROSS_VERIFY_ENABLED` (default off) — Enable the cross-verification gate over spawned-agent results.
- `MAGI_DEEP_WEB_RESEARCH_ENABLED` (default off) — Enable the live deep web-research harness (search + fetch + verify).
- `MAGI_DEFERRED_TOOLS_ENABLED` (default off) — Enable deferred (lazily-loaded) tool schemas.
- `MAGI_DOCUMENT_AUTHORING_COVERAGE` (default off) — Block document turns on failed DocumentCoverage (vs audit-only).
- `MAGI_DOCUMENT_QA_ENABLED` (default off) — Expose the question-conditioned DocumentQA file-QA sidecar tool (requires MAGI_FILE_TOOLS_ENABLED); strict default-OFF in all profiles.
- `MAGI_DOCUMENT_QA_MODEL` (no default) — Model id override for the DocumentQA sidecar call (e.g. a cheap haiku-class model); unset uses the configured provider model.
- `MAGI_EDIT_FORMAT_ON_WRITE_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Run a formatter on files written by the coding harness (default-ON full profile).
- `MAGI_EDIT_FUZZY_MATCH_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Use the 9-stage fuzzy-match cascade for FileEdit (default-ON full profile).
- `MAGI_EDIT_RETRY_REFLECTION_ENABLED` (default off) — Reflect on failed edits before retrying (coding repair loop).
- `MAGI_EGRESS_GATE_ENABLED` (default off) — Run the evidence-grounded critic gate before chat egress.
- `MAGI_ERROR_RECOVERY_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Enable automatic error-recovery retries (default-ON full profile).
- `MAGI_EVIDENCE_COMPLETION_GATE_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Block turn completion when required evidence is missing (default-ON full profile).
- `MAGI_EVIDENCE_LEDGER_DIR` (no default) — Directory for opt-in durable per-session JSONL evidence ledgers; unset keeps the lean in-memory live view only.
- `MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Build per-turn EvidenceLedger objects (default-ON full profile).
- `MAGI_FACTS_REPLAN_ENABLED` (default off) — Inject a periodic in-context facts survey (given/learned/look-up/derive) + plan refresh into the live model loop every N working steps.
- `MAGI_FACTS_REPLAN_INTERVAL` (default `4`) — Working steps between facts surveys (>= 1; a non-positive value disables the control).
- `MAGI_FACTS_REPLAN_MAX_PER_TURN` (default `5`) — Hard cap on facts surveys injected per (session, turn) (>= 1; a non-positive value disables the control).
- `MAGI_FILE_DELIVERY_LIVE_ENABLED` (default off) — Enable the live file-delivery tool (vs receipt-only).
- `MAGI_FORMAT_ADHERENCE_ENABLED` (default off) — Append a general prompt self-check for exact requested units, scale, rounding precision, names, and answer format.
- `MAGI_GA_DELIVERABLE_GATE_ENABLED` (default off) — Enable the GA artifact-deliverable pre-final gate; strict default-OFF and inert unless explicitly set.
- `MAGI_GOAL_LOOP_ENABLED` (default off) — Enable the autonomous goal-loop scheduler.
- `MAGI_HEADTAIL_TRUNCATION_ENABLED` (default off) — Use head+tail (middle-elision) truncation for tool output caps instead of head-only, so document/page tails stay visible.
- `MAGI_LEARNING_ENABLED` (default off) — Master switch for the learned-skills / self-improvement loop.
- `MAGI_LEARNING_INJECTION_ENABLED` (default off) — Inject learned skills/refinements into the runtime prompt.
- `MAGI_LEARNING_LIVE_ENABLED` (default off) — Allow the learning loop to run with live model-backed proposers.
- `MAGI_LEARNING_REFLECTION_ENABLED` (default off) — Enable post-turn reflection that feeds the learning loop.
- `MAGI_LOOP_GUARD_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Enable the repetition/loop guard brake (default-ON full profile).
- `MAGI_LSP_DIAGNOSTICS_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Surface LSP diagnostics to the coding harness (default-ON full profile).
- `MAGI_MEMORY_COMPACTION_ENABLED` (default off) — Enable the 5-level compaction tree builder for stored memory.
- `MAGI_MEMORY_ENABLED` (default off) — Master switch for the agent memory subsystem (3-tier + compaction).
- `MAGI_MEMORY_MODE_ROUTING_ENABLED` (default off) — Honour the per-channel memory mode header (normal/read-only/incognito).
- `MAGI_MEMORY_PROJECTION_ENABLED` (default off) — Project a lean memory view into the serve prompt block.
- `MAGI_MEMORY_QMD_LIVE_ENABLED` (default off) — Use the live qmd search backend for memory recall.
- `MAGI_MEMORY_RECALL_ENABLED` (default off) — Enable memory recall/injection into the working context.
- `MAGI_MEMORY_WRITE_ENABLED` (default off) — Allow the memory subsystem to persist writes (vs read-only recall).
- `MAGI_MULTI_FILE_JOIN_ENABLED` (default off) — Append multi-file cross-reference guidance: enumerate archives, read structured files fully, and run joins/dedup programmatically.
- `MAGI_OBSERVABILITY_ENABLED` (default off) — Enable the hook-tap observability module (bot-activity visibility).
- `MAGI_OUTPUT_CONTINUATION_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Enable automatic continuation of truncated model output (default-ON full profile).
- `MAGI_RESEARCH_FACT_GUIDANCE_ENABLED` (default off) — Enable research_fact cross-check guidance: consolidated brief header/footer plus the <web_research> system-prompt block (requires BRAVE_API_KEY + FIRECRAWL_API_KEY).
- `MAGI_RESEARCH_GOVERNANCE_MODE` (default `off`) — Research governance mode. `off` is inert; `audit` records source/citation mismatches without blocking.
- `MAGI_RIPGREP_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Use ripgrep for fast in-repo search when available (default-ON full profile).
- `MAGI_RUNTIME_PROFILE` (no default) — Runtime profile selector (safe/off/minimal/conservative/eval). Safe profiles disable default-ON resilience seams.
- `MAGI_SELF_INTROSPECTION_ENABLED` (default-ON (full runtime profile; OFF under safe/eval)) — Advertise the InspectSelfEvidence tool (default-ON full profile).
- `MAGI_STEP_DECOMPOSITION_ENABLED` (default off) — Inject a light first-pass guidance asking the agent to enumerate dependent sub-steps up front and confirm each before proceeding (prompt-only nudge; reuses existing planning seams).
- `MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED` (default off) — Live-SWE-style tool-synthesis: per-step reflection nudge + 'create your own tools' recipe block (frontier-tier models only).
- `MAGI_VISION_MODEL` (no default) — Vision-sidecar model override for image_understand (bare model id, same semantics as MAGI_MODEL); unset keeps the main provider/model.
- `MAGI_VISION_PROVIDER` (no default) — Optional provider for MAGI_VISION_MODEL (anthropic|openai|gemini|fireworks); unset inherits the main provider's credentials.

<!-- END GENERATED FLAGS -->

## Egress proxy (Agent Vault)

Optional, **default-OFF** seam that routes Bash-tool and `web_fetch`-tool egress
through an external forward proxy (trusting its CA). It never touches
model/provider egress. Disabled = byte-identical runtime; enabled-but-misconfigured
refuses to start (fail-closed).

- `MAGI_EGRESS_PROXY_ENABLED` (default off) — set to `1`/`true`/`yes`/`on` to route
  tool egress through the proxy. When off, all four vars below are ignored.
- `MAGI_EGRESS_PROXY_URL` — HTTP(S) proxy origin (e.g. `http://127.0.0.1:8888`).
  Required when enabled; must not embed credentials, path, query, or fragment.
- `MAGI_EGRESS_PROXY_AUTH` — proxy credentials (`user:token`), carried separately
  from the URL. Optional. Applied only by clients that can send proxy auth
  outside subprocess env; Bash receives auth-free proxy URLs.
- `MAGI_EGRESS_PROXY_CA_CERT_PATH` — path to the proxy CA cert to trust. Required
  and must be a readable file when enabled.

## Local server

- `CORE_AGENT_PORT` (default `8080`) — HTTP port used by `magi-agent serve`.

## Build metadata

These are optional and usually set by release or container builds.

- `CORE_AGENT_VERSION` — Semantic version string.
- `CORE_AGENT_BUILD_SHA` — Git commit SHA.
- `IMAGE_REPO` — Container image repository.
- `IMAGE_TAG` — Container image tag.
- `IMAGE_DIGEST` — Container image digest.

## Local memory and ToolHost options

- `MEMORY_WORKSPACE_ROOT` — Workspace root path for local memory adapters.
- `MAGI_FIRST_PARTY_TOOLS_ENABLED` — Disable first-party tools when set to
  `0`/`false`/`no`/`off`.

## Direct web tools (search / fetch / research)

The fast direct web tools (`web_search`, `web_fetch`, `research_fact`)
auto-activate on key presence and register no tools otherwise:

- `BRAVE_API_KEY` — enables Brave web search (the default search provider).
- `FIRECRAWL_API_KEY` — enables Firecrawl page fetch. Required for the web
  toolset to register at all.
- `MAGI_WEB_SEARCH_PROVIDER` (default unset → `brave`) — set to `serpapi`
  (together with `SERPAPI_API_KEY`) to serve `web_search`/`research_fact` from
  a real Google SERP via SerpAPI instead of Brave; responses are normalized to
  the same shape, and Google answer boxes appear as a `[answer box]` first
  result. Any other value, or a missing `SERPAPI_API_KEY`, falls back to
  Brave. Unset is byte-identical to before this flag existed.
- `SERPAPI_API_KEY` — SerpAPI key; only used when
  `MAGI_WEB_SEARCH_PROVIDER=serpapi`.
- `MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED` (default off) — set to
  `1`/`true`/`yes`/`on` to append a one-line
  `[receipt] provider=<name> latency_ms=<int>` footer to
  `web_search`/`web_fetch` output and per-source `(latency_ms=<int>)`
  annotations to `research_fact` briefs, so the agent can budget around slow
  providers/URLs.

## Authority and rollout flags

The authority flags below correspond to PythonRuntimeAuthorityConfig fields and
are read by packaged server deployments only (the local CLI does not read them).
All must be `false` or omitted. The `Literal[False]` type annotation means the
runtime structurally rejects attempts to set them to true, and the env parser
raises if any is set truthy. The env var names carry the `CORE_AGENT_PYTHON_`
prefix (`magi_agent/config/env.py`):

- `CORE_AGENT_PYTHON_TRANSCRIPT_WRITE` — Must be false. Transcript write authority.
- `CORE_AGENT_PYTHON_SSE_WRITE` — Must be false. SSE write authority.
- `CORE_AGENT_PYTHON_CHANNEL_DELIVERY` — Must be false. Channel delivery authority.
- `CORE_AGENT_PYTHON_DB_WRITE` — Must be false. Database write authority.
- `CORE_AGENT_PYTHON_WORKSPACE_MUTATION` — Must be false. Workspace mutation authority.
- `CORE_AGENT_PYTHON_CHILD_EXECUTION` — Must be false. Child agent execution authority.
- `CORE_AGENT_PYTHON_MISSION_RUNTIME` — Must be false. Mission runtime authority.
- `CORE_AGENT_PYTHON_EVIDENCE_BLOCK_MODE` — Must be false. Evidence blocking mode.

- [Security](/docs/security)
- [Config reference](/docs/config-reference)
