"""Standalone Agent Vault server — the per-bot hosted sidecar process.

This is the entrypoint behind ``magi-agent vault-serve``. It runs as the bot's
sidecar container and:

1. **Self-generates a CA** on first boot (:func:`bootstrap_ca`) into the shared
   CA dir (``AGENT_VAULT_CA_DIR``, default ``/etc/agent-vault`` — an emptyDir
   shared with the runtime container). It copies ONLY the CA *cert*
   (``mitmproxy-ca-cert.pem``) to ``<dir>/ca.pem`` (0644, world-readable) so the
   runtime can trust it. The CA *private key* stays in the mitmproxy confdir at
   0600 and is never copied to the shared volume.
2. Runs the **credential-injection proxy** (reusing
   :func:`local_proxy.start_local_proxy`) on ``127.0.0.1:<listen_port>`` —
   same-pod netns, reachable from the runtime via localhost.
3. Runs an **admin API** (:func:`build_vault_admin_app`) on
   ``0.0.0.0:<admin_port>``, token-authed by ``AGENT_VAULT_PROXY_AUTH``, where
   the hosted dashboard's secret lands. It reuses the same encrypted
   :class:`local_vault.LocalVault` store + the redacted ``store`` /
   ``approvals_store`` metadata files.

Security model
--------------
* The plaintext secret is forwarded ONLY into ``LocalVault.store_secret`` and is
  never logged, returned, persisted in metadata, or embedded in an exception.
  ``LocalVault.get_secret`` (the single decryption path) is NOT exposed by any
  admin endpoint — only the credential-injection proxy ever decrypts.
* The admin API is token-authed (constant-time compare). The CA private key is
  0600 and never leaves the pod.
* mitmproxy stays an optional extra: it is imported lazily and ONLY when the
  proxy actually starts. The admin API + store paths never import it, so this
  module's admin/store surface is usable without ``magi-agent[vault]``.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from secrets import compare_digest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.credentials_admin import approvals_store, store
from magi_agent.credentials_admin.local_proxy import _harden_ca_key_perms
from magi_agent.credentials_admin.local_vault import LocalVault, LocalVaultError

logger = logging.getLogger(__name__)

# -- env defaults (spec §3) ---------------------------------------------------

_DEFAULT_CA_DIR = "/etc/agent-vault"
_DEFAULT_LISTEN_PORT = 8443
_DEFAULT_ADMIN_PORT = 8444
_DEFAULT_STORE_DIR = "/var/lib/agent-vault"

# The mitmproxy CA cert filename inside the confdir, and the shared cert name.
_MITM_CA_CERT_NAME = "mitmproxy-ca-cert.pem"
_SHARED_CA_CERT_NAME = "ca.pem"

# J-10: ``_MAX_FIELD_LEN`` retired here — the canonical home is
# ``credentials_admin/payload.MAX_FIELD_LEN`` (imported via
# ``validate_register_body``).


class VaultServerConfigError(RuntimeError):
    """Raised (fail-fast, secret-free) when required vault-server config is missing."""


# -- config -------------------------------------------------------------------


class VaultServerConfig:
    """Resolved sidecar config (env-derived). Holds no secret beyond the token."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self.ca_dir = Path(env.get("AGENT_VAULT_CA_DIR") or _DEFAULT_CA_DIR)
        self.listen_port = _parse_port(
            env.get("AGENT_VAULT_LISTEN_PORT"), _DEFAULT_LISTEN_PORT
        )
        self.admin_port = _parse_port(
            env.get("AGENT_VAULT_ADMIN_PORT"), _DEFAULT_ADMIN_PORT
        )
        self.store_dir = Path(
            env.get("AGENT_VAULT_STORE_DIR")
            or env.get("MAGI_VAULT_DIR")
            or _DEFAULT_STORE_DIR
        )
        self.bot_id = env.get("VAULT_BOT_ID") or ""

        # Required — fail fast with a clear (secret-free) error if unset.
        token = env.get("AGENT_VAULT_PROXY_AUTH")
        if not token or not token.strip():
            raise VaultServerConfigError(
                "AGENT_VAULT_PROXY_AUTH is required (admin/proxy session token); "
                "refusing to start the Agent Vault sidecar without it."
            )
        self.admin_token = token

    @property
    def confdir(self) -> Path:
        """mitmproxy confdir (holds the CA private key, 0600)."""
        return self.store_dir / "mitmproxy"


