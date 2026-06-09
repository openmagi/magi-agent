"""Local "Credentials" registration admin surface for the OSS dashboard.

Metadata-only persistence with a default-OFF local vault seam. When the vault is
available, the plaintext secret is forwarded to the seam and dropped — never
logged, never raised, never persisted to durable storage. When the vault is not
available, registration is rejected and no metadata row is created.
"""

from __future__ import annotations

from magi_agent.credentials_admin import store, vault_local

__all__ = ["store", "vault_local"]
