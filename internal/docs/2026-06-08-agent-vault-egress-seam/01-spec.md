# Spec — Agent Vault Egress Seam (Sub-project A)

- **Status:** Draft for review
- **Date:** 2026-06-08
- **Owner:** Kevin
- **Scope:** OSS `magi-agent` runtime only. Default-OFF, core-unchanged when disabled.
- **Part of:** "Credential-broker for bots" full vertical. This is sub-project **A** (foundation). B (vault deploy/operation), C (credential registration UI + persistence), D (approval/policy wiring) are separate specs that depend on A.

## 1. Problem

A bot's tools (`Bash` running `curl`/CLI/scripts, the `web_fetch` tool) need to authenticate to third-party services (Slack, Notion, Stripe, Google, …) on the user's behalf. Today the only ways to do that are to put raw secrets into the bot's environment or prompt context. Any secret reachable by the agent is a leak risk: the agent can be prompt-injected into exfiltrating it, and anything in the LLM context window must be treated as compromised.

We want bots to *use* third-party credentials without ever *seeing* them.

## 2. Solution summary

Route the bot's **tool egress** through an externally-operated **Agent Vault forward proxy** (Infisical Agent Vault, an open-source local HTTPS forward proxy). The proxy terminates TLS with a locally-trusted CA, strips any credential the agent included, injects the correct credential from its own encrypted store, and re-establishes a verified TLS connection to the real upstream. The agent never sees the secret.

This spec covers **only the client-side seam in `magi-agent`**: when enabled, the runtime routes tool egress through the proxy and trusts the proxy's CA. We do **not** fork, embed, or operate Agent Vault here — the operator runs it; we trust it. Provisioning the proxy and feeding it credentials is sub-projects B/C.

## 3. Goals / Non-goals

### Goals
- G1. When enabled, the **Bash tool** subprocess and the **`web_fetch` tool** route outbound traffic through the configured proxy and trust its CA.
- G2. **Default-OFF**: when the feature flag is unset/false, the runtime is byte-for-byte unchanged. No new env vars required, no behavior change.
- G3. **Scoped injection**: only *tool* egress is proxied. Model/provider API calls (Anthropic/OpenAI/Gemini via LiteLlm/google-genai) must **NOT** go through this credential proxy.
- G4. **Fail-closed**: when enabled but misconfigured (missing/invalid proxy URL or CA path), the runtime refuses to start. When enabled and the proxy is unreachable at request time, the tool call fails — it must never silently fall back to direct egress.
- G5. Emit minimal egress evidence (one decision record per proxied call class) reusing the existing evidence/`observed_egress` substrate.

### Non-goals
- N1. Operating/deploying Agent Vault (sub-project B).
- N2. Storing or registering user credentials, service→auth-scheme mapping, UI (sub-project C).
- N3. Human-in-the-loop approval, mobile approval, SmartApprove wiring (sub-project D). The proxy's own approval controls are out of scope; we only route to it.
- N4. Proxying model/provider egress, or routing the research `search`/`scrape` platform-endpoint providers (those carry platform tokens, not user credentials — may be revisited in a later phase).
- N5. Enforcing OS-level egress lockdown (firewall/NetworkPolicy so the proxy is the *only* reachable route). That is a hosted-deploy concern (sub-project B). In OSS, fail-closed is enforced at the injection points we control, not at the kernel.

## 4. Configuration

Mirror the existing `MAGI_OBSERVABILITY_ENABLED` tri-state convention.

| Env var | Required when enabled | Meaning |
|---|---|---|
| `MAGI_EGRESS_PROXY_ENABLED` | — | Master switch (tri-state: on/off/unset→off). |
| `MAGI_EGRESS_PROXY_URL` | **yes** | Forward proxy origin, e.g. `http://127.0.0.1:8888`. HTTP(S) origin only (no path/query/fragment). |
| `MAGI_EGRESS_PROXY_AUTH` | no | Proxy session credential (Agent Vault scopes the agent session via proxy auth). Supplied separately from the URL so it is never logged as part of the URL. Form: `user:token` or an opaque token applied as `Proxy-Authorization`. |
| `MAGI_EGRESS_PROXY_CA_CERT_PATH` | **yes** | Filesystem path to the proxy's CA cert (PEM). Must exist and be readable at startup. |