def _parse_port(value: str | None, default: int) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


# -- store wiring -------------------------------------------------------------
#
# The reused ``store`` / ``approvals_store`` modules locate their JSON files via
# explicit ``path=`` args (falling back to MAGI_CONFIG / ~/.magi). The sidecar
# pins them to files under the configured store dir so all three artifacts
# (encrypted vault, redacted credential metadata, approval requests) live
# together on the sidecar's writable volume.


def _credentials_path(store_dir: Path) -> Path:
    return store_dir / "credentials.json"


def _approvals_path(store_dir: Path) -> Path:
    return store_dir / "credential_approvals.json"


def _vault(store_dir: Path) -> LocalVault:
    return LocalVault(vault_dir=store_dir / "vault")


# -- admin API ----------------------------------------------------------------


def build_vault_admin_app(*, admin_token: str, store_dir: Path | str) -> FastAPI:
    """Build the token-authed admin FastAPI app for the vault sidecar.

    Importable + testable WITHOUT mitmproxy — it touches only the encrypted
    ``LocalVault`` store and the redacted ``store`` / ``approvals_store`` files.

    All routes require ``x-gateway-token == admin_token`` (constant-time). The
    register route forwards the secret ONLY into ``LocalVault.store_secret`` and
    responds without it. ``get_secret`` is never exposed.
    """
    store_path = Path(store_dir)
    app = FastAPI(title="Agent Vault admin", version="1")
    app.state.vault_store_dir = str(store_path)

    def _auth(request: Request) -> JSONResponse | None:
        token = request.headers.get("x-gateway-token")
        # Compare bytes so a non-ASCII header byte fails closed as 401 rather
        # than raising from compare_digest's ASCII-only string path.
        if token is not None and compare_digest(
            token.encode("utf-8"), admin_token.encode("utf-8")
        ):
            return None
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    @app.get("/v1/vault/status")
    async def vault_status(request: Request) -> JSONResponse:
        unauthorized = _auth(request)
        if unauthorized is not None:
            return unauthorized
        present = _vault(store_path).is_provisioned()
        # Surface the most-recent redacted credential-proxy fault (if any) so an
        # operator can see when the proxy fail-closed-blocked an egress request
        # because a matched credential's secret was missing/undecryptable. The
        # recorder holds NO secret material (suffix + host + reason + timestamp).
        from magi_agent.credentials_admin.local_proxy import last_proxy_fault

        return JSONResponse(
            content={
                "present": present,
                "healthy": present,
                "lastProxyFault": last_proxy_fault(),
            }
        )

    @app.post("/v1/vault/credentials")
    async def create_credential(request: Request) -> JSONResponse:
        unauthorized = _auth(request)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})

        fields = _validate_body(body)
        if isinstance(fields, JSONResponse):
            return fields
        service, label, auth_scheme, secret, requires_approval, host = fields

        # Forward the plaintext ONLY into the encrypted vault, then drop it. The
        # vault never returns/logs/raises the plaintext; on failure it raises a
        # secret-free LocalVaultError.
        try:
            vault_ref = _vault(store_path).store_secret(secret)
        except LocalVaultError:
            logger.warning("vault rejected credential for service=%s", service)
            return JSONResponse(
                status_code=503,
                content={
                    "error": "vault_unavailable",
                    "message": "credential could not be stored",
                },
            )
        finally:
            secret = ""  # noqa: F841 - scrub the local plaintext reference

        projection = store.add_credential(
            service=service,
            label=label,
            auth_scheme=auth_scheme,
            status=store.STATUS_ACTIVE,
            vault_ref=vault_ref,
            requires_approval=requires_approval,
            host=host,
            path=_credentials_path(store_path),
        )
        # Never echo the secret — only the opaque ref + redacted projection.
        return JSONResponse(
            content={"vault_ref": vault_ref, "credential": projection}
        )

    @app.post("/v1/vault/credentials/revoke")
    async def revoke_credential(request: Request) -> JSONResponse:
        unauthorized = _auth(request)
        if unauthorized is not None:
            return unauthorized
        # Contract: the opaque vault_ref is carried in the BODY (not the path) so
        # the chat-proxy SSRF allowlist is a small set of FIXED paths with no
        # caller-controlled path segments. Mirrors vault-client.revokeCredential.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        vault_ref = body.get("vaultRef")
        if not isinstance(vault_ref, str) or not vault_ref:
            return JSONResponse(
                status_code=400,
                content={"error": "field_invalid", "field": "vaultRef"},
            )
        existing = next(
            (
                c
                for c in store.load_credentials(_credentials_path(store_path))[
                    "credentials"
                ]
                if c.get("vault_ref") == vault_ref
            ),
            None,
        )
        if existing is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "credential not found"},
            )
        # Delete the encrypted secret first (idempotent), then mark metadata.
        _vault(store_path).delete_secret(vault_ref)
        updated = store.set_status(
            str(existing["id"]),
            store.STATUS_REVOKED,
            path=_credentials_path(store_path),
        )
        if updated is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "credential not found"},
            )
        return JSONResponse(content={"credential": updated})

    @app.post("/v1/vault/credentials/requires-approval")
    async def set_requires_approval(request: Request) -> JSONResponse:
        unauthorized = _auth(request)
        if unauthorized is not None:
            return unauthorized
        # Contract: { vaultRef, requiresApproval } in the body. Mirrors
        # vault-client.setRequiresApproval. No secret is involved.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        vault_ref = body.get("vaultRef")
        if not isinstance(vault_ref, str) or not vault_ref:
            return JSONResponse(
                status_code=400,
                content={"error": "field_invalid", "field": "vaultRef"},
            )
        requires_approval = body.get("requiresApproval")
        if not isinstance(requires_approval, bool):
            return JSONResponse(
                status_code=400,
                content={"error": "field_invalid", "field": "requiresApproval"},
            )
        updated = store.set_requires_approval_by_ref(
            vault_ref,
            requires_approval,
            path=_credentials_path(store_path),
        )
        if updated is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "credential not found"},
            )
        return JSONResponse(content={"credential": updated})

    @app.get("/v1/vault/approvals")
    async def list_approvals(request: Request) -> JSONResponse:
        unauthorized = _auth(request)
        if unauthorized is not None:
            return unauthorized
        status = request.query_params.get("status")
        approvals = approvals_store.list_approvals(
            status=status or None, path=_approvals_path(store_path)
        )
        return JSONResponse(content={"approvals": approvals})

    @app.post("/v1/vault/approvals/resolve")
    async def resolve_approval(request: Request) -> JSONResponse:
        unauthorized = _auth(request)
        if unauthorized is not None:
            return unauthorized
        # Contract: { approvalId, decision } in the body. Mirrors
        # vault-client.resolveApproval. No secret is involved.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        approval_id = body.get("approvalId")
        if not isinstance(approval_id, str) or not approval_id:
            return JSONResponse(
                status_code=400,
                content={"error": "field_invalid", "field": "approvalId"},
            )
        decision = body.get("decision")
        if decision not in approvals_store.DECISION_STATUSES:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_decision", "field": "decision"},
            )
        updated = approvals_store.decide_approval(
            approval_id, decision, path=_approvals_path(store_path)
        )
        if updated is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "approval not found"},
            )
        return JSONResponse(content={"approval": updated})

    return app


