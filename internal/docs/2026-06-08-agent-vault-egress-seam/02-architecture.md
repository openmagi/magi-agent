# Architecture — Agent Vault Egress Seam (Sub-project A)

- **Date:** 2026-06-08
- **Companion:** `01-spec.md` (requirements), `03-implementation-plan.md` (build steps)
- **Substrate verified against:** `origin/main` @ c2b7603

## 1. Where this plugs into reality

Tool egress in `magi-agent` converges at two concrete points. Both were confirmed by reading the code, not inferred.

| Egress class | Code site | Today |
|---|---|---|
| **Bash tool subprocess** | `magi_agent/gates/gate5b_full_toolhost.py:866-879` | `subprocess.run(command, shell=True, env={"PATH": os.environ.get("PATH", …)}, …)` — env intentionally minimal (PATH only). |
| **`web_fetch` tool** | `magi_agent/web_acquisition/live_fetch_provider.py:265-267` | `httpx.Client(timeout=…, follow_redirects=False)` — no proxy/verify args. |

Tool dispatch flows model → ADK `Runner` → `tools/dispatcher.py:dispatch()` → `Gate5BFullToolHost`. The `web_fetch` tool (`plugins/native/web.py`) resolves to `LiveFetchProvider`. There is **no remote toolhost or container sandbox** — execution is local/in-process, so injecting env and httpx args in plain Python fully controls egress.

### Why NOT process-wide env
Setting `os.environ["HTTPS_PROXY"]` globally is the tempting one-liner. We reject it: model/provider clients (LiteLlm, google-genai) use `httpx`/`aiohttp` with `trust_env=True` and would silently start routing provider API calls (carrying provider keys) through the credential proxy. That is wrong and would also fight `gate1a` which deliberately sets `trust_env=False` for model-egress correlation. The seam therefore does **explicit, scoped** injection at the two tool sites only (Spec FR3).

## 2. Module layout

New package `magi_agent/egress_proxy/`, mirroring `magi_agent/observability/` (config + integration split, default-OFF, `register_*` no-op idiom).

```
magi_agent/egress_proxy/
  __init__.py
  config.py        # EgressProxyConfig.from_env() — tri-state parse + fail-closed validation
  injection.py     # pure builders: subprocess env dict + httpx client kwargs
  evidence.py      # thin adapter onto evidence/observed_egress for proxied-call records
```

Touched existing files (each guarded so disabled = unchanged):
- `gates/gate5b_full_toolhost.py` — merge proxy env into the Bash `env` dict.
- `web_acquisition/live_fetch_provider.py` — pass proxy/verify kwargs into `httpx.Client`.
- `app.py` (or runtime construction) — 1-line `validate_egress_proxy_config()` call for startup fail-closed (FR4).

### 2.1 `config.py`
```python
@dataclass(frozen=True)
class EgressProxyConfig:
    enabled: bool
    proxy_url: str | None          # validated HTTP(S) origin, no creds, no path
    proxy_auth: str | None         # opaque "user:token" → Proxy-Authorization
    ca_cert_path: str | None       # existing readable PEM

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "EgressProxyConfig":
        ...  # tri-state via the existing _parse_bool_env / _truthy helper

    def validate(self) -> None:
        # FR4: when enabled, raise on missing/invalid url or unreadable ca path
```
- Reuses the URL-shape validation philosophy of `gate1a._validate_proxy_url` but **permits** proxy auth (carried separately, so the URL itself still has no creds). This is a distinct validator living in `config.py` — we do not weaken gate1a's stricter, digest-only model-egress validator.

### 2.2 `injection.py` (pure, no I/O)
```python
def subprocess_env_overlay(cfg: EgressProxyConfig) -> dict[str, str]:
    """Extra env keys to merge into a tool subprocess. Empty dict if disabled."""
    # HTTPS_PROXY/HTTP_PROXY/ALL_PROXY (auth-free; Bash can print env)
    # SSL_CERT_FILE/CURL_CA_BUNDLE/REQUESTS_CA_BUNDLE/NODE_EXTRA_CA_CERTS/GIT_SSL_CAINFO

def httpx_client_kwargs(cfg: EgressProxyConfig) -> dict[str, object]:
    """kwargs to splat into httpx.Client(...). Empty dict if disabled."""
    # {"proxy": httpx.Proxy(url, headers={"Proxy-Authorization": ...}), "verify": ca_path}
```
Pure functions → trivially unit-testable with no network and no env mutation.

## 3. Data flow

