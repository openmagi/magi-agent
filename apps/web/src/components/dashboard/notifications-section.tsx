"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { registerPush, unregisterPush } from "@/lib/push";

type Status = "idle" | "registering" | "enabled" | "denied" | "error" | "unsupported";

/**
 * §7.14 Notification enablement UI.
 *
 * Shows one of:
 *   - "Enable notifications" button (default state).
 *   - Running spinner during registration.
 *   - "Notifications enabled" + Disable button once registered.
 *   - Error/unsupported fallback.
 */
export function NotificationsSection() {
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function probe(): Promise<void> {
      if (
        typeof window === "undefined" ||
        !("serviceWorker" in navigator) ||
        !("PushManager" in window)
      ) {
        if (!cancelled) setStatus("unsupported");
        return;
      }
      if (Notification.permission === "denied") {
        if (!cancelled) setStatus("denied");
        return;
      }
      const reg = await navigator.serviceWorker.getRegistration("/sw.js");
      const sub = await reg?.pushManager.getSubscription();
      if (!cancelled && sub) setStatus("enabled");
    }
    void probe();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onEnable(): Promise<void> {
    setStatus("registering");
    setMessage(null);
    const r = await registerPush();
    if (r.ok) {
      setStatus("enabled");
      setMessage("Notifications enabled on this device.");
    } else {
      setStatus("error");
      setMessage(`Couldn't enable push: ${r.reason ?? "unknown"}`);
    }
  }

  async function onDisable(): Promise<void> {
    setStatus("registering");
    setMessage(null);
    const r = await unregisterPush();
    if (r.ok) {
      setStatus("idle");
      setMessage("Notifications disabled on this device.");
    } else {
      setStatus("error");
      setMessage(`Couldn't disable push: ${r.reason ?? "unknown"}`);
    }
  }

  return (
    <GlassCard className="p-6">
      <h3 className="text-lg font-semibold mb-2">Push notifications</h3>
      <p className="text-sm text-muted-foreground mb-4">
        Get notified when a long-running task finishes, even if this tab is closed.
      </p>
      {status === "unsupported" && (
        <p className="text-sm text-muted-foreground">
          This browser doesn&apos;t support Web Push. Try Chrome, Edge, or Firefox.
        </p>
      )}
      {status === "denied" && (
        <p className="text-sm text-destructive">
          Notifications are blocked. Re-enable them in your browser site settings, then refresh.
        </p>
      )}
      {status === "idle" && (
        <Button onClick={onEnable} disabled={false}>
          Enable notifications
        </Button>
      )}
      {status === "registering" && (
        <Button disabled>Working...</Button>
      )}
      {status === "enabled" && (
        <div className="flex items-center gap-3">
          <span className="text-sm text-green-600">Enabled on this device.</span>
          <Button variant="secondary" onClick={onDisable}>
            Disable
          </Button>
        </div>
      )}
      {status === "error" && (
        <div className="space-y-2">
          <Button onClick={onEnable}>Retry</Button>
          {message && <p className="text-sm text-destructive">{message}</p>}
        </div>
      )}
      {message && status !== "error" && (
        <p className="text-sm text-muted-foreground mt-2">{message}</p>
      )}
    </GlassCard>
  );
}
