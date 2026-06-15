# Agent Vault — OSS Local Vault Phase 2: Credential-Injecting Forward Proxy

- **Date:** 2026-06-10
- **Repo:** openmagi/magi-agent
- **Depends on:** Phase 1 `LocalVault` (#571, merged) + A egress seam (`MAGI_EGRESS_PROXY_*`).
- **Decision (Kevin):** mitmproxy library as the TLS-MITM engine, shipped as an **optional extra** `magi-agent[vault]`. Native store stays; only the proxy engine is mitmproxy.

## 1. Goal

Make the bot actually **use** a registered credential without ever seeing it. A local mitmproxy-based forward proxy:
- terminates TLS with a local CA the runtime trusts,
- matches each outbound request's host → a registered credential,
- injects the auth header (secret fetched via `LocalVault.get_secret`), stripping any agent-supplied auth,
- enforces D approval: a `requires_approval` credential is **blocked + an approval request enqueued** until the user approves.

The A egress seam routes the Bash/web_fetch tool egress through this proxy.

## 2. Scope / gating

- Optional dep: `[project.optional-dependencies] vault = ["mitmproxy>=10"]`. Core import path must NOT hard-import mitmproxy (lazy import inside the proxy module). If the extra isn't installed and the proxy is requested, log a clear "install magi-agent[vault]" message and stay disabled.
- **Local serve only** (same gating as Phase 1): a `MAGI_LOCAL_VAULT_PROXY_ENABLED` flag, defaulted ON in `LOCAL_FULL_RUNTIME_ENV_DEFAULTS` under the local sentinel; never enabled on hosted; never when `MAGI_VAULT_ADMIN_URL` is set.
- Binds to **127.0.0.1 only**.

## 3. Components

### 3.1 Host mapping
Credentials need a target host. Add an additive, non-secret `host` field to the metadata (store `public_metadata` + `add_credential`, POST `_validate_body` optional). Plus a built-in `SERVICE_HOST_MAP` (slack→`slack.com`/`api.slack.com`, notion→`api.notion.com`, stripe→`api.stripe.com`, google→`www.googleapis.com`, …). Resolution: explicit `host` wins, else map by `service`, else no match (request passes through untouched).

### 3.2 Pure decision core — `local_proxy_decision.py` (no mitmproxy import; fully unit-testable)
```python
def decide_injection(*, host, existing_auth_present, credentials, now) -> InjectionDecision
# returns one of: PASS_THROUGH (no matching active cred),
#                 BLOCK_PENDING_APPROVAL (matched but requires_approval & not approved),
#                 INJECT(vault_ref, auth_scheme, header_name)
```
- Match active (`status=='active'`) credential by resolved host.
- `requires_approval` + no current approval (checked via approvals_store) → BLOCK.
- Else INJECT. Header per `auth_scheme`: bearer→`Authorization: Bearer <s>`; basic→`Authorization: Basic <s>`; api_key→`<header_name or "Authorization">: <s>`. The decision returns the *plan*; the secret is fetched separately so plaintext never enters the decision object.

### 3.3 mitmproxy addon — `local_proxy.py` (thin; lazy-imports mitmproxy)
- `request(flow)`: call `decide_injection(...)`; on INJECT fetch `LocalVault.get_secret(vault_ref)`, set the header on `flow.request`, remove any pre-existing auth header; on BLOCK set `flow.response` = 403 JSON `{"error":"credential_pending_approval","credential_id":...}` and enqueue an approval via `approvals_store`; on PASS_THROUGH do nothing.
- **Never** log the secret or the request body. Bind localhost. confdir = `<vault_dir>/mitmproxy` so the CA + its private key live in our 0700 dir; ensure CA key file perms 0600.
- `start_local_proxy() -> ProxyHandle{port, ca_cert_path}`: run mitmproxy `DumpMaster` (headless) on 127.0.0.1:<port> in a background asyncio task/thread; return CA path (`mitmproxy-ca-cert.pem` in confdir).

### 3.4 Lifecycle wiring (local serve)
When local vault + proxy enabled: start the proxy, then set `MAGI_EGRESS_PROXY_ENABLED=1`, `MAGI_EGRESS_PROXY_URL=http://127.0.0.1:<port>`, `MAGI_EGRESS_PROXY_CA_CERT_PATH=<ca>` so the A seam routes tool egress through it. Clean shutdown on serve stop.

## 4. Tests (no live proxy / no mitmproxy needed)
- `decide_injection`: pass-through (no cred), inject (bearer/basic/api_key header plan), block-pending-approval (requires_approval, no approval) → enqueues, approved → injects. Host resolution (explicit host vs service map vs none).
- secret never in the decision object / never logged (the addon's injection path tested via a fake flow object + monkeypatched get_secret; assert header set, secret absent from logs).
- gating: proxy disabled on hosted / when MAGI_VAULT_ADMIN_URL set / when extra missing (lazy import guard).
- store `host` field round-trips; absent → None.

## 5. Out of scope
- Hosted (sub-project B sidecar) — unchanged.
- Dashboard host-field UI (API accepts `host`; dashboard form field is a small follow-up, bundle rebuild deferred).
- Non-header auth schemes (query-param/cookie creds) — header-based only for v1.

## 6. Success criteria
- `magi-agent[vault]` installed + local serve: a Bash `curl https://api.notion.com/...` from the bot is auto-authenticated by the proxy; the bot never sees the token.
- A `requires_approval` cred blocks with a pending-approval error until approved in the dashboard, then succeeds.
- Core (without the extra) imports and runs unchanged; proxy stays disabled with a clear hint.
