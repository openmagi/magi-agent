"use client";

import { useCallback, useEffect, useState } from "react";
import { useAgentFetch } from "./local-api";

/**
 * Types and data hook for the local runtime "Credentials" admin surface.
 *
 * Mirrors the `GET/POST /v1/admin/credentials` contract served by the local
 * Python runtime. Persistence is metadata-only: the plaintext secret is sent
 * once on registration, forwarded to a local vault seam, and never returned by
 * the runtime. The `secret` field is therefore write-only — it is never
 * populated from server data.
 */

export interface CredentialMetadata {
  id: string;
  service: string;
  label: string;
  auth_scheme: string;
  status: "pending" | "active" | "revoked";
  vault_ref: string | null;
  created_at: string;
}

export interface VaultStatus {
  present: boolean;
  healthy: boolean;
}

export interface CredentialsResponse {
  credentials: CredentialMetadata[];
  vault_status: VaultStatus;
}

export interface RegisterCredentialInput {
  service: string;
  label: string;
  auth_scheme: string;
  secret: string;
}

interface UseCredentialsResult {
  data: CredentialsResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Registers a credential via `POST /v1/admin/credentials`.
 *
 * The secret is sent once and dropped by the runtime; the response carries
 * only redacted metadata. Throws on non-2xx so the caller can surface the error.
 */
export async function registerCredential(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  input: RegisterCredentialInput,
): Promise<CredentialMetadata> {
  const res = await fetch("/v1/admin/credentials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Failed to register credential (${res.status})`);
  const data = (await res.json()) as { credential: CredentialMetadata };
  return data.credential;
}

/**
 * Revokes a credential via `POST /v1/admin/credentials/{id}/revoke`.
 */
export async function revokeCredential(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  credentialId: string,
): Promise<CredentialMetadata> {
  const res = await fetch(
    `/v1/admin/credentials/${encodeURIComponent(credentialId)}/revoke`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Failed to revoke credential (${res.status})`);
  const data = (await res.json()) as { credential: CredentialMetadata };
  return data.credential;
}

/**
 * Loads the credentials list + vault status from `/v1/admin/credentials`.
 */
export function useCredentials(): UseCredentialsResult {
  const agentFetch = useAgentFetch();
  const [data, setData] = useState<CredentialsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => {
    setReloadKey((value) => value + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    agentFetch("/v1/admin/credentials")
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            `Failed to load /v1/admin/credentials (${response.status})`,
          );
        }
        const payload = (await response.json()) as CredentialsResponse;
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Failed to load /v1/admin/credentials",
          );
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [agentFetch, reloadKey]);

  return { data, loading, error, reload };
}
