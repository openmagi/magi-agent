"use client";

import { useCallback, useEffect, useState } from "react";
import { useAgentFetch } from "@/lib/local-api";
import { GlassCard } from "@/components/ui/glass-card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";

type ProviderName = "anthropic" | "openai" | "gemini" | "fireworks";

interface SettingsFormProps {
  bot?: null;
}

interface AppConfigPayload {
  ok?: boolean;
  exists?: boolean;
  config?: {
    llm?: {
      provider?: string;
      model?: string;
      baseUrl?: string;
      apiKeySet?: boolean;
      apiKeyEnvVar?: string;
      capabilities?: Record<string, unknown>;
    };
    server?: {
      gatewayTokenSet?: boolean;
      gatewayTokenEnvVar?: string;
    };
    workspace?: string;
  } | null;
}

const PROVIDER_OPTIONS: Array<{ value: ProviderName; label: string }> = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Gemini" },
  { value: "fireworks", label: "Fireworks" },
];

function isProviderName(value: string): value is ProviderName {
  return PROVIDER_OPTIONS.some((option) => option.value === value);
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumberString(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

export function SettingsForm(_props: SettingsFormProps) {
  const agentFetch = useAgentFetch();
  const t = useMessages();

  const [provider, setProvider] = useState<ProviderName>("anthropic");
  const [model, setModel] = useState("claude-sonnet-4-6");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeySet, setApiKeySet] = useState(false);
  const [apiKeyEnvVar, setApiKeyEnvVar] = useState("");
  const [gatewayTokenEnvVar, setGatewayTokenEnvVar] = useState("");
  const [workspacePath, setWorkspacePath] = useState("./workspace");
  const [contextWindow, setContextWindow] = useState("");
  const [maxOutputTokens, setMaxOutputTokens] = useState("");
  const [supportsThinking, setSupportsThinking] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await agentFetch("/v1/app/config");
      if (!res.ok) throw new Error("Failed to load settings");
      const data = (await res.json()) as AppConfigPayload;
      const llm = data.config?.llm;
      const server = data.config?.server;
      const nextProvider = asString(llm?.provider);
      if (isProviderName(nextProvider)) setProvider(nextProvider);
      setModel(asString(llm?.model) || "claude-sonnet-4-6");
      setBaseUrl(asString(llm?.baseUrl));
      setApiKey("");
      setApiKeySet(Boolean(llm?.apiKeySet));
      setApiKeyEnvVar(asString(llm?.apiKeyEnvVar));
      setGatewayTokenEnvVar(asString(server?.gatewayTokenEnvVar));
      setWorkspacePath(asString(data.config?.workspace) || "./workspace");
      setContextWindow(asNumberString(llm?.capabilities?.contextWindow));
      setMaxOutputTokens(asNumberString(llm?.capabilities?.maxOutputTokens));
      setSupportsThinking(llm?.capabilities?.supportsThinking === true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      const capabilities: Record<string, number | boolean> = {};
      const parsedContextWindow = Number(contextWindow);
      const parsedMaxOutputTokens = Number(maxOutputTokens);
      if (Number.isFinite(parsedContextWindow) && parsedContextWindow > 0) {
        capabilities.contextWindow = parsedContextWindow;
      }
      if (Number.isFinite(parsedMaxOutputTokens) && parsedMaxOutputTokens > 0) {
        capabilities.maxOutputTokens = parsedMaxOutputTokens;
      }
      capabilities.supportsThinking = supportsThinking;

      const res = await agentFetch("/v1/app/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          llm: {
            provider,
            model,
            baseUrl: baseUrl.trim() || undefined,
            apiKey: apiKey.trim() || undefined,
            apiKeyEnvVar: apiKeyEnvVar.trim() || undefined,
            capabilities,
          },
          server: {
            gatewayTokenEnvVar: gatewayTokenEnvVar.trim() || undefined,
          },
          workspace: workspacePath.trim() || "./workspace",
        }),
      });

      const data = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) throw new Error(data.error || "Failed to save settings");

      setApiKey("");
      setApiKeySet(apiKey.trim().length > 0 || apiKeySet);
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
            <h2 className="text-lg font-semibold text-foreground">Local Runtime</h2>
            <p className="mt-1 text-sm text-secondary">
              Configure the self-hosted agent provider, model, token source, and workspace path.
            </p>
          </div>
          {loading && (
            <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-semibold text-secondary">
              Loading
            </span>
          )}
        </div>

        <div className="space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-secondary">Provider</span>
            <select
              value={provider}
              onChange={(event) => setProvider(event.target.value as ProviderName)}
              className="min-h-11 w-full cursor-pointer rounded-xl border border-black/10 bg-white px-4 py-3 text-sm font-medium text-foreground outline-none transition-colors focus:border-primary/45 focus:ring-4 focus:ring-primary/10"
            >
              {PROVIDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <Input
            label="Model"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="claude-sonnet-4-6, gpt-4.1, llama3.1"
          />

          <Input
            label="Base URL"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="http://127.0.0.1:11434/v1"
          />

          <div className="border-t border-gray-200 pt-4">
            <Input
              label="API Key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={apiKeySet ? "Saved. Leave blank to keep current key." : "sk-..."}
            />
            <p className="mt-1.5 text-xs text-muted">
              Raw keys are written only to your local `magi-agent.yaml` and are never returned to the browser.
            </p>
          </div>

          <Input
            label="API key env var"
            value={apiKeyEnvVar}
            onChange={(event) => setApiKeyEnvVar(event.target.value)}
            placeholder="ANTHROPIC_API_KEY"
          />

          <Input
            label="Workspace Path"
            value={workspacePath}
            onChange={(event) => setWorkspacePath(event.target.value)}
            placeholder="./workspace"
          />
        </div>
      </GlassCard>

      <GlassCard>
        <h2 className="mb-4 text-lg font-semibold text-foreground">Advanced</h2>
        <div className="space-y-4">
          <Input
            label="Gateway token env var"
            value={gatewayTokenEnvVar}
            onChange={(event) => setGatewayTokenEnvVar(event.target.value)}
            placeholder="MAGI_AGENT_SERVER_TOKEN"
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Context window"
              value={contextWindow}
              onChange={(event) => setContextWindow(event.target.value)}
              placeholder="131072"
            />
            <Input
              label="Max output tokens"
              value={maxOutputTokens}
              onChange={(event) => setMaxOutputTokens(event.target.value)}
              placeholder="8192"
            />
          </div>
          <label className="flex cursor-pointer items-center gap-3 text-sm text-secondary">
            <input
              type="checkbox"
              checked={supportsThinking}
              onChange={(event) => setSupportsThinking(event.target.checked)}
              className="h-4 w-4 rounded border-black/10"
            />
            Model supports thinking blocks
          </label>
        </div>
      </GlassCard>

      <Button variant="cta" size="md" onClick={handleSave} disabled={saving || loading}>
        {saving ? t.settingsPage.saving : t.settingsPage.save}
      </Button>
    </div>
  );
}
