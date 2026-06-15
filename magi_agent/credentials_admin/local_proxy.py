"""mitmproxy addon + lifecycle for the local credential-injecting forward proxy.

This is the ONLY module that fetches a plaintext secret for egress injection and
the ONLY module that imports mitmproxy. mitmproxy is an OPTIONAL dependency
(``magi-agent[vault]``); it is imported lazily so the core import graph is
unaffected when the extra is not installed. If the proxy is requested without the
extra, ``start_local_proxy`` raises :class:`LocalProxyUnavailable` with a clear
install hint.

Security model
--------------
* The plaintext secret is fetched via ``LocalVault.get_secret(vault_ref)`` and
  applied to the upstream request header INSIDE :meth:`CredentialInjectionAddon.request`
  only. It is never logged, never returned, never put on the decision object, and
  never embedded in an exception. The header value is assembled in a single local
  expression and the local reference is dropped at the end of the call frame.
* Any agent-supplied auth header for the matched header name is stripped before
  the injected one is set, so the bot cannot smuggle its own credential past the
  proxy or observe whether one was injected.
* The proxy binds to 127.0.0.1 only. Its mitmproxy confdir lives inside the 0700
  vault dir; the CA private key is chmod'd 0600.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from magi_agent.credentials_admin import approvals_store, store
from magi_agent.credentials_admin.local_proxy_decision import (
    BlockPendingApproval,
    Inject,
    PassThrough,
    decide_injection,
)
from magi_agent.credentials_admin.local_vault import LocalVault

logger = logging.getLogger(__name__)

_VAULT_INSTALL_HINT = "install magi-agent[vault]"


class LocalProxyUnavailable(RuntimeError):
    """Raised when the local proxy is requested but mitmproxy is not installed."""


@dataclass(frozen=True)
class ProxyHandle:
    """Handle for a running local proxy: its bound port + CA cert path."""

    port: int
    ca_cert_path: str
    # Internal stop callback (best-effort clean shutdown). Not part of the
    # public contract beyond ``stop()``.
    _stop: object = None

    def stop(self) -> None:
        stop = self._stop
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001 - shutdown is best-effort
                logger.debug("local proxy stop raised; ignoring", exc_info=True)


def _approval_granted(credential_id: str) -> bool:
    """True when a current (pending-or-approved... actually approved) approval
    grants use of ``credential_id``.

    The dashboard's approvals store is the source of truth. A credential is usable
    when it has at least one approval in the ``approved`` state.
    """
    try:
        approvals = approvals_store.list_approvals(
            status=approvals_store.STATUS_APPROVED
        )
    except Exception:  # noqa: BLE001 - fail closed: no approval => block
        logger.warning("approvals lookup failed; treating as not approved")
        return False
    return any(a.get("credential_id") == credential_id for a in approvals)


class CredentialInjectionAddon:
    """mitmproxy addon that injects vault credentials into matching egress.

    Constructed with a ``vault_dir`` so the addon's ``LocalVault`` reads from the
    same encrypted store the dashboard writes to. ``credentials_loader`` /
    ``approvals_lookup`` are injectable for tests; they default to the live
    redacted-metadata store and the approvals store.
    """

    def __init__(
        self,
        *,
        vault_dir: Path | None = None,
        credentials_loader=None,
        approvals_lookup=None,
        approval_enqueue=None,
    ) -> None:
        self._vault = LocalVault(vault_dir=vault_dir)
        self._credentials_loader = credentials_loader or _load_active_credentials
        self._approvals_lookup = approvals_lookup or _approval_granted
        self._approval_enqueue = approval_enqueue or approvals_store.add_approval

    def request(self, flow) -> None:  # noqa: ANN001 - mitmproxy HTTPFlow (lazy)
        """Inspect an outbound request and inject/block/pass per the decision."""
        host = flow.request.pretty_host
        credentials = self._credentials_loader()
        decision = decide_injection(
            host=host,
            credentials=credentials,
            approvals_lookup=self._approvals_lookup,
        )

        if isinstance(decision, PassThrough):
            return

        if isinstance(decision, BlockPendingApproval):
            self._block(flow, decision.credential_id, host)
            return

        if isinstance(decision, Inject):
            self._inject(flow, decision)
            return

    def _inject(self, flow, decision: Inject) -> None:  # noqa: ANN001
        # Fetch the plaintext ONLY here, assemble the header value in a single
        # local expression, and drop the reference at the end of the frame. The
        # secret is never logged, returned, or attached to the decision/flow
        # metadata.
        secret = self._vault.get_secret(decision.vault_ref)
        if not secret:
            # Active credential but the ciphertext is missing/undecryptable. Fail
            # closed: do not inject, do not forward a request the user expected to
            # be authenticated. Pass through untouched (no secret to leak).
            logger.warning(
                "local proxy: vault_ref had no decryptable secret; passing through"
            )
            return
        try:
            # Strip any agent-supplied value for this header first so the bot
            # cannot smuggle its own credential nor observe the injected one.
            if decision.header_name in flow.request.headers:
                del flow.request.headers[decision.header_name]
            flow.request.headers[decision.header_name] = decision.value_prefix + secret
        finally:
            secret = ""  # noqa: F841 - intentional scrub; do not outlive the call

    def _block(self, flow, credential_id: str, host: str) -> None:  # noqa: ANN001
        # Enqueue a pending approval (metadata only — never a secret) and return a
        # 403 so the bot's egress is halted until the operator approves.
        try:
            self._approval_enqueue(
                credential_id=credential_id,
                requested_action="egress_credential_use",
                target_host=host,
            )
        except Exception:  # noqa: BLE001 - blocking must still happen
            logger.warning("local proxy: failed to enqueue approval; still blocking")

        from mitmproxy import http  # lazy: only needed when actually blocking

        flow.response = http.Response.make(
            403,
            json.dumps(
                {
                    "error": "credential_pending_approval",
                    "credential_id": credential_id,
                }
            ),
            {"Content-Type": "application/json"},
        )


def _load_active_credentials() -> Sequence[dict[str, object]]:
    """Load the redacted credential metadata projection for matching."""
    return store.load_credentials()["credentials"]


def start_local_proxy(vault_dir: Path | str, port: int = 0) -> ProxyHandle:
    """Start the local credential-injecting forward proxy on 127.0.0.1.

    Runs a headless mitmproxy ``DumpMaster`` in a background thread with its own
    asyncio event loop. The mitmproxy confdir is ``<vault_dir>/mitmproxy`` (0700)
    so the generated CA + its private key live inside our protected vault dir; the
    CA private key files are chmod'd 0600. Returns a :class:`ProxyHandle` carrying
    the bound port and the path to ``mitmproxy-ca-cert.pem``.

    Lazily imports mitmproxy; raises :class:`LocalProxyUnavailable` (with an
    install hint) if the optional ``magi-agent[vault]`` extra is not installed.
    """
    try:
        from mitmproxy.options import Options
        from mitmproxy.tools.dump import DumpMaster
    except ImportError as exc:
        raise LocalProxyUnavailable(_VAULT_INSTALL_HINT) from exc

    vault_path = Path(vault_dir)
    confdir = vault_path / "mitmproxy"
    confdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(confdir, 0o700)
    except OSError:
        pass

    ready = threading.Event()
    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            options = Options(
                listen_host="127.0.0.1",
                listen_port=port,
                confdir=str(confdir),
            )
            master = DumpMaster(options, loop=loop, with_termlog=False, with_dumper=False)
            master.addons.add(CredentialInjectionAddon(vault_dir=vault_path))
            result["master"] = master

            async def _serve() -> None:
                await master.running()
                # Resolve the actual bound port (port=0 → ephemeral).
                bound = _resolve_bound_port(master, port)
                result["port"] = bound
                _harden_ca_key_perms(confdir)
                ready.set()
                await master.run()

            loop.run_until_complete(_serve())
        except BaseException as exc:  # noqa: BLE001 - surface to caller thread
            error["exc"] = exc
            ready.set()
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=_run, name="magi-local-vault-proxy", daemon=True)
    thread.start()
    ready.wait(timeout=30)

    if "exc" in error:
        raise LocalProxyUnavailable(
            f"local proxy failed to start ({error['exc'].__class__.__name__})"
        ) from error["exc"]

    bound_port = int(result.get("port", port))
    ca_cert_path = str(confdir / "mitmproxy-ca-cert.pem")

    master = result.get("master")

    def _stop() -> None:
        if master is not None:
            try:
                master.shutdown()
            except Exception:  # noqa: BLE001 - best-effort
                pass

    return ProxyHandle(port=bound_port, ca_cert_path=ca_cert_path, _stop=_stop)


def _resolve_bound_port(master, requested: int) -> int:  # noqa: ANN001
    """Return the actual listening port (ephemeral when requested==0)."""
    if requested:
        return requested
    try:
        for server in master.addons.get("proxyserver").servers:
            sockets = getattr(server, "listen_addrs", None)
            if sockets:
                return int(sockets[0][1])
    except Exception:  # noqa: BLE001
        pass
    return requested


def _harden_ca_key_perms(confdir: Path) -> None:
    """chmod 0600 the mitmproxy CA private key material in ``confdir``."""
    for name in ("mitmproxy-ca.pem", "mitmproxy-ca-key.pem"):
        target = confdir / name
        if target.is_file():
            try:
                os.chmod(target, 0o600)
            except OSError:
                logger.debug("could not chmod %s", name)
