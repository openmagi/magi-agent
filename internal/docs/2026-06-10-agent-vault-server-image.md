# Agent Vault Server + Image (hosted sidecar)

- **Date:** 2026-06-10
- **Repo:** openmagi/magi-agent (server + Dockerfile). Clawy B CA-volume adjustment = separate PR.
- **Decision (Kevin):** sidecar **self-generates CA on boot into a shared volume**; the runtime reads only the CA cert. CA private key NEVER in a K8s Secret.
- **Reuses:** `credentials_admin/local_vault.py` (encrypted store), `local_proxy.py` (mitmproxy injection addon + proxy), `local_proxy_decision.py`, `approvals_store.py`.

## 1. What this is
A standalone Agent Vault process that runs as the per-bot **sidecar container**. It:
1. **Self-generates a CA** on first boot into the shared CA dir (`AGENT_VAULT_CA_DIR`, default `/etc/agent-vault`, an emptyDir shared with the runtime container). Writes the CA **cert** to `<dir>/ca.pem` (world-readable cert; the runtime trusts it). The CA **private key** stays in the mitmproxy confdir, mode 0600, and never leaves the pod.
2. Runs the **credential-injection proxy** (reuse `start_local_proxy` / the addon) on `127.0.0.1:<AGENT_VAULT_LISTEN_PORT>` (default 8443). Same-pod netns → the runtime reaches it via localhost.
3. Runs an **admin API** (FastAPI) on `0.0.0.0:<AGENT_VAULT_ADMIN_PORT>` (default 8444), token-authed by `AGENT_VAULT_PROXY_AUTH`, for `register`/`revoke`/`resolve_approval` — this is where the hosted dashboard's secret lands (routing to it = follow-up). Reuses the same `local_vault` store + `approvals_store`.

## 2. Server entrypoint
- `magi_agent/credentials_admin/vault_server.py`: `run_vault_server()` reads env, ensures CA dir, starts the proxy, starts the admin API (uvicorn), wires shutdown.
- CLI: `magi-agent vault-serve` (mirror the `serve` routing in `main.py`).
- Admin API endpoints (token-authed, mirror the OSS dashboard contract so `vault-client`/`vaultAdminFetch` and `MAGI_VAULT_ADMIN_URL` slot in):
  - `POST /v1/vault/credentials` `{service,label,auth_scheme,secret,requires_approval,host}` → `{vault_ref}` (stores via `LocalVault`, metadata via store).
  - `POST /v1/vault/credentials/{vault_ref}/revoke`.
  - `GET /v1/vault/approvals` + `POST /v1/vault/approvals/{id}` (resolve).
  - `GET /v1/vault/status` → `{present, healthy}`.
- **Secret invariant unchanged:** secret only into `LocalVault`; never logged/returned/in metadata.

## 3. Config (env)
| env | default | meaning |
|---|---|---|
| `AGENT_VAULT_CA_DIR` | `/etc/agent-vault` | shared volume; `ca.pem` written here |
| `AGENT_VAULT_LISTEN_PORT` | 8443 | proxy (127.0.0.1) |
| `AGENT_VAULT_ADMIN_PORT` | 8444 | admin API (0.0.0.0, token-authed) |
| `AGENT_VAULT_PROXY_AUTH` | — | admin/proxy session token (required) |
| `AGENT_VAULT_STORE_DIR` | `MAGI_VAULT_DIR` or `/var/lib/agent-vault` | encrypted store + confdir |
| `VAULT_BOT_ID` | — | label only |

## 4. Dockerfile (`Dockerfile.agent-vault`)
- `FROM python:3.12-slim`, copy package, `pip install .[vault]` (mitmproxy only — lean, no browser/playwright). Non-root user. `ENTRYPOINT magi-agent vault-serve`. Writable store dir for the encrypted vault; readOnlyRootFilesystem-compatible (store + CA dir are mounted writable volumes).

## 5. Out of scope (follow-ups)
- **Clawy B CA-volume adjustment** (separate PR): replace the `AGENT_VAULT_CA_CERT` Secret mount with a shared emptyDir between sidecar+runtime; sidecar writes `ca.pem`, runtime reads it. Keep `AGENT_VAULT_PROXY_AUTH` secret. NetworkPolicy: allow chat-proxy → sidecar admin port.
- **Dashboard→sidecar admin routing** (Vercel → chat-proxy → bot pod admin API): so `vault-client.registerCredential` reaches the live sidecar. Until then hosted registration stays `pending`.
- **Image build/push** (hel-system-1 arm64 → GHCR) — operator/infra action.
- Startup ordering: runtime egress fails closed until the sidecar writes `ca.pem`; acceptable for canary; a readiness gate is a follow-up.

## 6. Tests
- Admin API: token required (401), register stores ciphertext + metadata (no secret in response/store), revoke, approvals resolve. Reuse the existing local_vault/store/approvals tests' invariants.
- CA bootstrap: `run_vault_server` writes `ca.pem` to the CA dir; CA key file mode 0600; cert is readable.
- Proxy binds 127.0.0.1; admin binds configured port. (Use `pytest.importorskip("mitmproxy")` for proxy-start tests.)
- No network needed for the admin/store tests.
