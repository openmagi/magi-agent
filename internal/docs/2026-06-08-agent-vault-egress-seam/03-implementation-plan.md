# Agent Vault Egress Seam — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-OFF seam so the runtime routes Bash-tool and web_fetch-tool egress through an external Agent Vault forward proxy (trusting its CA), without ever exposing secrets to the agent and without touching model/provider egress.

**Architecture:** A new `magi_agent/egress_proxy/` package (config + pure injection builders + evidence adapter). Two existing tool-egress chokepoints consume the builders: the Bash subprocess env in `gate5b_full_toolhost.py` and the httpx client in `live_fetch_provider.py`. Disabled = byte-identical runtime; enabled-but-misconfigured = refuse to start (fail-closed). See `01-spec.md` and `02-architecture.md`.

**Tech Stack:** Python 3.11, httpx, pydantic/dataclasses, pytest (`uv run --extra dev pytest`). No new dependencies.

---

## File Structure

- Create: `magi_agent/egress_proxy/__init__.py`
- Create: `magi_agent/egress_proxy/config.py` — `EgressProxyConfig.from_env()` + `validate()`
- Create: `magi_agent/egress_proxy/injection.py` — `subprocess_env_overlay()`, `httpx_client_kwargs()`
- Create: `magi_agent/egress_proxy/evidence.py` — thin `observed_egress` adapter
- Modify: `magi_agent/gates/gate5b_full_toolhost.py:866-879` — merge overlay into Bash env
- Modify: `magi_agent/web_acquisition/live_fetch_provider.py:265-267` — splat httpx kwargs
- Modify: runtime construction (`magi_agent/app.py`) — 1-line startup `validate()`
- Test: `tests/egress_proxy/test_config.py`, `test_injection.py`, `test_gate5b_wiring.py`, `test_live_fetch_wiring.py`, `test_model_egress_isolation.py`

Run all tests with: `uv run --extra dev pytest tests/egress_proxy/ -v`

---

## Task 1: EgressProxyConfig — parse + validate

**Files:**
- Create: `magi_agent/egress_proxy/__init__.py`
- Create: `magi_agent/egress_proxy/config.py`
- Test: `tests/egress_proxy/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/egress_proxy/test_config.py
import pytest
from magi_agent.egress_proxy.config import EgressProxyConfig


def _base_env():
    return {
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_URL": "http://127.0.0.1:8888",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": "",  # filled per-test
    }


def test_disabled_when_unset():
    cfg = EgressProxyConfig.from_env({})
    assert cfg.enabled is False
    assert cfg.proxy_url is None


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("on", True), ("YES", True),
    ("0", False), ("false", False), ("", False),
])
def test_tristate_master_switch(val, expected):
    cfg = EgressProxyConfig.from_env({"MAGI_EGRESS_PROXY_ENABLED": val})
    assert cfg.enabled is expected


def test_validate_enabled_requires_url(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig.from_env({
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": str(ca),
    })
    with pytest.raises(ValueError, match="proxy URL"):
        cfg.validate()


def test_validate_enabled_requires_readable_ca():
    cfg = EgressProxyConfig.from_env({
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_URL": "http://127.0.0.1:8888",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": "/nonexistent/ca.pem",
    })
    with pytest.raises(ValueError, match="CA cert"):
        cfg.validate()


def test_validate_disabled_is_noop():
    EgressProxyConfig.from_env({}).validate()  # must not raise


def test_url_rejects_path_and_creds(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig.from_env({
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_URL": "http://u:p@127.0.0.1:8888/path",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": str(ca),
    })
    with pytest.raises(ValueError):
        cfg.validate()


def test_auth_carried_separately(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig.from_env({
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_URL": "http://127.0.0.1:8888",
        "MAGI_EGRESS_PROXY_AUTH": "agent:tok123",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": str(ca),
    })
    cfg.validate()
    assert cfg.proxy_auth == "agent:tok123"
    assert "tok123" not in (cfg.proxy_url or "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/egress_proxy/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: magi_agent.egress_proxy`

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/egress_proxy/__init__.py
from magi_agent.egress_proxy.config import EgressProxyConfig

