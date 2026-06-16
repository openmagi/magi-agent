"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Loader2,
  Lock,
  Plug,
  Search,
  Send,
} from "lucide-react";
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

const TG_BLUE = "#2AABEE";

// Display-only preview so users can browse/search before adding a Composio key.
// Connecting always goes through the live catalog (which requires the key), so
// these slugs are never used to initiate a connection.
const POPULAR_APPS: { slug: string; name: string; category: string }[] = [
  { slug: "gmail", name: "Gmail", category: "Email" },
  { slug: "googlecalendar", name: "Google Calendar", category: "Calendar" },
  { slug: "googledrive", name: "Google Drive", category: "Storage" },
  { slug: "googlesheets", name: "Google Sheets", category: "Productivity" },
  { slug: "googledocs", name: "Google Docs", category: "Productivity" },
  { slug: "slack", name: "Slack", category: "Comms" },
  { slug: "notion", name: "Notion", category: "Productivity" },
  { slug: "github", name: "GitHub", category: "Dev" },
  { slug: "gitlab", name: "GitLab", category: "Dev" },
  { slug: "linear", name: "Linear", category: "Dev" },
  { slug: "jira", name: "Jira", category: "Dev" },
  { slug: "asana", name: "Asana", category: "Productivity" },
  { slug: "trello", name: "Trello", category: "Productivity" },
  { slug: "clickup", name: "ClickUp", category: "Productivity" },
  { slug: "hubspot", name: "HubSpot", category: "CRM" },
  { slug: "salesforce", name: "Salesforce", category: "CRM" },
  { slug: "discord", name: "Discord", category: "Comms" },
  { slug: "twitter", name: "X (Twitter)", category: "Social" },
  { slug: "linkedin", name: "LinkedIn", category: "Social" },
  { slug: "reddit", name: "Reddit", category: "Social" },
  { slug: "youtube", name: "YouTube", category: "Social" },
  { slug: "airtable", name: "Airtable", category: "Productivity" },
  { slug: "stripe", name: "Stripe", category: "Payments" },
  { slug: "shopify", name: "Shopify", category: "Commerce" },
  { slug: "intercom", name: "Intercom", category: "Support" },
  { slug: "zendesk", name: "Zendesk", category: "Support" },
  { slug: "dropbox", name: "Dropbox", category: "Storage" },
  { slug: "onedrive", name: "OneDrive", category: "Storage" },
  { slug: "outlook", name: "Outlook", category: "Email" },
  { slug: "microsoft_teams", name: "Microsoft Teams", category: "Comms" },
  { slug: "figma", name: "Figma", category: "Design" },
  { slug: "calendly", name: "Calendly", category: "Calendar" },
  { slug: "zoom", name: "Zoom", category: "Comms" },
  { slug: "twilio", name: "Twilio", category: "Comms" },
  { slug: "sendgrid", name: "SendGrid", category: "Marketing" },
  { slug: "mailchimp", name: "Mailchimp", category: "Marketing" },
];

const COUNTRY_CODES: { code: string; flag: string }[] = [
  { code: "+1", flag: "🇺🇸" },
  { code: "+44", flag: "🇬🇧" },
  { code: "+82", flag: "🇰🇷" },
  { code: "+81", flag: "🇯🇵" },
  { code: "+86", flag: "🇨🇳" },
  { code: "+91", flag: "🇮🇳" },
  { code: "+49", flag: "🇩🇪" },
  { code: "+33", flag: "🇫🇷" },
  { code: "+34", flag: "🇪🇸" },
  { code: "+39", flag: "🇮🇹" },
  { code: "+55", flag: "🇧🇷" },
  { code: "+61", flag: "🇦🇺" },
  { code: "+65", flag: "🇸🇬" },
  { code: "+852", flag: "🇭🇰" },
  { code: "+971", flag: "🇦🇪" },
  { code: "+7", flag: "🇷🇺" },
];

const INPUT =
  "flex-1 text-xs px-3 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/40";

