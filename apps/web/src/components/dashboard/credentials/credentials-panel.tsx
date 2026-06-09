"use client";

import { useCallback, useState } from "react";
import { KeyRound, ShieldAlert, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { useAgentFetch } from "@/lib/local-api";
import {
  registerCredential,
  revokeCredential,
  useCredentials,
} from "@/lib/credentials-api";
import type {
  CredentialMetadata,
  VaultStatus,
} from "@/lib/credentials-api";

interface CredentialsPanelProps {
  botId: string;
}

const AUTH_SCHEMES = ["bearer", "api_key", "basic", "oauth2"] as const;

function statusVariant(
  status: CredentialMetadata["status"],
): "success" | "warning" | "error" | "default" {
  if (status === "active") return "success";
  if (status === "pending") return "warning";
  if (status === "revoked") return "error";
  return "default";
}

function VaultBanner({ status }: { status: VaultStatus }) {
  if (status.healthy) {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.06] px-4 py-3 text-sm text-emerald-800">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <div>
          <div className="font-semibold">Vault connected</div>
          <div className="mt-0.5 text-emerald-700/90">
            Secrets are stored in the provisioned vault. Only redacted metadata is
            kept on this device.
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-3 text-sm text-amber-800">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <div className="font-semibold">Vault not provisioned</div>
        <div className="mt-0.5 text-amber-700/90">
          A secure vault is not connected yet. Credential registration is disabled
          until this local vault is available.
        </div>
      </div>
    </div>
  );
}

export function CredentialsPanel({ botId }: CredentialsPanelProps): React.JSX.Element {
  const { data, loading, error, reload } = useCredentials();
  const agentFetch = useAgentFetch();
  const vaultReady = Boolean(data?.vault_status.present && data.vault_status.healthy);

  const [service, setService] = useState("");
  const [label, setLabel] = useState("");
  const [authScheme, setAuthScheme] = useState<string>(AUTH_SCHEMES[0]);
  // Write-only: this field is never populated from server data.
  const [secret, setSecret] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<Set<string>>(new Set());

  const canSubmit =
    service.trim() !== "" &&
    label.trim() !== "" &&
    authScheme.trim() !== "" &&
    secret !== "" &&
    vaultReady &&
    !submitting;

  const handleRegister = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      if (!vaultReady) {
        setFormError("Credential registration requires an available vault.");
        return;
      }
      if (!canSubmit) return;
      setSubmitting(true);
      setFormError(null);
      registerCredential(agentFetch, {
        service: service.trim(),
        label: label.trim(),
        auth_scheme: authScheme.trim(),
        secret,
      })
        .then(() => {
          // Clear the write-only secret immediately; never echo it back.
          setSecret("");
          setService("");
          setLabel("");
          reload();
        })
        .catch((err: unknown) => {
          setFormError(
            err instanceof Error ? err.message : "Failed to register credential",
          );
        })
        .finally(() => {
          setSubmitting(false);
        });
    },
    [agentFetch, authScheme, canSubmit, label, reload, secret, service, vaultReady],
  );

  const handleRevoke = useCallback(
    (id: string) => {
      setRevoking((prev) => new Set(prev).add(id));
      revokeCredential(agentFetch, id)
        .then(() => reload())
        .catch((err: unknown) => {
          setFormError(
            err instanceof Error ? err.message : "Failed to revoke credential",
          );
        })
        .finally(() => {
          setRevoking((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        });
    },
    [agentFetch, reload],
  );

  return (
    <div className="max-w-5xl space-y-6 pb-20">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-secondary/60">
          {botId ? `route: ${botId}` : "local"}
        </p>
        <h1 className="mt-2 text-2xl font-bold leading-tight text-foreground">
          Credentials
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
          Register the API keys and tokens your agent may use. Secrets are sent once
          to the local vault and never stored, returned, or logged here — only
          redacted metadata is kept.
        </p>
      </header>

      {data ? <VaultBanner status={data.vault_status} /> : null}

      <form
        onSubmit={handleRegister}
        className="rounded-2xl border border-black/[0.06] bg-white p-5 space-y-4"
      >
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">
            Register a credential
          </span>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Input
            label="Service"
            placeholder="openai"
            value={service}
            onChange={(e) => setService(e.target.value)}
            autoComplete="off"
            disabled={!vaultReady || submitting}
          />
          <Input
            label="Label"
            placeholder="Production key"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            autoComplete="off"
            disabled={!vaultReady || submitting}
          />
          <Select
            label="Auth scheme"
            value={authScheme}
            onChange={(e) => setAuthScheme(e.target.value)}
            disabled={!vaultReady || submitting}
          >
            {AUTH_SCHEMES.map((scheme) => (
              <option key={scheme} value={scheme}>
                {scheme}
              </option>
            ))}
          </Select>
        </div>
        <Input
          label="Secret (write-only)"
          type="password"
          placeholder="Paste the secret value"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          autoComplete="new-password"
          disabled={!vaultReady || submitting}
        />
        {formError ? (
          <div className="rounded-lg border border-red-500/20 bg-red-500/[0.06] px-3 py-2 text-sm text-red-700">
            {formError}
          </div>
        ) : null}
        <div>
          <Button type="submit" disabled={!canSubmit}>
            {submitting ? "Registering…" : "Register credential"}
          </Button>
        </div>
      </form>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-foreground">
          Registered credentials
        </h2>
        {loading ? (
          <div className="grid grid-cols-1 gap-3">
            <div className="h-16 animate-pulse rounded-2xl border border-black/[0.06] bg-gray-50" />
            <div className="h-16 animate-pulse rounded-2xl border border-black/[0.06] bg-gray-50" />
          </div>
        ) : error ? (
          <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-4 text-sm leading-6 text-amber-800">
            <div className="font-semibold">
              Could not load credentials from the local runtime.
            </div>
            <div className="mt-1">{error}</div>
            <button
              type="button"
              onClick={reload}
              className="mt-3 inline-flex min-h-[40px] items-center rounded-lg border border-amber-500/30 bg-white px-4 py-2 text-sm font-semibold text-amber-800 transition-colors hover:bg-amber-50"
            >
              Retry
            </button>
          </div>
        ) : data && data.credentials.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-black/[0.1] bg-gray-50/60 px-5 py-8 text-center text-sm text-secondary">
            No credentials registered yet.
          </div>
        ) : (
          <ul className="space-y-2">
            {data?.credentials.map((credential) => (
              <li
                key={credential.id}
                className="flex items-center justify-between gap-4 rounded-2xl border border-black/[0.06] bg-white px-5 py-4"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-foreground">
                      {credential.label}
                    </span>
                    <Badge variant={statusVariant(credential.status)}>
                      {credential.status}
                    </Badge>
                  </div>
                  <div className="mt-1 text-xs text-secondary">
                    {credential.service} · {credential.auth_scheme}
                  </div>
                </div>
                {credential.status !== "revoked" ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleRevoke(credential.id)}
                    disabled={revoking.has(credential.id)}
                  >
                    {revoking.has(credential.id) ? "Revoking…" : "Revoke"}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
