"""Local "Credentials" registration admin surface for the OSS dashboard.

Metadata-only persistence with a default-OFF local vault seam. The plaintext
secret is forwarded to the seam and dropped — never logged, never raised, never
persisted to durable storage.
"""

from __future__ import annotations

from magi_agent.credentials_admin import store, vault_local

__all__ = ["store", "vault_local"]