Notes:
- Unlike `gate1a` model-egress correlation (which forbids credentials in the proxy URL and is digest-only), this seam **must support proxy auth** because that is how Agent Vault scopes a session to a specific agent. Auth is carried out-of-band (`MAGI_EGRESS_PROXY_AUTH` → `Proxy-Authorization`), not embedded in `MAGI_EGRESS_PROXY_URL`, so the URL stays loggable.
- When disabled, none of these vars are read and the runtime is unchanged.

## 5. Functional requirements

### FR1 — Bash tool egress (primary)
When enabled, the `Bash` tool subprocess environment (currently `{"PATH": …}` in `gate5b_full_toolhost.py`) gains:
- `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` = configured proxy URL (with auth applied per-scheme as needed for CLI tools that read creds from the URL).
- CA trust vars so common runtimes verify the proxy CA: `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`, `GIT_SSL_CAINFO` = configured CA path.
- `NO_PROXY` left unset (the proxy decides what to allow); see §6 fail-closed.

When disabled, the env is exactly `{"PATH": …}` as today.

### FR2 — `web_fetch` tool egress
When enabled, the in-process `httpx.Client` built in `live_fetch_provider.py` is constructed with `proxy=<url>` (+ `Proxy-Authorization` header when auth set) and `verify=<ca_path>`. When disabled, the client is constructed exactly as today (`trust_env` default unaffected, no proxy).

### FR3 — Model/provider egress isolation
Provider/model HTTP paths (LiteLlm, google-genai, `gate1a` model-egress) must be unaffected by FR1/FR2. The seam performs **explicit, scoped** injection at the two tool egress points only; it must not set process-wide proxy env (`os.environ`) that model clients with `trust_env=True` could inherit.

### FR4 — Startup validation (fail-closed)
At runtime construction, if `MAGI_EGRESS_PROXY_ENABLED` is true:
- `MAGI_EGRESS_PROXY_URL` must be a valid HTTP(S) proxy origin → else raise and refuse to start.
- `MAGI_EGRESS_PROXY_CA_CERT_PATH` must point to an existing, readable file → else raise.
When false, validation is skipped entirely.

### FR5 — Evidence
Each proxied egress class (bash-subprocess, web_fetch) emits one `observed_egress`-style decision record (source tag reusing `gate5b_egress_proxy`), digest-only / redacted per existing sensitivity rules. Best-effort; evidence emission failure must not break the tool call.

## 6. Failure & edge behavior

- **Enabled + misconfigured** → refuse to start (FR4).
- **Enabled + proxy unreachable at call time** → the `curl`/httpx call fails with a connection error surfaced to the agent as a normal tool error. There is **no** direct-egress fallback. This is the fail-closed guarantee for the paths we control.
- **Disabled** → no-op, unchanged runtime.
- **CA path readable at startup but deleted later** → tool calls fail closed (TLS verification error). Acceptable.
- **Tool that ignores proxy env** (e.g. a statically-linked binary that bypasses `HTTPS_PROXY`): out of OSS scope to prevent; the hosted deploy (B) closes this with kernel-level egress lockdown. Documented limitation.

## 7. Success criteria

- With the flag off: full existing test suite passes unchanged; no new env reads; diff is inert.
- With the flag on and a stub proxy: a `Bash` `curl https://example.test` call carries the proxy + CA env; `web_fetch` uses the proxy transport; model calls demonstrably do **not**.
- Misconfigured-enabled raises at startup.
- All new tests run without network access (stub env / fake transport).

## 8. Open questions (deferred, not blocking A)

- OQ1. Should the research `search`/`scrape` platform-endpoint providers also route through the proxy? Deferred to a later phase (they carry platform tokens, not user creds).
- OQ2. Per-tool vs per-bot proxy session scoping — resolved in B/D where the vault issues per-bot session tokens.
- OQ3. Whether to also export `NODE_EXTRA_CA_CERTS`-style vars for less common runtimes (deno, bun, go). Start with the common set in FR1; extend on demand.
