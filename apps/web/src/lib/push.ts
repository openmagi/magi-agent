/**
 * Web Push registration helper — §7.14.
 *
 * Flow:
 *   1. Ensure Service Worker + Push API available.
 *   2. Request Notification permission.
 *   3. Register `/sw.js` (idempotent — browser dedupes).
 *   4. Subscribe using the VAPID public key from
 *      `NEXT_PUBLIC_VAPID_PUBLIC_KEY`.
 *   5. POST subscription to `/api/push/subscribe` (server writes to
 *      `push_subscriptions`).
 *
 * All functions throw on unrecoverable errors so the caller can surface
 * a toast / message.
 */

export interface RegisterPushResult {
  ok: boolean;
  reason?: string;
  endpoint?: string;
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function isSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export async function registerPush(
  deviceLabel?: string,
): Promise<RegisterPushResult> {
  if (!isSupported()) {
    return { ok: false, reason: "unsupported" };
  }
  const vapidKey = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;
  if (!vapidKey) {
    return { ok: false, reason: "vapid_missing" };
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return { ok: false, reason: `permission:${permission}` };
  }

  const reg = await navigator.serviceWorker.register("/sw.js");
  // Wait for it to become active so pushManager.subscribe doesn't race
  // on first install.
  if (reg.installing) {
    await new Promise<void>((resolve) => {
      const sw = reg.installing!;
      sw.addEventListener("statechange", () => {
        if (sw.state === "activated") resolve();
      });
    });
  }
  await navigator.serviceWorker.ready;

  const existing = await reg.pushManager.getSubscription();
  const appKey = urlBase64ToUint8Array(vapidKey);
  // Cast: TS lib type wants BufferSource, a Uint8Array IS one at runtime.
  const subscription =
    existing ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: appKey.buffer as ArrayBuffer,
    }));

  const json = subscription.toJSON();
  const payload = {
    channel: "web" as const,
    endpoint: json.endpoint,
    keys: json.keys,
    deviceLabel:
      deviceLabel ||
      (typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 128) : null),
  };

  const resp = await fetch("/api/push/subscribe", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    return { ok: false, reason: `server:${resp.status}:${text.slice(0, 100)}` };
  }
  return { ok: true, endpoint: json.endpoint };
}

export async function unregisterPush(): Promise<RegisterPushResult> {
  if (!isSupported()) return { ok: false, reason: "unsupported" };
  const reg = await navigator.serviceWorker.getRegistration("/sw.js");
  if (!reg) return { ok: true };
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return { ok: true };
  const endpoint = sub.endpoint;
  await sub.unsubscribe();
  await fetch("/api/push/subscribe", {
    method: "DELETE",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel: "web", endpoint }),
  }).catch(() => {});
  return { ok: true };
}