export function IntegrationsManager() {
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
  const keyInputRef = useRef<HTMLInputElement>(null);

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

  function promptForKey() {
    setErr("Add your Composio API key above to connect this app.");
    keyInputRef.current?.focus();
  }

  const connectedSlugs = new Set(connections.map((c) => c.toolkit));
  const q = query.trim().toLowerCase();

  const liveFiltered = q
    ? catalog.filter(
        (i) => i.name.toLowerCase().includes(q) || i.slug.toLowerCase().includes(q),
      )
    : catalog;
  const previewFiltered = q
    ? POPULAR_APPS.filter(
        (a) => a.name.toLowerCase().includes(q) || a.slug.toLowerCase().includes(q),
      )
    : POPULAR_APPS;

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

      {/* Key row — always present so it can be added/removed any time */}
      <div className="space-y-2">
        {!configured ? (
          <>
            <p className="text-[11px] text-secondary">
              Paste your Composio API key from{" "}
              <a
                href="https://app.composio.dev/developers"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                app.composio.dev
              </a>{" "}
              to connect apps and browse all 250+.
            </p>
            <div className="flex gap-2">
              <input
                ref={keyInputRef}
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="comp_..."
                className={INPUT}
              />
              <button
                onClick={saveKey}
                disabled={busy || !apiKey.trim()}
                className="text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors disabled:opacity-40"
              >
                {busy ? "Saving…" : "Save key"}
              </button>
            </div>
          </>
        ) : (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
              Key saved
            </span>
            <button
              onClick={removeKey}
              disabled={busy}
              className="text-[11px] text-red-400 hover:text-red-300 transition-colors disabled:opacity-40"
            >
              Remove key
            </button>
          </div>
        )}
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="w-3.5 h-3.5 text-secondary absolute left-2.5 top-1/2 -translate-y-1/2" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={configured ? "Search apps…" : "Search connectable apps…"}
          className="w-full text-xs pl-8 pr-3 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/40"
        />
      </div>

      {/* Grid */}
      {configured ? (
        catalogLoading ? (
          <div className="flex items-center gap-2 text-xs text-secondary py-3">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading catalog…
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-80 overflow-y-auto">
            {liveFiltered.map((item) => {
              const isConnected = connectedSlugs.has(item.slug);
              const conn = connections.find((c) => c.toolkit === item.slug);
              return (
                <AppRow
                  key={item.slug}
                  name={item.name}
                  logo={item.logo}
                  right={
                    isConnected && conn ? (
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
                    )
                  }
                />
              );
            })}
            {liveFiltered.length === 0 && (
              <p className="text-[11px] text-secondary/60 py-2 col-span-full">
                No apps match “{query}”.
              </p>
            )}
          </div>
        )
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-80 overflow-y-auto">
          {previewFiltered.map((app) => (
            <AppRow
              key={app.slug}
              name={app.name}
              category={app.category}
              right={
                <button
                  onClick={promptForKey}
                  className="flex items-center gap-1 text-[10px] text-secondary hover:text-foreground transition-colors shrink-0"
                >
                  <Lock className="w-3 h-3" /> Key needed
                </button>
              }
            />
          ))}
          {previewFiltered.length === 0 && (
            <p className="text-[11px] text-secondary/60 py-2 col-span-full">
              No popular apps match “{query}”. Add your API key to search all 250+.
            </p>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function AppRow({
  name,
  logo,
  category,
  right,
}: {
  name: string;
  logo?: string | null;
  category?: string;
  right: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-gray-100 bg-gray-50">
      <div className="flex items-center gap-2 min-w-0">
        {logo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={logo} alt="" className="w-5 h-5 rounded shrink-0" />
        ) : (
          <div className="w-5 h-5 rounded bg-black/5 flex items-center justify-center text-[9px] font-semibold text-secondary shrink-0">
            {name.charAt(0)}
          </div>
        )}
        <span className="text-xs text-foreground truncate">{name}</span>
        {category && (
          <span className="text-[9px] text-secondary/60 shrink-0">{category}</span>
        )}
      </div>
      {right}
    </div>
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
  const [mode, setMode] = useState<"advanced" | "easy">(easyAvailable ? "easy" : "advanced");
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
      icon={<Send className="w-5 h-5" style={{ color: TG_BLUE }} />}
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
              <ModeTab active={mode === "easy"} onClick={() => setMode("easy")}>
                Easy (phone)
              </ModeTab>
              <ModeTab active={mode === "advanced"} onClick={() => setMode("advanced")}>
                Advanced (token)
              </ModeTab>
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

function ModeTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 rounded-lg border transition-colors ${
        active ? "text-white" : "border-gray-200 text-secondary"
      }`}
      style={active ? { backgroundColor: TG_BLUE, borderColor: TG_BLUE } : undefined}
    >
      {children}
    </button>
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
  const [copied, setCopied] = useState(false);

  function copyNewbot() {
    navigator.clipboard.writeText("/newbot").then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

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
    <div className="space-y-3">
      <div className="rounded-xl border border-gray-100 bg-gray-50 p-3 space-y-2">
        <p className="text-xs font-medium text-foreground">How to get a token</p>
        <a
          href="https://t.me/BotFather?start"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2.5 px-3 py-2 rounded-lg transition-colors"
          style={{ backgroundColor: `${TG_BLUE}1A`, border: `1px solid ${TG_BLUE}33` }}
        >
          <span className="text-base">🤖</span>
          <span className="flex-1 min-w-0">
            <span className="block text-xs font-semibold" style={{ color: TG_BLUE }}>
              Open @BotFather
            </span>
            <span className="block text-[10px] text-secondary">
              Telegram&apos;s official bot for creating bots
            </span>
          </span>
          <span className="text-secondary text-xs">↗</span>
        </a>
        <ol className="list-decimal list-inside space-y-1 text-[11px] text-secondary">
          <li className="flex items-center gap-1.5 flex-wrap">
            <span>Send</span>
            <button
              type="button"
              onClick={copyNewbot}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/5 border border-black/10 hover:bg-black/10 font-mono text-primary"
            >
              /newbot
              <span className="text-[9px] text-secondary">
                {copied ? "copied" : "tap to copy"}
              </span>
            </button>
            <span>and follow the prompts</span>
          </li>
          <li>Choose a name and a username ending in “bot”</li>
          <li>Copy the token BotFather sends back and paste it below</li>
        </ol>
      </div>

      <div className="flex gap-2">
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="123456789:ABC-DEF…"
          className={`${INPUT} font-mono`}
        />
        <button
          onClick={save}
          disabled={busy || !token.trim()}
          className="text-xs px-3 py-1.5 rounded-lg text-white transition-colors disabled:opacity-40"
          style={{ backgroundColor: TG_BLUE }}
        >
          {busy ? "Connecting…" : "Connect"}
        </button>
      </div>
    </div>
  );
}

type EasyStep = "phone" | "code" | "2fa" | "name" | "creating";

function TelegramEasyWizard({
  onConnected,
  setError,
}: {
  onConnected: () => void;
  setError: (v: string | null) => void;
}) {
  const agentFetch = useAgentFetch();
  const [step, setStep] = useState<EasyStep>("phone");
  const [countryCode, setCountryCode] = useState("+1");
  const [phone, setPhone] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [botName, setBotName] = useState("");
  const [busy, setBusy] = useState(false);
  const [resendTimer, setResendTimer] = useState(0);

  useEffect(() => {
    if (resendTimer <= 0) return;
    const t = setTimeout(() => setResendTimer((r) => r - 1), 1000);
    return () => clearTimeout(t);
  }, [resendTimer]);

  function fail(e: unknown, fallback: string) {
    setError(e instanceof Error ? e.message : fallback);
  }

  const fullPhone = `${countryCode}${phone.replace(/\D/g, "")}`;

  async function sendCode() {
    if (!phone.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setSessionId(await easySendCode(agentFetch, fullPhone));
      setStep("code");
      setResendTimer(60);
    } catch (e) {
      fail(e, "Failed to send code");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true);
    setError(null);
    try {
      setSessionId(await easySendCode(agentFetch, fullPhone));
      setResendTimer(60);
      setCode("");
    } catch (e) {
      fail(e, "Failed to resend code");
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
    setStep("creating");
    setError(null);
    try {
      await easyCreateBot(agentFetch, sessionId, botName.trim());
      onConnected();
    } catch (e) {
      fail(e, "Failed to create bot");
      setStep("name");
    }
  }

  const tgBtn = "text-xs px-3 py-1.5 rounded-lg text-white transition-colors disabled:opacity-40";

  if (step === "creating") {
    return (
      <div className="flex flex-col items-center gap-2 py-6">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: TG_BLUE }} />
        <p className="text-xs text-foreground">Creating your bot via @BotFather…</p>
        <p className="text-[10px] text-secondary">This takes a few seconds.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-secondary/80">
        We log in to your Telegram account to create the bot via @BotFather. Your
        login session is discarded right after — only the bot token is kept.
      </p>

      {step === "phone" && (
        <div className="flex gap-2">
          <select
            value={countryCode}
            onChange={(e) => setCountryCode(e.target.value)}
            className="w-20 text-xs px-2 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground focus:outline-none focus:border-primary/40 cursor-pointer"
          >
            {COUNTRY_CODES.map((c) => (
              <option key={c.code} value={c.code}>
                {c.flag} {c.code}
              </option>
            ))}
          </select>
          <input
            type="tel"
            inputMode="numeric"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="10 1234 5678"
            className={INPUT}
          />
          <button onClick={sendCode} disabled={busy || !phone.trim()} className={tgBtn} style={{ backgroundColor: TG_BLUE }}>
            {busy ? "Sending…" : "Send code"}
          </button>
        </div>
      )}

      {step === "code" && (
        <>
          <p className="text-[10px] text-secondary">
            Enter the code Telegram sent to <strong>{fullPhone}</strong>.
          </p>
          <div className="flex gap-2">
            <input
              inputMode="numeric"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="12345"
              className={`${INPUT} text-center tracking-[0.3em] font-mono`}
              autoComplete="one-time-code"
            />
            <button onClick={verifyCode} disabled={busy || code.length < 3} className={tgBtn} style={{ backgroundColor: TG_BLUE }}>
              {busy ? "Verifying…" : "Verify"}
            </button>
          </div>
          <div className="flex justify-between items-center">
            <button
              onClick={() => { setStep("phone"); setError(null); }}
              className="flex items-center gap-1 text-[10px] text-secondary hover:text-foreground"
            >
              <ArrowLeft className="w-3 h-3" /> Back
            </button>
            <button
              onClick={resend}
              disabled={resendTimer > 0 || busy}
              className="text-[10px] disabled:text-gray-400"
              style={resendTimer > 0 ? undefined : { color: TG_BLUE }}
            >
              {resendTimer > 0 ? `Resend (${resendTimer}s)` : "Resend code"}
            </button>
          </div>
        </>
      )}

      {step === "2fa" && (
        <>
          <p className="text-[10px] text-secondary">
            Your account has two-step verification. Enter your password.
          </p>
          <div className="flex gap-2">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="2FA password"
              className={INPUT}
              autoComplete="current-password"
            />
            <button onClick={verify2fa} disabled={busy || !password} className={tgBtn} style={{ backgroundColor: TG_BLUE }}>
              {busy ? "Verifying…" : "Verify"}
            </button>
          </div>
          <button
            onClick={() => { setStep("phone"); setError(null); }}
            className="flex items-center gap-1 text-[10px] text-secondary hover:text-foreground"
          >
            <ArrowLeft className="w-3 h-3" /> Back
          </button>
        </>
      )}

      {step === "name" && (
        <>
          <p className="text-[10px] text-secondary">
            Name your bot. BotFather assigns a unique username automatically.
          </p>
          <div className="flex gap-2">
            <input
              value={botName}
              onChange={(e) => setBotName(e.target.value.slice(0, 64))}
              placeholder="My Magi Agent"
              className={INPUT}
              onKeyDown={(e) => {
                if (e.key === "Enter" && botName.trim()) createBot();
              }}
            />
            <button onClick={createBot} disabled={busy || !botName.trim()} className={tgBtn} style={{ backgroundColor: TG_BLUE }}>
              Create bot
            </button>
          </div>
        </>
      )}
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
