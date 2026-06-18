"""C-8: the mitmproxy CA private-key hardening must cover every key-bearing file.

The local-proxy hardening helper historically hardened only ``mitmproxy-ca.pem``
and ``mitmproxy-ca-key.pem`` while the vault-server copy also hardened
``mitmproxy-ca.p12`` (which contains the private key). That divergence left the
``.p12`` bundle world-readable on the local-proxy path. Both helpers must harden
the same superset of files to 0o600.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from magi_agent.credentials_admin.local_proxy import (
    _harden_ca_key_perms as harden_local_proxy,
)
from magi_agent.credentials_admin.vault_server import (
    _harden_ca_key_perms as harden_vault_server,
)

_CA_KEY_FILES = (
    "mitmproxy-ca.pem",
    "mitmproxy-ca-key.pem",
    "mitmproxy-ca.p12",
)


def _make_world_readable_ca_dir(tmp_path: Path) -> Path:
    confdir = tmp_path / "mitm"
    confdir.mkdir()
    for name in _CA_KEY_FILES:
        target = confdir / name
        target.write_bytes(b"key-material")
        os.chmod(target, 0o644)
    return confdir


@pytest.mark.parametrize("harden", [harden_local_proxy, harden_vault_server])
def test_harden_ca_key_perms_covers_p12(harden, tmp_path: Path) -> None:
    confdir = _make_world_readable_ca_dir(tmp_path)

    harden(confdir)

    for name in _CA_KEY_FILES:
        mode = stat.S_IMODE((confdir / name).stat().st_mode)
        assert mode == 0o600, f"{name} not hardened to 0o600 (got {oct(mode)})"