### 3.1 Bash tool (enabled)
```
model emits Bash{command:"curl https://api.slack.com/..."}
  → dispatcher.dispatch("Bash", …)
  → Gate5BFullToolHost: build env
        base = {"PATH": …}
        overlay = injection.subprocess_env_overlay(cfg)   # HTTPS_PROXY, CA vars
        env = base | overlay
  → subprocess.run(command, env=env)
        curl reads HTTPS_PROXY → CONNECT to Agent Vault without env-exposed auth
        Agent Vault: terminate TLS (trusted CA) → strip agent creds → inject real cred
                     → verified TLS to api.slack.com
  ← response; agent never saw the Slack token
```

### 3.2 web_fetch tool (enabled)
```
model emits web_fetch{url}
  → LiveFetchProvider: httpx.Client(**base, **injection.httpx_client_kwargs(cfg))
        proxy=Agent Vault, verify=CA
  → request routed through proxy, same injection as above
```

### 3.3 Model/provider egress (always, unaffected)
```
LiteLlm / google-genai → their own httpx/aiohttp clients (trust_env=False for gate1a)
  → NO egress-proxy overlay applied → direct to provider API with provider key
```

### 3.4 Disabled (default)
`EgressProxyConfig.enabled is False` → both builders return `{}` → Bash env is `{"PATH": …}`, httpx client is constructed exactly as today. Inert.

## 4. Fail-closed enforcement

Two layers, matching what we actually control in OSS:

1. **Startup/shared builders (FR4):** `cfg.validate()` raises if enabled-but-misconfigured. Startup calls it, and the shared injection builders call it again so CLI/toolhost/direct builder paths cannot silently fall back to an empty overlay.
2. **Request time:** because injection forces `HTTPS_PROXY`/`proxy=` onto the tool paths, an unreachable proxy makes the call error out. There is no code path that drops the overlay and retries direct. (We never set `NO_PROXY`, never catch-and-bypass.)

Kernel-level guarantee that *nothing else* on the box can egress directly is explicitly out of OSS scope (Spec N5) and handled by hosted deploy (B).

## 5. Proxy-auth handling detail

Agent Vault scopes the session via proxy credentials. Two consumers, two mechanisms:
- **Subprocess/CLI tools** receive only the auth-free proxy origin. Auth is deliberately not composed into `HTTPS_PROXY` because Bash can print env values to model-visible stdout/stderr. Authenticated subprocess proxying needs a future non-env credential mechanism.
- **httpx** → `httpx.Proxy(url, headers={"Proxy-Authorization": basic/bearer})`. URL stays auth-free.

This keeps `MAGI_EGRESS_PROXY_URL` safe to log/emit while still authenticating the session.

## 6. Evidence

`evidence.py` adapts onto `evidence/observed_egress.py` (already calls `safe_proxy_url_from_env`) and reuses the `GATE1A_EGRESS_TELEMETRY_SOURCE = "gate5b_egress_proxy"` tag. One redacted, digest-only record per proxied call class. Emission is best-effort; wrapped so failure never breaks a tool call (Spec FR5).

## 7. Testing strategy (no network)

| Unit | Test |
|---|---|
| `config.from_env` | tri-state on/off/unset; alias parsing |
| `config.validate` | enabled+missing-url raises; enabled+bad-ca raises; disabled skips |
| `subprocess_env_overlay` | enabled → expected auth-free keys+values; disabled → `{}`; enabled misconfig raises |
| `httpx_client_kwargs` | enabled → proxy+verify; disabled → `{}`; Proxy-Authorization header set |
| gate5b wiring | enabled → Bash env contains auth-free overlay; `printenv HTTPS_PROXY` does not leak auth; disabled → env == `{"PATH":…}` (byte-identical) |
| live_fetch wiring | enabled → client built with proxy/verify; disabled → unchanged construction |
| isolation (FR3) | model/provider client construction never receives overlay |

All via fake env maps and constructed-but-not-sent transports/fakes; zero sockets.

## 8. Risk register

- **R1 — overlay leaks into model egress.** Mitigation: explicit scoped injection + an FR3 isolation test asserting model client construction is untouched.
- **R2 — CA env var coverage gaps** (a tool runtime that reads a var we didn't set). Mitigation: enumerate the common set now (FR1), document the limitation, extend on demand (OQ3).
- **R3 — proxy auth accidentally logged.** Mitigation: auth never stored in the config URL, never composed into subprocess env, and only applied by clients that can carry `Proxy-Authorization` out of env; evidence is digest-only/redacted.
- **R4 — disabled path drift** (someone makes the seam do work even when off). Mitigation: byte-identical test on the gate5b Bash env when disabled.
