"use client";

import { useState, useEffect, useCallback } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { GlassCard } from "@/components/ui/glass-card";
import { useMessages } from "@/lib/i18n";
import { SocialBrowserConnect } from "./social-browser-connect";

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      className={`w-4 h-4 text-secondary transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
    >
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
    </svg>
  );
}

interface IntegrationsPanelProps {
  botId?: string;
}

export function IntegrationsPanel({ botId }: IntegrationsPanelProps) {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const [open, setOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Google Ads Developer Token
  const [adsDevToken, setAdsDevToken] = useState("");
  const [adsDevTokenSaved, setAdsDevTokenSaved] = useState(false);
  const [adsDevTokenSaving, setAdsDevTokenSaving] = useState(false);

  // Zapier MCP URL
  const [zapierMcpUrl, setZapierMcpUrl] = useState("");
  const [zapierMcpUrlSaved, setZapierMcpUrlSaved] = useState(false);
  const [zapierMcpUrlSaving, setZapierMcpUrlSaving] = useState(false);

  // Check if dev token already saved on mount
  useEffect(() => {
    if (!botId) return;
    authFetch(`/api/bots/${botId}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.has_google_ads_dev_token) setAdsDevTokenSaved(true);
        if (data.has_zapier_mcp_url) setZapierMcpUrlSaved(true);
      })
      .catch(() => {});
  }, [botId, authFetch]);

  // Google
  const [googleStatus, setGoogleStatus] = useState<"loading" | "none" | "approved" | "active">("loading");
  const [googleEmail, setGoogleEmail] = useState<string | null>(null);
  const [googleScopes, setGoogleScopes] = useState<string[] | null>(null);
  const [googleActionLoading, setGoogleActionLoading] = useState(false);

  // Google Picker — selected files
  const [googleFiles, setGoogleFiles] = useState<Array<{
    file_id: string; name: string; mime_type: string; icon_url: string | null;
  }>>([]);
  const [pickerLoading, setPickerLoading] = useState(false);

  const fetchGoogleFiles = useCallback(() => {
    authFetch("/api/integrations/google/files")
      .then((res) => res.ok ? res.json() : { files: [] })
      .then((data) => setGoogleFiles(data.files ?? []))
      .catch(() => {});
  }, [authFetch]);

  // Notion
  const [notionStatus, setNotionStatus] = useState<"loading" | "none" | "approved" | "active">("loading");
  const [notionWorkspaceName, setNotionWorkspaceName] = useState<string | null>(null);
  const [notionActionLoading, setNotionActionLoading] = useState(false);
  const [notionWriteAccess, setNotionWriteAccess] = useState(false);
  const [notionHasWrite, setNotionHasWrite] = useState(false);

  // Twitter
  const [twitterStatus, setTwitterStatus] = useState<"loading" | "none" | "approved" | "active">("loading");
  const [twitterUsername, setTwitterUsername] = useState<string | null>(null);
  const [twitterActionLoading, setTwitterActionLoading] = useState(false);
  const [twitterWriteAccess, setTwitterWriteAccess] = useState(false);
  const [twitterHasWrite, setTwitterHasWrite] = useState(false);

  // Meta
  const [metaStatus, setMetaStatus] = useState<"loading" | "none" | "active">("loading");
  const [metaPageName, setMetaPageName] = useState<string | null>(null);
  const [metaIgUsername, setMetaIgUsername] = useState<string | null>(null);
  const [metaActionLoading, setMetaActionLoading] = useState(false);
  const [metaWriteAccess, setMetaWriteAccess] = useState(false);
  const [metaHasWrite, setMetaHasWrite] = useState(false);

  // Dropbox
  const [dropboxStatus, setDropboxStatus] = useState<"loading" | "none" | "active">("loading");
  const [dropboxEmail, setDropboxEmail] = useState<string | null>(null);
  const [dropboxActionLoading, setDropboxActionLoading] = useState(false);
  const [dropboxWriteAccess, setDropboxWriteAccess] = useState(false);
  const [dropboxHasWrite, setDropboxHasWrite] = useState(false);

  // Discord
  const [discordStatus, setDiscordStatus] = useState<"loading" | "none" | "active" | "unavailable">("loading");
  const [discordGuild, setDiscordGuild] = useState<{
    id: string; name: string; icon: string | null; connected_at: string;
  } | null>(null);
  const [discordBots, setDiscordBots] = useState<Array<{
    bot_id: string; display_name: string; avatar_url: string | null; is_active: boolean;
  }>>([]);
  const [discordActionLoading, setDiscordActionLoading] = useState(false);
  const [showAddDiscordBot, setShowAddDiscordBot] = useState(false);
  const [selectedDiscordBotId, setSelectedDiscordBotId] = useState("");
  const [discordBotDisplayName, setDiscordBotDisplayName] = useState("");
  const [userBots, setUserBots] = useState<Array<{ id: string; name: string; avatar_url: string | null }>>([]);

  // Fetch status callbacks
  const fetchGoogleStatus = useCallback(() => {
    authFetch("/api/integrations/google/status")
      .then((res) => res.ok ? res.json() : { status: "none" })
      .then((data) => {
        setGoogleStatus(data.status ?? "none");
        setGoogleEmail(data.email ?? null);
        setGoogleScopes(data.scopes ?? null);
      })
      .catch(() => setGoogleStatus("none"));
  }, [authFetch]);

  const fetchNotionStatus = useCallback(() => {
    authFetch("/api/integrations/notion/status")
      .then((res) => res.ok ? res.json() : { status: "none" })
      .then((data) => {
        setNotionStatus(data.status ?? "none");
        setNotionWorkspaceName(data.workspaceName ?? null);
        setNotionHasWrite(data.writeAccess ?? false);
      })
      .catch(() => setNotionStatus("none"));
  }, [authFetch]);

  const fetchTwitterStatus = useCallback(() => {
    authFetch("/api/integrations/twitter/status")
      .then((res) => res.ok ? res.json() : { status: "none" })
      .then((data) => {
        setTwitterStatus(data.status ?? "none");
        setTwitterUsername(data.username ?? null);
        const scopes: string[] = data.scopes ?? [];
        setTwitterHasWrite(scopes.includes("tweet.write"));
      })
      .catch(() => setTwitterStatus("none"));
  }, [authFetch]);

  const fetchMetaStatus = useCallback(() => {
    authFetch("/api/integrations/meta/status")
      .then((res) => res.ok ? res.json() : { status: "none" })
      .then((data) => {
        setMetaStatus(data.status ?? "none");
        setMetaPageName(data.pageName ?? null);
        setMetaIgUsername(data.instagramUsername ?? null);
        setMetaHasWrite(data.writeAccess ?? false);
      })
      .catch(() => setMetaStatus("none"));
  }, [authFetch]);

  const fetchDropboxStatus = useCallback(() => {
    authFetch("/api/integrations/dropbox/status")
      .then((res) => res.ok ? res.json() : { status: "none" })
      .then((data) => {
        setDropboxStatus(data.status ?? "none");
        setDropboxEmail(data.email ?? null);
        setDropboxHasWrite(data.writeAccess ?? false);
      })
      .catch(() => setDropboxStatus("none"));
  }, [authFetch]);

  const fetchDiscordStatus = useCallback(() => {
    authFetch("/api/integrations/discord/status")
      .then((res) => res.ok ? res.json() : { status: "none" })
      .then((data) => {
        setDiscordStatus(data.status ?? "none");
        setDiscordGuild(data.guild ?? null);
        setDiscordBots(data.bots ?? []);
      })
      .catch(() => setDiscordStatus("none"));
  }, [authFetch]);

  useEffect(() => { fetchGoogleStatus(); }, [fetchGoogleStatus]);
  useEffect(() => { fetchNotionStatus(); }, [fetchNotionStatus]);
  useEffect(() => { fetchTwitterStatus(); }, [fetchTwitterStatus]);
  useEffect(() => { fetchMetaStatus(); }, [fetchMetaStatus]);
  useEffect(() => { fetchDropboxStatus(); }, [fetchDropboxStatus]);
  useEffect(() => { fetchDiscordStatus(); }, [fetchDiscordStatus]);

  useEffect(() => {
    if (googleStatus === "active") fetchGoogleFiles();
  }, [googleStatus, fetchGoogleFiles]);

  useEffect(() => {
    if (discordStatus === "active") {
      authFetch("/api/bots")
        .then((res) => res.ok ? res.json() : { bots: [] })
        .then((data) => setUserBots(data.bots ?? []))
        .catch(() => {});
    }
  }, [discordStatus, authFetch]);

  // OAuth popup listener
  useEffect(() => {
    const expectedOrigin = getOAuthMessageOrigin();
    function handleMessage(event: MessageEvent) {
      if (event.origin !== expectedOrigin) return;
      if (event.data?.type === "google-oauth") fetchGoogleStatus();
      if (event.data?.type === "notion-oauth") fetchNotionStatus();
      if (event.data?.type === "twitter-oauth") fetchTwitterStatus();
      if (event.data?.type === "meta-oauth") fetchMetaStatus();
      if (event.data?.type === "dropbox-oauth") fetchDropboxStatus();
      if (event.data?.type === "discord-oauth") fetchDiscordStatus();
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [fetchGoogleStatus, fetchNotionStatus, fetchTwitterStatus, fetchMetaStatus, fetchDropboxStatus, fetchDiscordStatus]);

  // Handlers
  async function handleGoogleConnect(includeAds = false) {
    setGoogleActionLoading(true);
    setError(null);
    try {
      const res = await authFetch(includeAds ? "/api/integrations/google/authorize?ads=true" : "/api/integrations/google/authorize");
      const data = await res.json();
      if (data.url) {
        window.open(data.url, "_blank", "width=500,height=700");
      } else {
        setError(data.error || t.settingsPage.googleNotConfigured);
      }
    } catch {
      setError(t.settingsPage.googleNotConfigured);
    } finally {
      setGoogleActionLoading(false);
    }
  }

  async function handleGoogleDisconnect() {
    setGoogleActionLoading(true);
    setError(null);
    try {
      const res = await authFetch("/api/integrations/google/disconnect", { method: "POST" });
      if (res.ok) {
        setGoogleStatus("approved");
        setGoogleEmail(null);
      }
    } catch {
      setError(t.settingsPage.googleNotConfigured);
    } finally {
      setGoogleActionLoading(false);
    }
  }

  async function handleGooglePicker() {
    setPickerLoading(true);
    try {
      // Load Google Picker API
      await new Promise<void>((resolve, reject) => {
        if (window.google?.picker) { resolve(); return; }
        const script = document.createElement("script");
        script.src = "https://apis.google.com/js/api.js";
        script.onload = () => {
          window.gapi.load("picker", () => resolve());
        };
        script.onerror = () => reject(new Error("Failed to load Google Picker"));
        document.head.appendChild(script);
      });

      // Get OAuth token for Picker auth
      const tokenRes = await authFetch("/api/integrations/google/picker-token");
      const tokenData = await tokenRes.json();
      if (!tokenData.accessToken) {
        setError("Failed to get Picker token");
        return;
      }

      const picker = new window.google.picker.PickerBuilder()
        .setOAuthToken(tokenData.accessToken)
        .setAppId(process.env.NEXT_PUBLIC_GOOGLE_PROJECT_NUMBER!)
        .addView(window.google.picker.ViewId.DOCS)
        .addView(window.google.picker.ViewId.SPREADSHEETS)
        .addView(window.google.picker.ViewId.FOLDERS)
        .enableFeature(window.google.picker.Feature.MULTISELECT_ENABLED)
        .setCallback(async (data: GooglePickerCallbackData) => {
          if (data.action === window.google.picker.Action.PICKED && data.docs) {
            const files = data.docs.map((d) => ({
              id: d.id,
              name: d.name,
              mimeType: d.mimeType,
              iconUrl: d.iconUrl,
            }));
            await authFetch("/api/integrations/google/files", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ files }),
            });
            fetchGoogleFiles();
          }
        })
        .build();
      picker.setVisible(true);

      // Google Picker uses position:absolute — force it to fixed center
      requestAnimationFrame(() => {
        const pickerDialog = document.querySelector(".picker-dialog") as HTMLElement | null;
        if (pickerDialog) {
          pickerDialog.style.position = "fixed";
          pickerDialog.style.top = "50%";
          pickerDialog.style.left = "50%";
          pickerDialog.style.transform = "translate(-50%, -50%)";
        }
        const bg = document.querySelector(".picker-dialog-bg") as HTMLElement | null;
        if (bg) {
          bg.style.position = "fixed";
        }
      });
    } catch {
      setError("Failed to open file picker");
    } finally {
      setPickerLoading(false);
    }
  }

  async function handleRemoveGoogleFile(fileId: string) {
    await authFetch(`/api/integrations/google/files/${fileId}`, { method: "DELETE" });
    setGoogleFiles((prev) => prev.filter((f) => f.file_id !== fileId));
  }

  async function handleSaveAdsDevToken() {
    if (!botId || !adsDevToken.trim()) return;
    setAdsDevTokenSaving(true);
    try {
      const res = await authFetch(`/api/bots/${botId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ google_ads_developer_token: adsDevToken.trim() }),
      });
      if (res.ok) {
        setAdsDevTokenSaved(true);
        setAdsDevToken("");
      } else {
        const data = await res.json();
        setError(data.error || "Failed to save Developer Token");
      }
    } catch {
      setError("Failed to save Developer Token");
    } finally {
      setAdsDevTokenSaving(false);
    }
  }

  const handleSaveZapierMcpUrl = useCallback(async () => {
    if (!botId || !zapierMcpUrl.trim()) return;
    setZapierMcpUrlSaving(true);
    try {
      const res = await authFetch(`/api/bots/${botId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zapier_mcp_url: zapierMcpUrl.trim() }),
      });
      if (res.ok) {
        setZapierMcpUrl("");
        setZapierMcpUrlSaved(true);
      } else {
        setError("Failed to save Zapier MCP URL");
      }
    } catch {
      setError("Failed to save Zapier MCP URL");
    } finally {
      setZapierMcpUrlSaving(false);
    }
  }, [botId, zapierMcpUrl, authFetch]);

  async function handleNotionConnect() {
    setNotionActionLoading(true);
    setError(null);
    try {
      const res = await authFetch(`/api/integrations/notion/authorize${notionWriteAccess ? "?write=true" : ""}`);
      const data = await res.json();
      if (data.url) {
        window.open(data.url, "_blank", "width=500,height=700");
      } else {
        setError(data.error || t.settingsPage.notionNotConfigured);
      }
    } catch {
      setError(t.settingsPage.notionNotConfigured);
    } finally {
      setNotionActionLoading(false);
    }
  }

  async function handleNotionDisconnect() {
    setNotionActionLoading(true);
    setError(null);
    try {
      const res = await authFetch("/api/integrations/notion/disconnect", { method: "POST" });
      if (res.ok) {
        setNotionStatus("approved");
        setNotionWorkspaceName(null);
      }
    } catch {
      setError(t.settingsPage.notionNotConfigured);
    } finally {
      setNotionActionLoading(false);
    }
  }

  async function handleTwitterConnect() {
    setTwitterActionLoading(true);
    setError(null);
    try {
      const res = await authFetch(`/api/integrations/twitter/authorize${twitterWriteAccess ? "?write=true" : ""}`);
      const data = await res.json();
      if (data.url) {
        window.open(data.url, "_blank", "width=500,height=700");
      } else {
        setError(data.error || t.settingsPage.twitterNotConfigured);
      }
    } catch {
      setError(t.settingsPage.twitterNotConfigured);
    } finally {
      setTwitterActionLoading(false);
    }
  }

  async function handleTwitterDisconnect() {
    setTwitterActionLoading(true);
    setError(null);
    try {
      const res = await authFetch("/api/integrations/twitter/disconnect", { method: "POST" });
      if (res.ok) {
        setTwitterStatus("approved");
        setTwitterUsername(null);
      }
    } catch {
      setError(t.settingsPage.twitterNotConfigured);
    } finally {
      setTwitterActionLoading(false);
    }
  }

  async function handleMetaConnect() {
    setMetaActionLoading(true);
    setError(null);
    try {
      const res = await authFetch(`/api/integrations/meta/authorize${metaWriteAccess ? "?write=true" : ""}`);
      const data = await res.json();
      if (data.url) {
        window.open(data.url, "_blank", "width=500,height=700");
      } else {
        setError(data.error || t.settingsPage.metaNotConfigured);
      }
    } catch {
      setError(t.settingsPage.metaNotConfigured);
    } finally {
      setMetaActionLoading(false);
    }
  }

  async function handleMetaDisconnect() {
    setMetaActionLoading(true);
    setError(null);
    try {
      const res = await authFetch("/api/integrations/meta/disconnect", { method: "POST" });
      if (res.ok) {
        setMetaStatus("none");
        setMetaPageName(null);
        setMetaIgUsername(null);
      }
    } catch {
      setError(t.settingsPage.metaNotConfigured);
    } finally {
      setMetaActionLoading(false);
    }
  }

  async function handleDropboxConnect() {
    setDropboxActionLoading(true);
    setError(null);
    try {
      const res = await authFetch(`/api/integrations/dropbox/authorize${dropboxWriteAccess ? "?write=true" : ""}`);
      const data = await res.json();
      if (data.url) {
        window.open(data.url, "_blank", "width=500,height=700");
      } else {
        setError(data.error || t.settingsPage.dropboxNotConfigured);
      }
    } catch {
      setError(t.settingsPage.dropboxNotConfigured);
    } finally {
      setDropboxActionLoading(false);
    }
  }

  async function handleDropboxDisconnect() {
    setDropboxActionLoading(true);
    setError(null);
    try {
      const res = await authFetch("/api/integrations/dropbox/disconnect", { method: "POST" });
      if (res.ok) {
        setDropboxStatus("none");
        setDropboxEmail(null);
      }
    } catch {
      setError(t.settingsPage.dropboxNotConfigured);
    } finally {
      setDropboxActionLoading(false);
    }
  }

  async function handleDiscordConnect() {
    setDiscordActionLoading(true);
    setError(null);
    try {
      const res = await authFetch("/api/integrations/discord/authorize");
      const data = await res.json();
      if (data.url) {
        window.open(data.url, "_blank", "width=500,height=700");
      } else {
        setError(data.error || "Discord not configured");
      }
    } catch {
      setError("Failed to connect Discord");
    } finally {
      setDiscordActionLoading(false);
    }
  }

  async function handleDiscordDisconnect() {
    setDiscordActionLoading(true);
    try {
      await authFetch("/api/integrations/discord/disconnect", { method: "POST" });
      setDiscordStatus("none");
      setDiscordGuild(null);
      setDiscordBots([]);
    } catch {
      setError("Failed to disconnect");
    } finally {
      setDiscordActionLoading(false);
    }
  }

  async function handleAddDiscordBot() {
    if (!selectedDiscordBotId || !discordBotDisplayName.trim()) return;
    try {
      const res = await authFetch("/api/integrations/discord/bots", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          botId: selectedDiscordBotId,
          displayName: discordBotDisplayName.trim(),
        }),
      });
      if (res.ok) {
        setShowAddDiscordBot(false);
        setSelectedDiscordBotId("");
        setDiscordBotDisplayName("");
        fetchDiscordStatus();
      }
    } catch {
      setError("Failed to add bot");
    }
  }

  async function handleRemoveDiscordBot(botId: string) {
    try {
      await authFetch(`/api/integrations/discord/bots/${botId}`, { method: "DELETE" });
      fetchDiscordStatus();
    } catch {
      setError("Failed to remove bot");
    }
  }

  const connectedCount = [googleStatus, notionStatus, twitterStatus, metaStatus, dropboxStatus].filter(s => s === "active").length;

  return (
    <GlassCard className="!p-0 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between p-5 cursor-pointer hover:bg-gray-50 transition-colors text-left"
      >
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">{t.settingsPage.integrationsTitle}</span>
            {connectedCount > 0 && !open && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                {connectedCount} connected
              </span>
            )}
          </div>
          <p className="text-xs text-secondary mt-0.5">{t.settingsPage.integrationsDescription}</p>
        </div>
        <ChevronIcon expanded={open} />
      </button>

      {open && (
        <div className="border-t border-gray-200 px-5 pb-5 pt-4 space-y-3">
          {error && (
            <div className="p-2.5 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
              {error}
            </div>
          )}

          {/* Google Workspace */}
          <div className="p-3 rounded-xl border border-gray-100 bg-gray-50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-black/5 flex items-center justify-center shrink-0">
                  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none">
                    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
                    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                  </svg>
                </div>
                <div>
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm font-medium text-foreground">{t.settingsPage.googleWorkspace}</p>
                  </div>
                  <p className="text-[11px] text-secondary">{t.settingsPage.googleWorkspaceDescription}</p>
                </div>
              </div>
              <div>
                {googleStatus === "loading" ? (
                  <span className="text-xs text-secondary">...</span>
                ) : googleStatus === "active" ? (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                      {t.settingsPage.googleConnected}
                    </span>
                    <button
                      onClick={handleGoogleDisconnect}
                      disabled={googleActionLoading}
                      className="text-xs text-red-400 hover:text-red-300 transition-colors cursor-pointer disabled:opacity-40"
                    >
                      {googleActionLoading ? t.settingsPage.googleDisconnecting : t.settingsPage.googleDisconnect}
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => handleGoogleConnect()}
                    disabled={googleActionLoading}
                    className="text-xs text-primary hover:text-primary-light transition-colors px-3 py-1.5 rounded-lg border border-primary/20 hover:border-primary/40 cursor-pointer disabled:opacity-40"
                  >
                    {googleActionLoading ? t.settingsPage.googleConnecting : t.settingsPage.googleConnect}
                  </button>
                )}
              </div>
            </div>
            {googleStatus === "active" && googleEmail && (
              <p className="text-[11px] text-secondary/70 mt-2 ml-11">{googleEmail}</p>
            )}
            {googleStatus === "active" && (
              <div className="mt-3 pt-3 border-t border-gray-100 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <p className="text-xs text-secondary">{t.settingsPage.googleAdsToggle}</p>
                    <p className="text-[10px] text-secondary/60">{t.settingsPage.googleAdsToggleDescription}</p>
                  </div>
                  {googleScopes?.includes("https://www.googleapis.com/auth/adwords") ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                      {t.settingsPage.googleAdsEnabled}
                    </span>
                  ) : (
                    <button
                      onClick={() => handleGoogleConnect(true)}
                      disabled={googleActionLoading}
                      className="text-[11px] px-2.5 py-1 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20 transition-colors cursor-pointer disabled:opacity-40"
                    >
                      {t.settingsPage.googleAdsReauthorize}
                    </button>
                  )}
                </div>
                {googleScopes?.includes("https://www.googleapis.com/auth/adwords") && (
                  <div className="space-y-2">
                    <div className="p-2.5 rounded-lg bg-amber-500/5 border border-amber-500/10">
                      <p className="text-[11px] text-amber-400/90 font-medium mb-1">{t.settingsPage.googleAdsSetupTitle}</p>
                      <p className="text-[10px] text-secondary/70 leading-relaxed whitespace-pre-line">{t.settingsPage.googleAdsSetupSteps}</p>
                    </div>
                    {adsDevTokenSaved ? (
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                          {t.settingsPage.googleAdsDevTokenSaved}
                        </span>
                        <button
                          onClick={() => setAdsDevTokenSaved(false)}
                          className="text-[10px] text-secondary/50 hover:text-secondary transition-colors cursor-pointer"
                        >
                          {t.settingsPage.googleAdsDevTokenChange}
                        </button>
                      </div>
                    ) : (
                      <div className="flex gap-2">
                        <input
                          type="password"
                          value={adsDevToken}
                          onChange={(e) => setAdsDevToken(e.target.value)}
                          placeholder={t.settingsPage.googleAdsDevTokenPlaceholder}
                          className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/30 transition-colors"
                        />
                        <button
                          onClick={handleSaveAdsDevToken}
                          disabled={adsDevTokenSaving || !adsDevToken.trim() || !botId}
                          className="text-[11px] px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                        >
                          {adsDevTokenSaving ? "..." : t.settingsPage.googleAdsDevTokenSave}
                        </button>
                      </div>
                    )}
                    <a
                      href="https://ads.google.com/aw/apicenter"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[10px] text-primary/70 hover:text-primary transition-colors inline-block"
                    >
                      Google Ads API Center &rarr;
                    </a>
                  </div>
                )}
                {/* Selected Files for Edit Access */}
                <div className="mt-3 pt-3 border-t border-gray-100">
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <p className="text-xs font-medium text-foreground">{t.settingsPage.googlePickerTitle}</p>
                    <p className="text-[10px] text-secondary mt-0.5">{t.settingsPage.googlePickerDescription}</p>
                  </div>
                  <button
                    onClick={handleGooglePicker}
                    disabled={pickerLoading}
                    className="text-[11px] px-2.5 py-1 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20 transition-colors cursor-pointer disabled:opacity-40"
                  >
                    {pickerLoading ? t.settingsPage.googlePickerAdding : t.settingsPage.googlePickerAdd}
                  </button>
                </div>
                {googleFiles.length === 0 ? (
                  <p className="text-[10px] text-secondary/60 py-2">{t.settingsPage.googlePickerEmpty}</p>
                ) : (
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {googleFiles.map((f) => (
                      <div key={f.file_id} className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-gray-50 group">
                        <div className="flex items-center gap-2 min-w-0">
                          {f.icon_url && <img src={f.icon_url} alt="" className="w-4 h-4 shrink-0" />}
                          <span className="text-[11px] text-foreground truncate">{f.name}</span>
                          <span className="text-[9px] text-secondary/50 shrink-0">
                            {f.mime_type.includes("document") ? "Doc" : f.mime_type.includes("spreadsheet") ? "Sheet" : "File"}
                          </span>
                        </div>
                        <button
                          onClick={() => handleRemoveGoogleFile(f.file_id)}
                          className="text-[10px] text-red-400/60 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
                        >
                          {t.settingsPage.googlePickerRemove}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                </div>
              </div>
            )}
            {googleStatus !== "active" && googleStatus !== "loading" && (
              <p className="text-[11px] text-secondary/70 mt-2 ml-11">{t.settingsPage.googleWorkspaceDescription}</p>
            )}
          </div>

          {/* Notion */}
          <div className="p-3 rounded-xl border border-gray-100 bg-gray-50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-black/5 flex items-center justify-center shrink-0">
                  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none">
                    <path d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L18.28 2.093c-.42-.326-.98-.7-2.055-.607L3.01 2.62c-.467.047-.56.28-.374.466l1.823 1.122zm.793 3.358v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.84-.046.934-.56.934-1.166V6.63c0-.606-.233-.933-.746-.886l-15.177.886c-.56.047-.748.327-.748.933zm14.337.373c.093.42 0 .84-.42.887l-.7.14v10.264c-.607.327-1.167.514-1.634.514-.747 0-.934-.234-1.494-.934l-4.577-7.186v6.952l1.447.327s0 .84-1.167.84l-3.218.187c-.093-.187 0-.653.327-.747l.84-.233V9.854L7.037 9.76c-.094-.42.14-1.027.747-1.073l3.451-.234 4.764 7.28v-6.44l-1.214-.14c-.093-.514.28-.886.747-.933l3.218-.187z" fill="currentColor" className="text-foreground"/>
                  </svg>
                </div>
                <div>
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm font-medium text-foreground">{t.settingsPage.notionWorkspace}</p>
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20 font-medium uppercase tracking-wider">
                      Beta
                    </span>
                  </div>
                  <p className="text-[11px] text-secondary">{t.settingsPage.notionWorkspaceDescription}</p>
                </div>
              </div>
              <div>
                {notionStatus === "loading" ? (
                  <span className="text-xs text-secondary">...</span>
                ) : notionStatus === "active" ? (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                      {t.settingsPage.notionConnected}
                    </span>
                    <button
                      onClick={handleNotionDisconnect}
                      disabled={notionActionLoading}
                      className="text-xs text-red-400 hover:text-red-300 transition-colors cursor-pointer disabled:opacity-40"
                    >
                      {notionActionLoading ? t.settingsPage.notionDisconnecting : t.settingsPage.notionDisconnect}
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={handleNotionConnect}
                    disabled={notionActionLoading}
                    className="text-xs text-primary hover:text-primary-light transition-colors px-3 py-1.5 rounded-lg border border-primary/20 hover:border-primary/40 cursor-pointer disabled:opacity-40"
                  >
                    {notionActionLoading ? t.settingsPage.notionConnecting : t.settingsPage.notionConnect}
                  </button>
                )}
              </div>
            </div>
            {notionStatus === "active" && (
              <div className="mt-2 ml-11 space-y-1.5">
                <div className="flex items-center gap-2">
                  {notionWorkspaceName && (
                    <p className="text-[11px] text-secondary/70">{notionWorkspaceName}</p>
                  )}
                  <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${notionHasWrite ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" : "bg-slate-500/10 text-slate-400 border border-slate-500/20"}`}>
                    {notionHasWrite ? t.settingsPage.notionReadWrite : t.settingsPage.notionReadOnly}
                  </span>
                </div>
                <div className="p-2 rounded-lg bg-white border border-gray-200">
                  <p className="text-[10px] text-secondary/50 mb-1">{t.settingsPage.notionSuggestedCommands}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {[
                      t.settingsPage.notionCmdKbSync,
                      t.settingsPage.notionCmdSearch,
                      t.settingsPage.notionCmdSync,
                    ].map((cmd) => (
                      <span
                        key={cmd}
                        className="text-[10px] px-2 py-0.5 rounded-md bg-gray-100 text-gray-500 border border-gray-200 font-mono"
                      >
                        {cmd}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            )}
            {notionStatus !== "active" && notionStatus !== "loading" && (
              <div className="mt-2 ml-11 space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <button
                    type="button"
                    role="switch"
                    aria-checked={notionWriteAccess}
                    onClick={() => setNotionWriteAccess(!notionWriteAccess)}
                    className={`relative inline-flex h-4 w-7 shrink-0 rounded-full border transition-colors cursor-pointer ${notionWriteAccess ? "bg-primary border-primary/40" : "bg-gray-200 border-gray-300"}`}
                  >
                    <span className={`pointer-events-none inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${notionWriteAccess ? "translate-x-3" : "translate-x-0.5"} mt-px`} />
                  </button>
                  <span className="text-[11px] text-secondary">{t.settingsPage.notionIncludeWrite}</span>
                </label>
                <p className="text-[11px] text-secondary/70">{t.settingsPage.notionApprovedHint}</p>
              </div>
            )}
          </div>

          {/* X (Twitter) */}
          <div className="p-3 rounded-xl border border-gray-100 bg-gray-50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-black/5 flex items-center justify-center shrink-0">
                  <svg className="w-5 h-5 text-foreground" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                  </svg>
                </div>
                <div>
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm font-medium text-foreground">{t.settingsPage.twitterX}</p>
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20 font-medium uppercase tracking-wider">
                      Beta
                    </span>
                  </div>
                  <p className="text-[11px] text-secondary">{t.settingsPage.twitterXDescription}</p>
                </div>
              </div>
              <div>
                {twitterStatus === "loading" ? (
                  <span className="text-xs text-secondary">...</span>
                ) : twitterStatus === "active" ? (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                      {t.settingsPage.twitterConnected}
                    </span>
                    <button
                      onClick={handleTwitterDisconnect}
                      disabled={twitterActionLoading}
                      className="text-xs text-red-400 hover:text-red-300 transition-colors cursor-pointer disabled:opacity-40"
                    >
                      {twitterActionLoading ? t.settingsPage.twitterDisconnecting : t.settingsPage.twitterDisconnect}
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={handleTwitterConnect}
                    disabled={twitterActionLoading}
                    className="text-xs text-primary hover:text-primary-light transition-colors px-3 py-1.5 rounded-lg border border-primary/20 hover:border-primary/40 cursor-pointer disabled:opacity-40"
                  >
                    {twitterActionLoading ? t.settingsPage.twitterConnecting : t.settingsPage.twitterConnect}
                  </button>
                )}
              </div>
            </div>
            {twitterStatus === "active" && (
              <div className="flex items-center gap-2 mt-2 ml-11">
                {twitterUsername && (
                  <p className="text-[11px] text-secondary/70">@{twitterUsername}</p>
                )}
                <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${twitterHasWrite ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" : "bg-slate-500/10 text-slate-400 border border-slate-500/20"}`}>
                  {twitterHasWrite ? t.settingsPage.twitterReadWrite : t.settingsPage.twitterReadOnly}
                </span>
              </div>
            )}
            {twitterStatus !== "active" && twitterStatus !== "loading" && (
              <div className="mt-2 ml-11 space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <button
                    type="button"
                    role="switch"
                    aria-checked={twitterWriteAccess}
                    onClick={() => setTwitterWriteAccess(!twitterWriteAccess)}
                    className={`relative inline-flex h-4 w-7 shrink-0 rounded-full border transition-colors cursor-pointer ${twitterWriteAccess ? "bg-primary border-primary/40" : "bg-gray-200 border-gray-300"}`}
                  >
                    <span className={`pointer-events-none inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${twitterWriteAccess ? "translate-x-3" : "translate-x-0.5"} mt-px`} />
                  </button>
                  <span className="text-[11px] text-secondary">{t.settingsPage.twitterIncludeWrite}</span>
                </label>
                <p className="text-[11px] text-secondary/70">{t.settingsPage.twitterApprovedHint}</p>
              </div>
            )}
          </div>

          {/* Meta */}
          <div className="p-3 rounded-xl border border-gray-100 bg-gray-50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-black/5 flex items-center justify-center shrink-0">
                  <svg className="w-5 h-5 text-foreground" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2.04c-5.5 0-10 4.49-10 10.02 0 5 3.66 9.15 8.44 9.9v-7H7.9v-2.9h2.54V9.85c0-2.52 1.49-3.93 3.78-3.93 1.09 0 2.23.19 2.23.19v2.47h-1.26c-1.24 0-1.63.77-1.63 1.56v1.88h2.78l-.45 2.9h-2.33v7a10 10 0 0 0 8.44-9.9c0-5.53-4.5-10.02-10-10.02Z"/>
                  </svg>
                </div>
                <div>
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm font-medium text-foreground">{t.settingsPage.metaIntegration}</p>
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20 font-medium uppercase tracking-wider">
                      Beta
                    </span>
                  </div>
                  <p className="text-[11px] text-secondary">{t.settingsPage.metaIntegrationDescription}</p>
                </div>
              </div>
              <div>
                {metaStatus === "loading" ? (
                  <span className="text-xs text-secondary">...</span>
                ) : metaStatus === "active" ? (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                      {t.settingsPage.metaConnected}
                    </span>
                    <button
                      onClick={handleMetaDisconnect}
                      disabled={metaActionLoading}
                      className="text-xs text-red-400 hover:text-red-300 transition-colors cursor-pointer disabled:opacity-40"
                    >
                      {metaActionLoading ? t.settingsPage.metaDisconnecting : t.settingsPage.metaDisconnect}
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={handleMetaConnect}
                    disabled={metaActionLoading}
                    className="text-xs text-primary hover:text-primary-light transition-colors px-3 py-1.5 rounded-lg border border-primary/20 hover:border-primary/40 cursor-pointer disabled:opacity-40"
                  >
                    {metaActionLoading ? t.settingsPage.metaConnecting : t.settingsPage.metaConnect}
                  </button>
                )}
              </div>
            </div>
            {metaStatus === "active" && (
              <div className="flex items-center gap-2 mt-2 ml-11">
                {metaPageName && (
                  <p className="text-[11px] text-secondary/70">{metaPageName}</p>
                )}
                {metaIgUsername && (
                  <p className="text-[11px] text-secondary/70">@{metaIgUsername}</p>
                )}
                <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${metaHasWrite ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" : "bg-slate-500/10 text-slate-400 border border-slate-500/20"}`}>
                  {metaHasWrite ? t.settingsPage.metaReadWrite : t.settingsPage.metaReadOnly}
                </span>
              </div>
            )}
            {metaStatus !== "active" && metaStatus !== "loading" && (
              <div className="mt-2 ml-11 space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <button
                    type="button"
                    role="switch"
                    aria-checked={metaWriteAccess}
                    onClick={() => setMetaWriteAccess(!metaWriteAccess)}
                    className={`relative inline-flex h-4 w-7 shrink-0 rounded-full border transition-colors cursor-pointer ${metaWriteAccess ? "bg-primary border-primary/40" : "bg-gray-200 border-gray-300"}`}
                  >
                    <span className={`pointer-events-none inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${metaWriteAccess ? "translate-x-3" : "translate-x-0.5"} mt-px`} />
                  </button>
                  <span className="text-[11px] text-secondary">{t.settingsPage.metaIncludeWrite}</span>
                </label>
                <p className="text-[11px] text-secondary/70">{t.settingsPage.metaApprovedHint}</p>
              </div>
            )}
          </div>

          <SocialBrowserConnect />

          {/* Dropbox */}
          <div className="p-3 rounded-xl border border-gray-100 bg-gray-50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-black/5 flex items-center justify-center shrink-0">
                  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none">
                    <path d="M6 2l6 3.75L6 9.5 0 5.75zm12 0l6 3.75-6 3.75-6-3.75zM0 13.25L6 9.5l6 3.75L6 17zm12 0l6-3.75 6 3.75L18 17zM6 18.25l6-3.75 6 3.75L12 22z" fill="#0061FF"/>
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">{t.settingsPage.dropboxIntegration}</p>
                  <p className="text-[11px] text-secondary">{t.settingsPage.dropboxIntegrationDescription}</p>
                </div>
              </div>
              <div>
                {dropboxStatus === "loading" ? (
                  <span className="text-xs text-secondary">...</span>
                ) : dropboxStatus === "active" ? (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                      {t.settingsPage.dropboxConnected}
                    </span>
                    <button
                      onClick={handleDropboxDisconnect}
                      disabled={dropboxActionLoading}
                      className="text-xs text-red-400 hover:text-red-300 transition-colors cursor-pointer disabled:opacity-40"
                    >
                      {dropboxActionLoading ? t.settingsPage.dropboxDisconnecting : t.settingsPage.dropboxDisconnect}
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={handleDropboxConnect}
                    disabled={dropboxActionLoading}
                    className="text-xs text-primary hover:text-primary-light transition-colors px-3 py-1.5 rounded-lg border border-primary/20 hover:border-primary/40 cursor-pointer disabled:opacity-40"
                  >
                    {dropboxActionLoading ? t.settingsPage.dropboxConnecting : t.settingsPage.dropboxConnect}
                  </button>
                )}
              </div>
            </div>
            {dropboxStatus === "active" && (
              <div className="flex items-center gap-2 mt-2 ml-11">
                {dropboxEmail && (
                  <p className="text-[11px] text-secondary/70">{dropboxEmail}</p>
                )}
                <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${dropboxHasWrite ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" : "bg-slate-500/10 text-slate-400 border border-slate-500/20"}`}>
                  {dropboxHasWrite ? t.settingsPage.dropboxReadWrite : t.settingsPage.dropboxReadOnly}
                </span>
              </div>
            )}
            {dropboxStatus !== "active" && dropboxStatus !== "loading" && (
              <div className="mt-2 ml-11 space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <button
                    type="button"
                    role="switch"
                    aria-checked={dropboxWriteAccess}
                    onClick={() => setDropboxWriteAccess(!dropboxWriteAccess)}
                    className={`relative inline-flex h-4 w-7 shrink-0 rounded-full border transition-colors cursor-pointer ${dropboxWriteAccess ? "bg-primary border-primary/40" : "bg-gray-200 border-gray-300"}`}
                  >
                    <span className={`pointer-events-none inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${dropboxWriteAccess ? "translate-x-3" : "translate-x-0.5"} mt-px`} />
                  </button>
                  <span className="text-[11px] text-secondary">{t.settingsPage.dropboxIncludeWrite}</span>
                </label>
                <p className="text-[11px] text-secondary/70">{t.settingsPage.dropboxApprovedHint}</p>
              </div>
            )}
          </div>

          {/* Zapier MCP */}
          <div className="p-3 rounded-xl border border-gray-100 bg-gray-50">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-orange-500/10 flex items-center justify-center text-orange-400 text-sm font-bold shrink-0">Z</div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground">{t.settingsPage.zapierMcpTitle}</p>
                <p className="text-[10px] text-secondary/70 mt-0.5">{t.settingsPage.zapierMcpDescription}</p>
              </div>
            </div>
            <div className="mt-2.5 ml-11 space-y-2">
              <div className="p-2.5 rounded-lg bg-orange-500/5 border border-orange-500/10">
                <p className="text-[10px] text-secondary/70 leading-relaxed whitespace-pre-line">{t.settingsPage.zapierMcpSetupSteps}</p>
              </div>
              {zapierMcpUrlSaved ? (
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                    {t.settingsPage.zapierMcpUrlSaved}
                  </span>
                  <button
                    onClick={() => setZapierMcpUrlSaved(false)}
                    className="text-[10px] text-secondary/50 hover:text-secondary transition-colors cursor-pointer"
                  >
                    {t.settingsPage.zapierMcpUrlChange}
                  </button>
                </div>
              ) : (
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={zapierMcpUrl}
                    onChange={(e) => setZapierMcpUrl(e.target.value)}
                    placeholder={t.settingsPage.zapierMcpUrlPlaceholder}
                    className="flex-1 text-xs px-3 py-1.5 rounded-lg bg-white border border-gray-300 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/30 transition-colors"
                  />
                  <button
                    onClick={handleSaveZapierMcpUrl}
                    disabled={zapierMcpUrlSaving || !zapierMcpUrl.trim() || !botId}
                    className="text-[11px] px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                  >
                    {zapierMcpUrlSaving ? "..." : t.settingsPage.zapierMcpUrlSave}
                  </button>
                </div>
              )}
              <a
                href="https://zapier.com/mcp"
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] text-primary/70 hover:text-primary transition-colors inline-block"
              >
                Zapier MCP Setup &rarr;
              </a>
            </div>
          </div>

          {/* Future integrations placeholder */}
          <div className="p-3 rounded-xl border border-gray-100 bg-white opacity-50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-black/5 flex items-center justify-center shrink-0">
                  <svg className="w-4 h-4 text-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">Slack, Spotify...</p>
                  <p className="text-[11px] text-secondary">{t.settingsPage.comingSoon}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </GlassCard>
  );
}

function getOAuthMessageOrigin(): string {
  const configuredUrl = process.env.NEXT_PUBLIC_APP_URL;
  if (configuredUrl) {
    try {
      return new URL(configuredUrl).origin;
    } catch {
      return window.location.origin;
    }
  }

  return window.location.origin;
}
