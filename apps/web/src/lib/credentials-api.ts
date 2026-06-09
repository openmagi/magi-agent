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
  requires_approval: boolean;
  created_at: string;
}

export type ApprovalStatus = "pending" | "approved" | "denied" | "expired";

export interface ApprovalRequest {
  id: string;
  credential_id: string;
  requested_action: string;
  target_host: string;
  status: ApprovalStatus;
  reason: string;
  created_at: string;
  decided_at: string | null;
}

export interface ApprovalsResponse {
  approvals: ApprovalRequest[];
}

export type ApprovalDecision = "approved" | "denied";

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
  requires_approval?: boolean;
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

/**
 * Records an operator decision via `POST /v1/admin/credentials/approvals/{id}`.
 *
 * The approval record is metadata only — it never carries a secret. The local
 * runtime updates the status + decided_at and forwards the decision to the
 * (default-OFF) vault seam.
 */
export async function decideApproval(
  fetch: (path: string, init?: RequestInit) => Promise<Response>,
  approvalId: string,
  decision: ApprovalDecision,
): Promise<ApprovalRequest> {
  const res = await fetch(
    `/v1/admin/credentials/approvals/${encodeURIComponent(approvalId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    },
  );
  if (!res.ok) throw new Error(`Failed to decide approval (${res.status})`);
  const data = (await res.json()) as { approval: ApprovalRequest };
  return data.approval;
}

interface UseApprovalsResult {
  data: ApprovalsResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Loads the pending approval queue from `/v1/admin/credentials/approvals`.
 */
export function usePendingApprovals(): UseApprovalsResult {
  const agentFetch = useAgentFetch();
  const [data, setData] = useState<ApprovalsResponse | null>(null);
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

    agentFetch("/v1/admin/credentials/approvals?status=pending")
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            `Failed to load approvals (${response.status})`,
          );
        }
        const payload = (await response.json()) as ApprovalsResponse;
        if (!cancelled) {
          setData(payload);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load approvals",
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