def _validate_body(
    body: object,
) -> tuple[str, str, str, str, bool, str | None] | JSONResponse:
    """J-10: thin wrapper delegating to the single seam in
    ``credentials_admin/payload.validate_register_body``. Pre-J-10 this
    function carried a byte-identical copy of the validator (the
    leading comment said "Mirrors transport.credentials' validation").
    The dedup now means the two HTTP surfaces can never drift.

    Never echoes the secret in an error (the secret field is checked
    for presence only — its value never lands in an error response).
    """

    from magi_agent.credentials_admin.payload import (
        RegisterPayloadError,
        validate_register_body,
    )

    result = validate_register_body(body)
    if isinstance(result, RegisterPayloadError):
        content: dict[str, object] = {"error": result.error}
        if result.field is not None:
            content["field"] = result.field
        return JSONResponse(status_code=400, content=content)
    return (
        result.service,
        result.label,
        result.auth_scheme,
        result.secret,
        result.requires_approval,
        result.host,
    )


# -- CA bootstrap -------------------------------------------------------------


def bootstrap_ca(*, ca_dir: Path | str, confdir: Path | str) -> Path:
    """Generate the mitmproxy CA and copy ONLY its cert to ``<ca_dir>/ca.pem``.

    The CA is materialized inside ``confdir`` (the mitmproxy confdir, holding the
    private key at 0600). The CA *cert* is copied to ``<ca_dir>/ca.pem`` at 0644
    so the runtime container can read + trust it. The CA *private key* is NEVER
    copied to ``ca_dir``.

    Lazily imports mitmproxy's cert utilities (no proxy start required); raises
    :class:`local_proxy.LocalProxyUnavailable` with an install hint when the
    optional ``magi-agent[vault]`` extra is missing.

    Returns the path to the written shared CA cert.
    """
    from magi_agent.credentials_admin.local_proxy import (
        LocalProxyUnavailable,
        _VAULT_INSTALL_HINT,
    )

    try:
        from mitmproxy import certs as mitm_certs
    except ImportError as exc:  # pragma: no cover - exercised only without extra
        raise LocalProxyUnavailable(_VAULT_INSTALL_HINT) from exc

    confdir_path = Path(confdir)
    ca_dir_path = Path(ca_dir)
    confdir_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    ca_dir_path.mkdir(parents=True, exist_ok=True, mode=0o755)
    try:
        os.chmod(confdir_path, 0o700)
    except OSError:
        pass

    # mitmproxy's CertStore materializes mitmproxy-ca.pem (cert+key, 0600),
    # mitmproxy-ca-cert.pem (cert only), etc. into the confdir on first use.
    mitm_certs.CertStore.from_store(
        path=str(confdir_path), basename="mitmproxy", key_size=2048
    )

    _harden_ca_key_perms(confdir_path)

    src_cert = confdir_path / _MITM_CA_CERT_NAME
    if not src_cert.is_file():
        raise LocalProxyUnavailable(
            "mitmproxy did not produce a CA cert in the confdir"
        )
    dst_cert = ca_dir_path / _SHARED_CA_CERT_NAME
    # Copy ONLY the cert (never the *-ca.pem / *-ca-key.pem private-key files).
    shutil.copyfile(src_cert, dst_cert)
    try:
        os.chmod(dst_cert, 0o644)
    except OSError:
        pass
    return dst_cert


