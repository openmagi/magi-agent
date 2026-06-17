"use client";

import { useCallback, useEffect, useState } from "react";
import { useAgentFetch } from "@/lib/local-api";
import { GlassCard } from "@/components/ui/glass-card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";

// ---------------------------------------------------------------------------
// Types matching the GET /v1/app/providers response shape.
// The server NEVER returns key values — configured is a bool only.
// ---------------------------------------------------------------------------

interface ProviderRow {
  name: string;
  configured: boolean;
  model: string;
  envVar: string | null;
}

interface ProvidersSnapshot {
  providers: ProviderRow[];
  active: string | null;
}

// All five providers the local runtime supports (incl. openrouter which
// settings-form.tsx omits).
const SUPPORTED_PROVIDER_NAMES = [
  "anthropic",
  "openai",
  "gemini",
  "fireworks",
  "openrouter",
] as const;

type SupportedProvider = (typeof SUPPORTED_PROVIDER_NAMES)[number];

const PROVIDER_LABELS: Record<SupportedProvider | string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Gemini",
  fireworks: "Fireworks",
  openrouter: "OpenRouter",
};

// State per row: the write-only key draft (never populated from server).
interface RowDraft {
  keyDraft: string;
}

type DraftMap = Record<string, RowDraft>;

// ---------------------------------------------------------------------------

interface ProviderKeysFormProps {
  bot?: null;
}

export function ProviderKeysForm(_props: ProviderKeysFormProps) {
  const agentFetch = useAgentFetch();
  const t = useMessages();

  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [drafts, setDrafts] = useState<DraftMap>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadSnapshot = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await agentFetch("/v1/app/providers");
      if (!res.ok) throw new Error("Failed to load provider keys");
      const data = (await res.json()) as ProvidersSnapshot;
      setRows(data.providers ?? []);
      // Reset key drafts — never populate from server response.
      setDrafts({});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load provider keys");
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  function setKeyDraft(name: string, value: string) {
    setDrafts((prev) => ({ ...prev, [name]: { keyDraft: value } }));
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      // Only include providers whose key field is non-empty.
      // Blank entries are omitted entirely so the server leaves the
      // existing key untouched — we never send an empty key by accident.
      const providersPayload: Record<string, { apiKey: string }> = {};
      for (const row of rows) {
        const draft = drafts[row.name];
        const key = draft?.keyDraft?.trim() ?? "";
        if (key.length > 0) {
          providersPayload[row.name] = { apiKey: key };
        }
      }

      const res = await agentFetch("/v1/app/providers", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providers: providersPayload }),
      });

      const data = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) throw new Error(data.error ?? "Failed to save provider keys");

      // Reload snapshot so configured badges update; clear drafts.
      await loadSnapshot();
      setSuccess(t.settingsPage?.saveSuccess ?? "Settings saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-4">
      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-500">
          {error}
        </div>
      )}
      {success && (
        <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700">
          {success}
        </div>
      )}

      <GlassCard>
        <div className="mb-5 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Models &amp; Keys</h2>
            <p className="mt-1 text-sm text-secondary">
              Set API keys for each provider. Keys are stored only in your local{" "}
              <code className="rounded bg-gray-100 px-1 text-xs">~/.magi/config.toml</code> and are
              never returned to the browser.
            </p>
          </div>
          {loading && (
            <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-semibold text-secondary">
              Loading
            </span>
          )}
        </div>

        <div className="space-y-6">
          {rows.map((row) => {
            const label = PROVIDER_LABELS[row.name] ?? row.name;
            const configured = row.configured;
            const placeholder = configured
              ? "Saved — leave blank to keep"
              : row.envVar
                ? `Enter key or set ${row.envVar}`
                : "sk-…";

            return (
              <div key={row.name} className="rounded-xl border border-black/5 bg-white/40 p-4">
                <div className="mb-3 flex items-center gap-3">
                  <span className="text-sm font-semibold text-foreground">{label}</span>
                  {configured ? (
                    <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-700">
                      Configured ✓
                    </span>
                  ) : (
                    <span className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-secondary">
                      Not set
                    </span>
                  )}
                  {row.envVar && (
                    <span className="ml-auto text-xs text-muted">
                      env: <code className="rounded bg-gray-100 px-1">{row.envVar}</code>
                    </span>
                  )}
                </div>

                <div className="space-y-3">
                  <Input
                    label="API Key"
                    type="password"
                    value={drafts[row.name]?.keyDraft ?? ""}
                    onChange={(event) => setKeyDraft(row.name, event.target.value)}
                    placeholder={placeholder}
                  />
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted">Model:</span>
                    <span className="text-xs font-medium text-secondary">{row.model}</span>
                  </div>
                </div>
              </div>
            );
          })}

          {!loading && rows.length === 0 && (
            <p className="text-sm text-secondary">No providers available.</p>
          )}
        </div>
      </GlassCard>

      <Button variant="cta" size="md" onClick={handleSave} disabled={saving || loading}>
        {saving ? (t.settingsPage?.saving ?? "Saving…") : (t.settingsPage?.save ?? "Save Settings")}
      </Button>
    </div>
  );
}
