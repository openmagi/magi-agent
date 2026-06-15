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
  easyCreateBot,
  easySendCode,
  easyVerify2fa,
  easyVerifyCode,
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
            easyAvailable={data.telegram.easy_available ?? false}
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
  easyAvailable,
  onChange,
}: {
  configured: boolean;
  label: string | null;
  easyAvailable: boolean;
  onChange: () => void;
}) {
  const agentFetch = useAgentFetch();
  const [mode, setMode] = useState<"advanced" | "easy">("advanced");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
      description="Run your agent as a Telegram bot."
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
        <div className="space-y-3">
          {easyAvailable && (
            <div className="flex gap-1 text-[11px]">
              <button
                onClick={() => setMode("easy")}
                className={`px-2.5 py-1 rounded-lg border transition-colors ${
                  mode === "easy"
                    ? "border-primary/40 text-primary bg-primary/10"
                    : "border-gray-200 text-secondary"
                }`}
              >
                Easy (phone)
              </button>
              <button
                onClick={() => setMode("advanced")}
                className={`px-2.5 py-1 rounded-lg border transition-colors ${
                  mode === "advanced"
                    ? "border-primary/40 text-primary bg-primary/10"
                    : "border-gray-200 text-secondary"
                }`}
              >
                Advanced (token)
              </button>
            </div>
          )}
          {easyAvailable && mode === "easy" ? (
            <TelegramEasyWizard onConnected={onChange} setError={setErr} />
          ) : (
            <TelegramAdvancedForm onConnected={onChange} setError={setErr} />
          )}
        </div>
      )}
    </SectionCard>
  );
}

function TelegramAdvancedForm({
  onConnected,
  setError,
}: {
  onConnected: () => void;
  setError: (v: string | null) => void;
}) {
  const agentFetch = useAgentFetch();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!token.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await setTelegramToken(agentFetch, token.trim());
      setToken("");
      onConnected();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save token");
    } finally {
      setBusy(false);
    }
  }

  return (
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
        Message{" "}
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
  );
}

function TelegramEasyWizard({
  onConnected,
  setError,
}: {
  onConnected: () => void;
  setError: (v: string | null) => void;
}) {
  const agentFetch = useAgentFetch();
  const [step, setStep] = useState<"phone" | "code" | "2fa" | "name">("phone");
  const [phone, setPhone] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [botName, setBotName] = useState("");
  const [busy, setBusy] = useState(false);

  function fail(e: unknown, fallback: string) {
    setError(e instanceof Error ? e.message : fallback);
  }

  async function sendCode() {
    if (!phone.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setSessionId(await easySendCode(agentFetch, phone.trim()));
      setStep("code");
    } catch (e) {
      fail(e, "Failed to send code");
    } finally {
      setBusy(false);
    }
  }

  async function verifyCode() {
    setBusy(true);
    setError(null);
    try {
      const needs2fa = await easyVerifyCode(agentFetch, sessionId, code.trim());
      setStep(needs2fa ? "2fa" : "name");
    } catch (e) {
      fail(e, "Failed to verify code");
    } finally {
      setBusy(false);
    }
  }

  async function verify2fa() {
    setBusy(true);
    setError(null);
    try {
      await easyVerify2fa(agentFetch, sessionId, password);
      setStep("name");
    } catch (e) {
      fail(e, "Failed to verify password");
    } finally {
      setBusy(false);
    }
  }

  async function createBot() {
    if (!botName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await easyCreateBot(agentFetch, sessionId, botName.trim());
      onConnected();
    } catch (e) {
      fail(e, "Failed to create bot");
    } finally {
      setBusy(false);
    }
  }

  const inputClass =
    "flex-1 text-xs px-3 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/40";
  const btnClass =
    "text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors disabled:opacity-40";

  return (
    <div className="space-y-2">
      {step === "phone" && (
        <div className="flex gap-2">
          <input
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+1 555 123 4567"
            className={inputClass}
          />
          <button onClick={sendCode} disabled={busy || !phone.trim()} className={btnClass}>
            {busy ? "Sending…" : "Send code"}
          </button>
        </div>
      )}
      {step === "code" && (
        <div className="flex gap-2">
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="Login code from Telegram"
            className={inputClass}
          />
          <button onClick={verifyCode} disabled={busy || !code.trim()} className={btnClass}>
            {busy ? "Verifying…" : "Verify"}
          </button>
        </div>
      )}
      {step === "2fa" && (
        <div className="flex gap-2">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="2FA password"
            className={inputClass}
          />
          <button onClick={verify2fa} disabled={busy || !password} className={btnClass}>
            {busy ? "Verifying…" : "Verify"}
          </button>
        </div>
      )}
      {step === "name" && (
        <div className="flex gap-2">
          <input
            value={botName}
            onChange={(e) => setBotName(e.target.value)}
            placeholder="Bot display name"
            className={inputClass}
          />
          <button onClick={createBot} disabled={busy || !botName.trim()} className={btnClass}>
            {busy ? "Creating…" : "Create bot"}
          </button>
        </div>
      )}
      <p className="text-[10px] text-secondary/70">
        Enter your phone number; we log in to Telegram and create the bot via
        @BotFather for you. Your login session is discarded afterwards.
      </p>
    </div>
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
