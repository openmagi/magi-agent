"use client";

import { useState, useEffect, useCallback } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useMessages } from "@/lib/i18n";

interface AgentEmailSectionProps {
  botId: string;
  botName: string | null;
  apiKeyMode: string;
  onReprovisioning?: () => void;
}

interface EmailQuota {
  used: number;
  limit: number;
}

export function AgentEmailSection({ botId, botName, apiKeyMode, onReprovisioning }: AgentEmailSectionProps) {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [emailAddress, setEmailAddress] = useState<string | null>(null);
  const [emailQuota, setEmailQuota] = useState<EmailQuota | null>(null);
  const [emailUsername, setEmailUsername] = useState("");
  const [emailCopied, setEmailCopied] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchEmail = useCallback(async () => {
    setLoading(true);
    try {
      const [emailRes, quotaRes] = await Promise.all([
        authFetch(`/api/bots/${botId}/email`),
        authFetch("/api/email-quota"),
      ]);
      if (emailRes.ok) {
        const data = await emailRes.json();
        setEmailEnabled(data.enabled);
        setEmailAddress(data.email);
      }
      if (quotaRes.ok) {
        const data = await quotaRes.json();
        setEmailQuota({ used: data.used_count, limit: data.monthly_limit });
      }
    } catch {
      // non-critical
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [authFetch, botId]);

  useEffect(() => {
    if (expanded && !fetched) {
      void fetchEmail();
    }
  }, [expanded, fetched, fetchEmail]);

  const handleToggle = useCallback(async () => {
    setToggling(true);
    setError(null);
    try {
      if (emailEnabled) {
        const res = await authFetch(`/api/bots/${botId}/email`, { method: "DELETE" });
        if (!res.ok) throw new Error((await res.json()).error || "Failed to disable email");
        setEmailEnabled(false);
      } else {
        const res = await authFetch(`/api/bots/${botId}/email`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(emailUsername.trim() ? { username: emailUsername.trim() } : {}),
        });
        if (!res.ok) throw new Error((await res.json()).error || "Failed to enable email");
        const data = await res.json();
        setEmailEnabled(true);
        setEmailAddress(data.email);
        if (data.reprovisioning) {
          onReprovisioning?.();
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setToggling(false);
    }
  }, [authFetch, botId, emailEnabled, emailUsername, onReprovisioning, t.errors.unexpected]);

  const handleCopy = useCallback(() => {
    if (emailAddress) {
      navigator.clipboard.writeText(emailAddress);
      setEmailCopied(true);
      setTimeout(() => setEmailCopied(false), 2000);
    }
  }, [emailAddress]);

  const isByok = apiKeyMode === "byok";

  return (
    <GlassCard className="!p-0 overflow-hidden mt-4">
      {/* Collapsible header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-5 cursor-pointer hover:bg-black/[0.02] transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="font-medium text-foreground">{t.email.title}</span>
          {!expanded && emailEnabled && emailAddress && (
            <span className="text-xs text-secondary truncate max-w-[200px]">{emailAddress}</span>
          )}
        </div>
        <svg
          className={`w-4 h-4 text-secondary transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-black/[0.06] px-5 pb-5 pt-4">
          {loading && !fetched ? (
            <div className="space-y-3 animate-pulse">
              <div className="h-4 w-48 bg-black/[0.06] rounded" />
              <div className="h-4 w-32 bg-black/[0.06] rounded" />
            </div>
          ) : isByok ? (
            <p className="text-sm text-secondary">{t.email.planRequired}</p>
          ) : !emailEnabled ? (
            <div className="space-y-4">
              <p className="text-xs text-secondary">{t.email.enableDescription}</p>
              <div>
                <p className="text-xs text-secondary mb-1.5">{t.email.usernameLabel}</p>
                <div className="flex items-center">
                  <input
                    type="text"
                    value={emailUsername}
                    onChange={(e) => setEmailUsername(e.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, ""))}
                    placeholder={botName?.toLowerCase().replace(/[^a-z0-9]/g, "") || "mybot"}
                    className="w-full bg-black/[0.04] border border-black/10 rounded-l-lg px-3 py-2 text-sm text-foreground placeholder:text-secondary/40 outline-none focus:border-primary/40 transition-colors"
                  />
                  <span className="text-sm text-secondary bg-black/[0.03] border border-black/10 border-l-0 px-3 py-2 rounded-r-lg whitespace-nowrap select-none">
                    @agentmail.openmagi.ai
                  </span>
                </div>
                <p className="text-[11px] text-secondary/50 mt-1.5">{t.email.usernameHint}</p>
              </div>
              {error && (
                <p className="text-xs text-red-400">{error}</p>
              )}
              <Button
                variant="cta"
                size="sm"
                onClick={handleToggle}
                disabled={toggling}
              >
                {toggling ? t.email.enabling : t.email.enableLabel}
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              {/* Email address */}
              <div>
                <p className="text-xs text-secondary mb-1">{t.email.emailAddress}</p>
                <div className="flex items-center gap-2">
                  <code className="text-sm text-foreground bg-black/[0.04] px-3 py-1.5 rounded-lg flex-1 truncate">
                    {emailAddress}
                  </code>
                  <button
                    onClick={handleCopy}
                    className="text-xs text-primary hover:text-primary-light transition-colors px-2 py-1.5 cursor-pointer"
                  >
                    {emailCopied ? t.email.copied : t.email.copy}
                  </button>
                </div>
              </div>

              {/* Quota */}
              {emailQuota && (
                <div>
                  <p className="text-xs text-secondary mb-1">{t.email.quota}</p>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-1.5 bg-black/[0.04] rounded-full overflow-hidden">
                      <div
                        className="h-full bg-primary/60 rounded-full transition-all"
                        style={{ width: `${emailQuota.limit > 0 ? Math.min((emailQuota.used / emailQuota.limit) * 100, 100) : 0}%` }}
                      />
                    </div>
                    <span className="text-xs text-secondary">
                      {emailQuota.used}/{emailQuota.limit}
                    </span>
                  </div>
                  <p className="text-[11px] text-secondary/60 mt-1">{t.email.overage}</p>
                </div>
              )}

              {error && (
                <p className="text-xs text-red-400">{error}</p>
              )}

              <button
                onClick={handleToggle}
                disabled={toggling}
                className="text-xs text-red-400 hover:text-red-300 transition-colors cursor-pointer"
              >
                {toggling ? t.email.disabling : t.email.disableLabel}
              </button>
            </div>
          )}
        </div>
      )}
    </GlassCard>
  );
}