# -- server entrypoint --------------------------------------------------------


def run_vault_server(
    *,
    env: Mapping[str, str] | None = None,
    _serve: bool = True,
) -> None:
    """Run the Agent Vault sidecar: bootstrap CA, start proxy + admin API.

    ``env`` defaults to ``os.environ``. ``_serve`` is an internal test seam — when
    False, config + CA bootstrap run but neither the proxy nor uvicorn is started
    (so the fail-fast / config path is testable without a network or mitmproxy).
    """
    env = os.environ if env is None else env
    config = VaultServerConfig(env)  # raises VaultServerConfigError if no token

    config.store_dir.mkdir(parents=True, exist_ok=True)

    if not _serve:
        return

    # 1) CA bootstrap: write the shared cert; keep the private key 0600 in confdir.
    try:
        bootstrap_ca(ca_dir=config.ca_dir, confdir=config.confdir)
    except Exception as exc:  # noqa: BLE001 - surface a clear message, no secret
        logger.error("Agent Vault: CA bootstrap failed: %s", exc.__class__.__name__)
        raise

    # 2) Credential-injection proxy on 127.0.0.1:<listen_port> (same confdir CA).
    from magi_agent.credentials_admin.local_proxy import start_local_proxy

    handle = start_local_proxy(
        config.store_dir / "vault",
        port=config.listen_port,
        # Pin the addon's credential + approval files to the sidecar store so the
        # proxy producer and the admin API the dashboard reads share ONE store.
        store_dir=config.store_dir,
    )
    logger.info(
        "Agent Vault proxy listening on 127.0.0.1:%s (bot=%s)",
        handle.port,
        config.bot_id or "-",
    )

    # 3) Admin API on 0.0.0.0:<admin_port>, token-authed.
    import uvicorn

    app = build_vault_admin_app(
        admin_token=config.admin_token, store_dir=config.store_dir
    )
    try:
        uvicorn.run(app, host="0.0.0.0", port=config.admin_port)
    finally:
        handle.stop()
