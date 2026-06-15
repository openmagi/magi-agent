"use client";

import { useCallback, useEffect, useState } from "react";
import { Cable, Loader2, Plug, Send } from "lucide-react";
import { CredentialsPanel } from "@/components/dashboard/credentials/credentials-panel";
import { useAgentFetch } from "@/lib/local-api";
import {
  clearComposioKey,
  clearTelegramToken,
  composioConnect,
  composioConnectStatus,
  composioDisconnect,
  fetchComposioCatalog,
  fetchComposioConnections,
  setComposioKey,
  setTelegramToken,
  useIntegrations,
  type CatalogItem,
  type ConnectionItem,
} from "@/lib/integrations-api";

interface IntegrationsManagerProps {
  botId?: string;
}

export function IntegrationsManager({ botId }: IntegrationsManagerProps) {
  const { data, loading, error, reload } = useIntegrations();

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-foreground">Integrations</h1>
        <p className="text-sm text-secondary mt-1">
          Connect third-party apps and channels. Secrets are stored in your local
          encrypted vault and never leave this machine.
        </p>
      </header>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-secondary">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </div>
      )}
      {error && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
          {error}
        </div>
      )}

      {data && !data.vault_status.present && (
        <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-500">
          The local vault is not enabled. Set <code>MAGI_LOCAL_VAULT_ENABLED=1</code>{" "}
          to store integration secrets.
        </div>
      )}

      {data && (
        <>
          <ComposioSection configured={data.composio.configured} onChange={reload} />
          <TelegramSection
            configured={data.telegram.configured}
            label={data.telegram.label}
            onChange={reload}
          />
        </>
      )}

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <Cable className="w-4 h-4 text-secondary" />
          <h2 className="text-sm font-medium text-foreground">Manual credentials</h2>
        </div>
        <p className="text-xs text-secondary">
          Raw API keys for services without a guided connector.
        </p>
        <CredentialsPanel botId={botId ?? "local"} />
      </section>
    </div>
  );
}

