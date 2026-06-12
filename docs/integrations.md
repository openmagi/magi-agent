# Integrations

> **Note — default-off.** External side-effect surfaces (chat channels, Composio) ship gated; they require explicit scope, credentials, and approval, and most run in shadow / record-intent mode today.

Integrations are side-effect surfaces controlled by ToolHost and policy.

External systems such as Slack, documents, storage, browser sessions, and chat channels should require approvals, receipts, idempotency receipts, and governed projection.

## External side-effect boundary

An integration call is not direct model authority. The model proposes an action, ToolHost computes the action digest, policy checks approvals and idempotency receipts, then the runtime records delivery or mutation receipts.

Channel delivery is also a projection boundary. User-visible messages should be derived from governed output projection, not raw draft text or hidden tool output.

- Slack drafts require validation before they are sent.
- Document and artifact delivery require public-safe projection.
- Browser and external API actions require least privilege and auditability.
- Repeated side effects require idempotency receipts.

## Concrete integration surfaces

- **Chat channels (Telegram, Discord).** Implemented under
  `magi_agent/channels/` as adapter → boundary → dispatcher. They validate,
  redact secrets / private paths, and record send intents and receipts. Live
  send/receive is **default-off / shadow** today — the adapters produce
  local-fake receipts, not real delivery. Full guide:
  [channels.md](channels.md).

- **Composio external tools — ON HOLD.** An optional external-integration
  surface lives under `magi_agent/composio/` (config, health, and redaction
  modules only; no live end-to-end connection ships today). The integration is
  **deliberately on hold**: measured GAIA usage showed the per-call MCP
  connect/teardown latency made multi-hop web tasks time out, and the direct
  web tools (Brave search + Firecrawl fetch + `research_fact`, which
  auto-activate when `BRAVE_API_KEY`/`FIRECRAWL_API_KEY` are set) replaced it
  as the supported web path. The config/health/redaction modules remain so an
  operator-driven revival has a seam, but no new Composio capability will be
  added unless a concrete use case the direct tools cannot serve appears.
  Treat it as a gated, dormant surface — enabling it does not bypass the
  side-effect boundary above.

- **Apify Actors.** Magi Agent can discover and run Apify Actors for
  platform-specific structured scraping (Instagram, TikTok, Google Maps, Amazon,
  LinkedIn, …) when the general web-fetch tools hit anti-bot walls. Implemented
  in `magi_agent/plugins/native/apify.py` and **enabled by default**.

  ### Tools

  - `apify_search_actors(query)` — Search the public Apify store by keyword
    (e.g. `"instagram scraper"`, `"google maps"`). **Free — no account or token
    needed.** Returns up to 10 Actors, each with `actor_id`, title, description,
    categories, rating, and total run count.
  - `apify_run_actor(actor_id, run_input)` — Run an Actor and return its
    structured dataset items in one call. **Paid — billed to your own Apify
    account.** Requires `APIFY_TOKEN`. Every run is hard-capped at
    `APIFY_MAX_USD_PER_RUN` (default `$1.00`) and 300 seconds.

  ### Setup (paid execution)

  1. Create an account at <https://apify.com> (free tier includes trial credit).
  2. Copy your API token from the Apify console.
  3. Set the environment variables before starting Magi Agent:

     ```bash
     export APIFY_TOKEN="apify_api_..."
     # optional: hard cost cap per run (default $1.00)
     export APIFY_MAX_USD_PER_RUN="0.50"
     ```

  Discovery (`apify_search_actors`) works without a token. Running an Actor
  (`apify_run_actor`) requires `APIFY_TOKEN` and is billed to your Apify
  account; every run is capped at `APIFY_MAX_USD_PER_RUN`.