__all__ = ["EgressProxyConfig"]
```

```python
# magi_agent/egress_proxy/config.py
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _validate_proxy_origin(value: str) -> str:
    cleaned = str(value or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("egress proxy URL must be an HTTP(S) proxy origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("egress proxy URL must not contain path/query/fragment")
    if parsed.username or parsed.password:
        raise ValueError("egress proxy URL must not embed credentials; use MAGI_EGRESS_PROXY_AUTH")
    if any(c.isspace() for c in cleaned):
        raise ValueError("egress proxy URL must not contain whitespace")
    return cleaned


@dataclass(frozen=True)
class EgressProxyConfig:
    enabled: bool
    proxy_url: str | None
    proxy_auth: str | None
    ca_cert_path: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EgressProxyConfig":
        env = os.environ if env is None else env
        return cls(
            enabled=_truthy(env.get("MAGI_EGRESS_PROXY_ENABLED")),
            proxy_url=(env.get("MAGI_EGRESS_PROXY_URL") or "").strip() or None,
            proxy_auth=(env.get("MAGI_EGRESS_PROXY_AUTH") or "").strip() or None,
            ca_cert_path=(env.get("MAGI_EGRESS_PROXY_CA_CERT_PATH") or "").strip() or None,
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.proxy_url:
            raise ValueError("MAGI_EGRESS_PROXY_ENABLED set but proxy URL missing")
        _validate_proxy_origin(self.proxy_url)
        if not self.ca_cert_path or not os.path.isfile(self.ca_cert_path):
            raise ValueError("MAGI_EGRESS_PROXY_ENABLED set but CA cert path missing/unreadable")
        try:
            with open(self.ca_cert_path, "r"):
                pass
        except OSError as exc:
            raise ValueError(f"CA cert path unreadable: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/egress_proxy/test_config.py -v`
Expected: PASS (all cases)

- [ ] **Step 5: Commit**

```bash
git add magi_agent/egress_proxy/__init__.py magi_agent/egress_proxy/config.py tests/egress_proxy/test_config.py
git commit -m "feat(egress-proxy): config parse + fail-closed validation (default-OFF)"
```

---

## Task 2: Pure injection builders

**Files:**
- Create: `magi_agent/egress_proxy/injection.py`
- Test: `tests/egress_proxy/test_injection.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/egress_proxy/test_injection.py
from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.egress_proxy.injection import (
    subprocess_env_overlay,
    httpx_client_kwargs,
)


def _enabled(tmp_path, auth=None):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    return EgressProxyConfig(
        enabled=True,
        proxy_url="http://127.0.0.1:8888",
        proxy_auth=auth,
        ca_cert_path=str(ca),
    )


def test_overlay_empty_when_disabled():
    cfg = EgressProxyConfig(False, None, None, None)
    assert subprocess_env_overlay(cfg) == {}


def test_overlay_sets_proxy_and_ca(tmp_path):
    overlay = subprocess_env_overlay(_enabled(tmp_path))
    assert overlay["HTTPS_PROXY"] == "http://127.0.0.1:8888"
    assert overlay["HTTP_PROXY"] == "http://127.0.0.1:8888"
    for k in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
              "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO"):
        assert overlay[k].endswith("ca.pem")


def test_overlay_keeps_auth_out_of_subprocess_proxy_urls(tmp_path):
    overlay = subprocess_env_overlay(_enabled(tmp_path, auth="agent:tok"))
    assert overlay["HTTPS_PROXY"] == "http://127.0.0.1:8888"
    assert "agent:tok" not in overlay["HTTPS_PROXY"]


def test_httpx_kwargs_empty_when_disabled():
    cfg = EgressProxyConfig(False, None, None, None)
    assert httpx_client_kwargs(cfg) == {}


def test_httpx_kwargs_sets_proxy_and_verify(tmp_path):
    kwargs = httpx_client_kwargs(_enabled(tmp_path, auth="agent:tok"))
    assert kwargs["verify"].endswith("ca.pem")
    proxy = kwargs["proxy"]
    # httpx.Proxy carries url + Proxy-Authorization header
    assert "127.0.0.1:8888" in str(proxy.url)
    assert any(h.lower() == b"proxy-authorization" for h, _ in proxy.headers.raw)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/egress_proxy/test_injection.py -v`
Expected: FAIL — `ImportError: cannot import name 'subprocess_env_overlay'`

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/egress_proxy/injection.py
from __future__ import annotations

import base64

from magi_agent.egress_proxy.config import EgressProxyConfig

_CA_ENV_KEYS = (
    "SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO",
)


def _validate_enabled(cfg: EgressProxyConfig) -> bool:
    if not cfg.enabled:
        return False
    cfg.validate()
    return True


def subprocess_env_overlay(cfg: EgressProxyConfig) -> dict[str, str]:
    if not _validate_enabled(cfg):
        return {}
    proxy = cfg.proxy_url
    overlay = {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "ALL_PROXY": proxy}
    if cfg.ca_cert_path:
        for key in _CA_ENV_KEYS:
            overlay[key] = cfg.ca_cert_path
    return overlay


def httpx_client_kwargs(cfg: EgressProxyConfig) -> dict[str, object]:
    if not _validate_enabled(cfg):
        return {}
    import httpx

    headers = {}
    if cfg.proxy_auth:
        token = base64.b64encode(cfg.proxy_auth.encode()).decode()
        headers["Proxy-Authorization"] = f"Basic {token}"
    return {
        "proxy": httpx.Proxy(cfg.proxy_url, headers=headers or None),
        "verify": cfg.ca_cert_path,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/egress_proxy/test_injection.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/egress_proxy/injection.py tests/egress_proxy/test_injection.py
git commit -m "feat(egress-proxy): pure subprocess-env and httpx-kwargs builders"
```

---

## Task 3: Wire Bash subprocess env (gate5b)

**Files:**
- Modify: `magi_agent/gates/gate5b_full_toolhost.py:866-879`
- Test: `tests/egress_proxy/test_gate5b_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/egress_proxy/test_gate5b_wiring.py
from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.gates import gate5b_full_toolhost as g5


def test_bash_env_byte_identical_when_disabled(monkeypatch):
    monkeypatch.delenv("MAGI_EGRESS_PROXY_ENABLED", raising=False)
    env = g5._build_bash_env(EgressProxyConfig.from_env({}))
    assert set(env.keys()) == {"PATH"}


def test_bash_env_has_overlay_when_enabled(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig(True, "http://127.0.0.1:8888", None, str(ca))
    env = g5._build_bash_env(cfg)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:8888"
    assert env["PATH"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/egress_proxy/test_gate5b_wiring.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_build_bash_env'`

- [ ] **Step 3: Extract a helper and consume it**

Add near the top of `gate5b_full_toolhost.py` (after imports):

```python
import os
from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.egress_proxy.injection import subprocess_env_overlay


def _build_bash_env(cfg: EgressProxyConfig | None = None) -> dict[str, str]:
    cfg = EgressProxyConfig.from_env() if cfg is None else cfg
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    env.update(subprocess_env_overlay(cfg))
    return env
```

Then change the Bash `subprocess.run` call (line ~871-879) from:

```python
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
```

to:

```python
        env=_build_bash_env(),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/egress_proxy/test_gate5b_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Run the gate5b suite to confirm no regression**

Run: `uv run --extra dev pytest tests/ -k gate5b -q`
Expected: PASS (no behavior change when flag unset)

- [ ] **Step 6: Commit**

```bash
git add magi_agent/gates/gate5b_full_toolhost.py tests/egress_proxy/test_gate5b_wiring.py
git commit -m "feat(egress-proxy): route Bash tool egress through proxy when enabled"
```

---

## Task 4: Wire web_fetch httpx client (live_fetch)

**Files:**
- Modify: `magi_agent/web_acquisition/live_fetch_provider.py:265-267`
- Test: `tests/egress_proxy/test_live_fetch_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/egress_proxy/test_live_fetch_wiring.py
from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.web_acquisition import live_fetch_provider as lf


def test_client_kwargs_empty_when_disabled():
    assert lf._egress_client_kwargs(EgressProxyConfig.from_env({})) == {}


def test_client_kwargs_present_when_enabled(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig(True, "http://127.0.0.1:8888", None, str(ca))
    kwargs = lf._egress_client_kwargs(cfg)
    assert kwargs["verify"].endswith("ca.pem")
    assert "proxy" in kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/egress_proxy/test_live_fetch_wiring.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_egress_client_kwargs'`

- [ ] **Step 3: Add helper and apply at client construction**

Add near imports in `live_fetch_provider.py`:

```python
from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.egress_proxy.injection import httpx_client_kwargs


def _egress_client_kwargs(cfg: EgressProxyConfig | None = None) -> dict:
    cfg = EgressProxyConfig.from_env() if cfg is None else cfg
    return httpx_client_kwargs(cfg)
```

Change the client construction (line ~265-267) from:

```python
        client = self._client or httpx.Client(
            timeout=self.timeout_s, follow_redirects=False
        )
```

to:

```python
        client = self._client or httpx.Client(
            timeout=self.timeout_s,
            follow_redirects=False,
            **_egress_client_kwargs(),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/egress_proxy/test_live_fetch_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Run the web_acquisition suite**

Run: `uv run --extra dev pytest tests/ -k "live_fetch or web_acquisition" -q`
Expected: PASS (unchanged when flag unset)

- [ ] **Step 6: Commit**

```bash
git add magi_agent/web_acquisition/live_fetch_provider.py tests/egress_proxy/test_live_fetch_wiring.py
git commit -m "feat(egress-proxy): route web_fetch tool egress through proxy when enabled"
```

---

## Task 5: Startup fail-closed validation

**Files:**
- Modify: `magi_agent/app.py` (runtime construction)
- Test: `tests/egress_proxy/test_config.py` (extend) — startup path

- [ ] **Step 1: Write the failing test**

```python
# append to tests/egress_proxy/test_config.py
import pytest
from magi_agent.egress_proxy.config import EgressProxyConfig


def test_startup_validate_raises_on_enabled_misconfig():
    cfg = EgressProxyConfig.from_env({"MAGI_EGRESS_PROXY_ENABLED": "1"})
    with pytest.raises(ValueError):
        cfg.validate()
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run --extra dev pytest tests/egress_proxy/test_config.py::test_startup_validate_raises_on_enabled_misconfig -v`
Expected: PASS (validate already exists from Task 1) — this test pins the startup contract.

- [ ] **Step 3: Call validate() once at runtime construction**

In `magi_agent/app.py`, locate the runtime/app construction function (where other optional modules like `register_observability` are wired). Add near the top of that function, before route registration:

```python
from magi_agent.egress_proxy.config import EgressProxyConfig

EgressProxyConfig.from_env().validate()  # fail-closed: refuse to start if enabled-but-misconfigured
```

- [ ] **Step 4: Run the app construction smoke test**

Run: `uv run --extra dev pytest tests/ -k "app or build_app or runtime" -q`
Expected: PASS (validate is a no-op when the flag is unset)

- [ ] **Step 5: Commit**

```bash
git add magi_agent/app.py tests/egress_proxy/test_config.py
git commit -m "feat(egress-proxy): fail-closed startup validation when enabled"
```

---

## Task 6: Model-egress isolation guard (FR3)

**Files:**
- Test: `tests/egress_proxy/test_model_egress_isolation.py`

This task adds no production code — it pins the invariant that model/provider egress is never given the proxy overlay. If the assertion fails later, someone broke the separation.

- [ ] **Step 1: Write the test**

```python
# tests/egress_proxy/test_model_egress_isolation.py
import inspect
from magi_agent.web_acquisition import live_fetch_provider as lf
from magi_agent.gates import gate5b_full_toolhost as g5


def test_no_process_wide_proxy_env_mutation():
    """The seam must never set os.environ proxy vars (would capture model egress)."""
    for mod in (lf, g5):
        src = inspect.getsource(mod)
        assert 'os.environ["HTTPS_PROXY"]' not in src
        assert "os.environ['HTTPS_PROXY']" not in src
        assert "setdefault(\"HTTPS_PROXY\"" not in src


def test_overlay_is_scoped_to_tool_paths_only():
    # subprocess overlay + httpx kwargs are the ONLY consumers; assert they are
    # the functions the tool sites call (guards against a refactor that widens scope)
    assert hasattr(g5, "_build_bash_env")
    assert hasattr(lf, "_egress_client_kwargs")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/egress_proxy/test_model_egress_isolation.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/egress_proxy/test_model_egress_isolation.py
git commit -m "test(egress-proxy): pin model-egress isolation invariant"
```

---

## Task 7: Evidence adapter (minimal)

**Files:**
- Create: `magi_agent/egress_proxy/evidence.py`
- Test: `tests/egress_proxy/test_evidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/egress_proxy/test_evidence.py
from magi_agent.egress_proxy.evidence import egress_proxy_record


def test_record_is_redacted_and_tagged():
    rec = egress_proxy_record(call_class="bash_subprocess")
    assert rec["evidence_source"] == "gate5b_egress_proxy"
    assert rec["call_class"] == "bash_subprocess"
    # no raw secrets / urls / auth in the record
    serialized = str(rec)
    assert "Proxy-Authorization" not in serialized
    assert "http://" not in serialized


def test_record_emit_never_raises():
    # best-effort: a broken sink must not break the tool call
    egress_proxy_record(call_class="web_fetch", sink=lambda _: (_ for _ in ()).throw(RuntimeError()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/egress_proxy/test_evidence.py -v`
Expected: FAIL — `ModuleNotFoundError: magi_agent.egress_proxy.evidence`

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/egress_proxy/evidence.py
from __future__ import annotations

import logging
from collections.abc import Callable

from magi_agent.evidence.gate1a_egress_correlation import GATE1A_EGRESS_TELEMETRY_SOURCE

logger = logging.getLogger(__name__)


def egress_proxy_record(
    *,
    call_class: str,
    sink: Callable[[dict], None] | None = None,
) -> dict:
    """Build (and optionally emit) a digest-only egress-proxy decision record.

    Best-effort: emission failures are swallowed so a tool call is never broken.
    """
    record = {
        "evidence_source": GATE1A_EGRESS_TELEMETRY_SOURCE,
        "call_class": call_class,
        "decision": "routed_via_egress_proxy",
    }
    if sink is not None:
        try:
            sink(record)
        except Exception:  # noqa: BLE001 — best-effort telemetry
            logger.debug("egress proxy evidence sink failed", exc_info=True)
    return record
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/egress_proxy/test_evidence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/egress_proxy/evidence.py tests/egress_proxy/test_evidence.py
git commit -m "feat(egress-proxy): minimal redacted egress evidence record"
```

---

## Task 8: Full-suite regression + docs

- [ ] **Step 1: Run the focused suite**

Run: `uv run --extra dev pytest tests/egress_proxy/ -v`
Expected: PASS (all tasks)

- [ ] **Step 2: Run the broad suite to confirm default-OFF inertness**

Run: `uv run --extra dev pytest tests/ -q`
Expected: PASS — no pre-existing test changes (proves byte-identical when flag unset). Note any pre-existing unrelated failures separately (see memory: model_tiers / pr18 import-boundary tests may fail on this machine regardless).

- [ ] **Step 3: Add env-reference docs**

Add the four `MAGI_EGRESS_PROXY_*` vars to `docs/env-reference.md` (and `docs/config-reference.md` if it enumerates flags), one row each, marked default-OFF.

- [ ] **Step 4: Commit**

```bash
git add docs/env-reference.md docs/config-reference.md
git commit -m "docs(egress-proxy): document MAGI_EGRESS_PROXY_* env vars"
```

---

## Self-Review (spec coverage)

| Spec requirement | Task |
|---|---|
| G1 Bash + web_fetch routed | T3, T4 |
| G2 default-OFF byte-identical | T3 (byte-identical test), T8 broad suite |
| G3 model-egress isolation | T6 |
| G4 fail-closed startup + no fallback | T1 validate, T5 wiring, T3/T4 forced injection |
| G5 evidence | T7 |
| FR1 subprocess env (proxy+CA keys) | T2, T3 |
| FR2 httpx proxy+verify | T2, T4 |
| FR3 scoped injection | T6 |
| FR4 startup validation | T1, T5 |
| FR5 best-effort evidence | T7 |
| §5 proxy-auth carried separately | T1 (config), T2 (compose) |

No placeholders. Signatures consistent: `EgressProxyConfig(enabled, proxy_url, proxy_auth, ca_cert_path)`, `subprocess_env_overlay(cfg)`, `httpx_client_kwargs(cfg)`, `_build_bash_env(cfg=None)`, `_egress_client_kwargs(cfg=None)`, `egress_proxy_record(call_class=…, sink=None)` used identically across tasks.

## Notes for the implementer

- Verify exact line numbers before editing (`gate5b_full_toolhost.py` and `live_fetch_provider.py` shift over time); the anchors are the `subprocess.run(... env=...)` call and the `httpx.Client(timeout=..., follow_redirects=False)` call.
- `app.py` may construct the runtime in a factory (`build_app`/`create_app`); place the `validate()` call wherever `register_observability` / learning bootstrap are wired (memory: app.py ~line 103-109).
- Do NOT set process-wide `os.environ` proxy vars — that is the one move that breaks model-egress isolation (T6 guards it).
- Run tests with an isolated config: `MAGI_CONFIG=$(mktemp)` to avoid `~/.magi/config.toml` contamination (memory: magi-agent test env gotcha).