function SectionCard({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5 space-y-4">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-black/5 flex items-center justify-center shrink-0">
          {icon}
        </div>
        <div>
          <h2 className="text-sm font-medium text-foreground">{title}</h2>
          <p className="text-[11px] text-secondary">{description}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function ComposioSection({
  configured,
  onChange,
}: {
  configured: boolean;
  onChange: () => void;
}) {
  const agentFetch = useAgentFetch();
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [connections, setConnections] = useState<ConnectionItem[]>([]);
  const [connecting, setConnecting] = useState<string | null>(null);

  const refreshConnections = useCallback(() => {
    fetchComposioConnections(agentFetch)
      .then(setConnections)
      .catch(() => setConnections([]));
  }, [agentFetch]);

  const loadCatalog = useCallback(() => {
    setCatalogLoading(true);
    fetchComposioCatalog(agentFetch)
      .then((page) => setCatalog(page.items))
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : "Failed to load catalog"))
      .finally(() => setCatalogLoading(false));
  }, [agentFetch]);

  useEffect(() => {
    if (configured) {
      loadCatalog();
      refreshConnections();
    }
  }, [configured, loadCatalog, refreshConnections]);

  async function saveKey() {
    if (!apiKey.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await setComposioKey(agentFetch, apiKey.trim());
      setApiKey("");
      onChange();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to save key");
    } finally {
      setBusy(false);
    }
  }

  async function removeKey() {
    setBusy(true);
    try {
      await clearComposioKey(agentFetch);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  async function connect(slug: string) {
    setConnecting(slug);
    setErr(null);
    try {
      const result = await composioConnect(agentFetch, slug);
      if (result.redirect_url) {
        window.open(result.redirect_url, "_blank", "noopener,noreferrer");
      }
      await pollUntilActive(agentFetch, result.connection_id);
      refreshConnections();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to connect");
    } finally {
      setConnecting(null);
    }
  }

  async function disconnect(connectionId: string) {
    try {
      await composioDisconnect(agentFetch, connectionId);
      refreshConnections();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to disconnect");
    }
  }

  const connectedSlugs = new Set(connections.map((c) => c.toolkit));
  const filtered = query.trim()
    ? catalog.filter(
        (i) =>
          i.name.toLowerCase().includes(query.toLowerCase()) ||
          i.slug.toLowerCase().includes(query.toLowerCase()),
      )
    : catalog;

  return (
    <SectionCard
      icon={<Plug className="w-5 h-5 text-foreground" />}
      title="Composio apps"
      description="Connect Gmail, Slack, Notion, GitHub and 250+ apps via OAuth (BYO Composio API key)."
    >
      {err && (
        <div className="p-2.5 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
          {err}
        </div>
      )}

      {!configured ? (
        <div className="space-y-2">
          <p className="text-[11px] text-secondary">
            Paste your Composio API key from{" "}
            <a
              href="https://app.composio.dev/developers"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              app.composio.dev
            </a>
            .
          </p>
          <div className="flex gap-2">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="comp_..."
              className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/40"
            />
            <button
              onClick={saveKey}
              disabled={busy || !apiKey.trim()}
              className="text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors disabled:opacity-40"
            >
              {busy ? "Saving…" : "Save key"}
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search apps…"
              className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/40"
            />
            <button
              onClick={removeKey}
              disabled={busy}
              className="text-[11px] text-red-400 hover:text-red-300 transition-colors disabled:opacity-40 shrink-0"
            >
              Remove key
            </button>
          </div>

          {catalogLoading ? (
            <div className="flex items-center gap-2 text-xs text-secondary py-3">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading catalog…
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-80 overflow-y-auto">
              {filtered.map((item) => {
                const isConnected = connectedSlugs.has(item.slug);
                const conn = connections.find((c) => c.toolkit === item.slug);
                return (
                  <div
                    key={item.slug}
                    className="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-gray-100 bg-gray-50"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {item.logo && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={item.logo} alt="" className="w-5 h-5 rounded shrink-0" />
                      )}
                      <span className="text-xs text-foreground truncate">{item.name}</span>
                    </div>
                    {isConnected && conn ? (
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                          Connected
                        </span>
                        <button
                          onClick={() => disconnect(conn.connection_id)}
                          className="text-[10px] text-red-400 hover:text-red-300"
                        >
                          Disconnect
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => connect(item.slug)}
                        disabled={connecting === item.slug}
                        className="text-[11px] px-2.5 py-1 rounded-lg border border-primary/20 text-primary hover:bg-primary/10 transition-colors disabled:opacity-40 shrink-0"
                      >
                        {connecting === item.slug ? "Connecting…" : "Connect"}
                      </button>
                    )}
                  </div>
                );
              })}
              {filtered.length === 0 && (
                <p className="text-[11px] text-secondary/60 py-2 col-span-full">
                  No apps match “{query}”.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function TelegramSection({
  configured,
  label,
  onChange,
}: {
  configured: boolean;
  label: string | null;
  onChange: () => void;
}) {
  const agentFetch = useAgentFetch();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    if (!token.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await setTelegramToken(agentFetch, token.trim());
      setToken("");
      onChange();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to save token");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await clearTelegramToken(agentFetch);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <SectionCard
      icon={<Send className="w-5 h-5 text-foreground" />}
      title="Telegram bot"
      description="Run your agent as a Telegram bot. Paste a token from @BotFather."
    >
      {err && (
        <div className="p-2.5 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
          {err}
        </div>
      )}
      {configured ? (
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-xs text-foreground">
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
              Connected
            </span>
            {label}
          </span>
          <button
            onClick={remove}
            disabled={busy}
            className="text-[11px] text-red-400 hover:text-red-300 transition-colors disabled:opacity-40"
          >
            Disconnect
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex gap-2">
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="123456:ABC-DEF…"
              className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/40"
            />
            <button
              onClick={save}
              disabled={busy || !token.trim()}
              className="text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors disabled:opacity-40"
            >
              {busy ? "Connecting…" : "Connect"}
            </button>
          </div>
          <p className="text-[10px] text-secondary/70">
            Open Telegram, message{" "}
            <a
              href="https://t.me/BotFather"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              @BotFather
            </a>
            , send <code>/newbot</code>, and paste the token it gives you.
          </p>
        </div>
      )}
    </SectionCard>
  );
}

async function pollUntilActive(
  agentFetch: (path: string, init?: RequestInit) => Promise<Response>,
  connectionId: string,
): Promise<void> {
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const status = await composioConnectStatus(agentFetch, connectionId);
      if (status.status?.toUpperCase() === "ACTIVE") return;
    } catch {
      // transient — keep polling
    }
  }
}
