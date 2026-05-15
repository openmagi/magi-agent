"use client";

/* eslint-disable @next/next/no-img-element */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useMessages } from "@/lib/i18n";

type SocialProvider = "instagram" | "x";

interface SocialSession {
  provider: SocialProvider;
  sessionId: string;
  expiresAt?: number;
}

interface SocialScreenshot {
  contentType?: string;
  imageBase64?: string;
  url?: string;
}

const REMOTE_KEYS = new Set([
  "Backspace",
  "Delete",
  "Enter",
  "Escape",
  "Tab",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

function providerLabel(provider: SocialProvider, labels: { instagram: string; x: string }) {
  return provider === "instagram" ? labels.instagram : labels.x;
}

export function SocialBrowserConnect() {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const [sessions, setSessions] = useState<SocialSession[]>([]);
  const [activeSession, setActiveSession] = useState<SocialSession | null>(null);
  const [screenshot, setScreenshot] = useState<SocialScreenshot | null>(null);
  const [loadingProvider, setLoadingProvider] = useState<SocialProvider | null>(null);
  const [commandLoading, setCommandLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const labels = useMemo(
    () => ({
      instagram: t.settingsPage.socialBrowserInstagram,
      x: t.settingsPage.socialBrowserX,
    }),
    [t.settingsPage.socialBrowserInstagram, t.settingsPage.socialBrowserX],
  );

  const fetchSessions = useCallback(async () => {
    try {
      const res = await authFetch("/api/integrations/social-browser/session");
      const data = await res.json();
      const nextSessions: SocialSession[] = data.sessions ?? [];
      setSessions(nextSessions);
      setActiveSession((current) =>
        nextSessions.find((session) => session.sessionId === current?.sessionId) ?? nextSessions[0] ?? null,
      );
    } catch {
      setSessions([]);
    }
  }, [authFetch]);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const sendCommand = useCallback(
    async (command: Record<string, unknown>) => {
      if (!activeSession) return;
      setCommandLoading(true);
      setError(null);
      try {
        const res = await authFetch(
          `/api/integrations/social-browser/session/${activeSession.sessionId}/command`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(command),
          },
        );
        const data = await res.json();
        if (!res.ok) {
          setError(data.error || "Social browser command failed");
          return;
        }
        setScreenshot({
          contentType: data.contentType,
          imageBase64: data.imageBase64,
          url: data.url,
        });
        if (data.session) setActiveSession(data.session);
      } catch {
        setError("Social browser command failed");
      } finally {
        setCommandLoading(false);
      }
    },
    [activeSession, authFetch],
  );

  async function start(provider: SocialProvider) {
    setLoadingProvider(provider);
    setError(null);
    setScreenshot(null);
    try {
      const res = await authFetch("/api/integrations/social-browser/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Social browser start failed");
        return;
      }
      setActiveSession(data.session);
      setSessions(data.session ? [data.session] : []);
      setScreenshot(data.screenshot ?? null);
    } catch {
      setError("Social browser start failed");
    } finally {
      setLoadingProvider(null);
    }
  }

  async function closeSession() {
    if (!activeSession) return;
    setCommandLoading(true);
    setError(null);
    try {
      const res = await authFetch(`/api/integrations/social-browser/session/${activeSession.sessionId}/command`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.error || "Social browser close failed");
        return;
      }
      setSessions((current) => current.filter((session) => session.sessionId !== activeSession.sessionId));
      setActiveSession(null);
      setScreenshot(null);
    } catch {
      setError("Social browser close failed");
    } finally {
      setCommandLoading(false);
    }
  }

  function handleScreenshotClick(event: MouseEvent<HTMLImageElement>) {
    if (!activeSession || commandLoading) return;
    const target = event.currentTarget;
    const rect = target.getBoundingClientRect();
    const scaleX = target.naturalWidth / rect.width;
    const scaleY = target.naturalHeight / rect.height;
    sendCommand({
      action: "click",
      x: Math.round((event.clientX - rect.left) * scaleX),
      y: Math.round((event.clientY - rect.top) * scaleY),
    });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!activeSession || commandLoading || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key.length === 1) {
      event.preventDefault();
      sendCommand({ action: "type", text: event.key });
      return;
    }
    if (REMOTE_KEYS.has(event.key)) {
      event.preventDefault();
      sendCommand({ action: "key", key: event.key });
    }
  }

  const imageSrc =
    screenshot?.imageBase64 && screenshot.contentType
      ? `data:${screenshot.contentType};base64,${screenshot.imageBase64}`
      : null;

  return (
    <div className="p-3 rounded-xl border border-gray-100 bg-gray-50">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-medium text-foreground">{t.settingsPage.socialBrowserTitle}</p>
          <p className="text-[11px] text-secondary">{t.settingsPage.socialBrowserDescription}</p>
          <p className="mt-1 text-[11px] text-secondary/70">{t.settingsPage.socialBrowserPasswordNotice}</p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-1.5">
          {(["instagram", "x"] as const).map((provider) => (
            <button
              key={provider}
              type="button"
              onClick={() => start(provider)}
              disabled={Boolean(loadingProvider) || commandLoading}
              className="text-xs text-primary hover:text-primary-light transition-colors px-3 py-1.5 rounded-lg border border-primary/20 hover:border-primary/40 cursor-pointer disabled:opacity-40"
            >
              {loadingProvider === provider
                ? t.settingsPage.socialBrowserStart
                : `${t.settingsPage.socialBrowserStart} ${providerLabel(provider, labels)}`}
            </button>
          ))}
          {activeSession && (
            <button
              type="button"
              onClick={closeSession}
              disabled={commandLoading}
              className="text-xs text-red-400 hover:text-red-300 transition-colors px-3 py-1.5 rounded-lg border border-red-400/20 hover:border-red-400/40 cursor-pointer disabled:opacity-40"
            >
              {t.settingsPage.socialBrowserClose}
            </button>
          )}
        </div>
      </div>

      {sessions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {sessions.map((session) => (
            <button
              key={session.sessionId}
              type="button"
              onClick={() => setActiveSession(session)}
              className={`text-[10px] px-2 py-1 rounded-lg border transition-colors ${
                activeSession?.sessionId === session.sessionId
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "border-gray-200 bg-white text-secondary"
              }`}
            >
              {providerLabel(session.provider, labels)}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="mt-2 rounded-lg border border-red-500/20 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-400">
          {error}
        </div>
      )}

      {activeSession && (
        <div
          tabIndex={0}
          onKeyDown={handleKeyDown}
          title={t.settingsPage.socialBrowserKeyboardHint}
          className="mt-3 overflow-hidden rounded-lg border border-black/[0.08] bg-white outline-none focus:ring-2 focus:ring-primary/20"
        >
          <div className="flex items-center justify-between border-b border-black/[0.06] px-2.5 py-1.5">
            <span className="truncate text-[11px] text-secondary">
              {providerLabel(activeSession.provider, labels)}
              {screenshot?.url ? ` · ${screenshot.url}` : ""}
            </span>
            <button
              type="button"
              onClick={() => sendCommand({ action: "screenshot" })}
              disabled={commandLoading}
              className="text-[11px] text-primary hover:text-primary-light disabled:opacity-40"
            >
              {t.settingsPage.socialBrowserRefresh}
            </button>
          </div>
          {imageSrc ? (
            <img
              src={imageSrc}
              alt={t.settingsPage.socialBrowserScreenshotAlt}
              onClick={handleScreenshotClick}
              className="block aspect-video w-full cursor-crosshair object-contain"
            />
          ) : (
            <div className="flex aspect-video items-center justify-center text-[11px] text-secondary">
              {commandLoading ? "..." : providerLabel(activeSession.provider, labels)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
