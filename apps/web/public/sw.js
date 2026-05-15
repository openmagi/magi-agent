/* eslint-env serviceworker */
/* global clients */
// Open Magi Web Push service worker — §7.14.
//
// Handles:
//   - `install` + `activate`: claim clients immediately so newly-subscribed
//     tabs receive pushes without a hard reload.
//   - `push`: show an OS-level notification. Accepts JSON payloads of the
//     shape { title, body?, url?, tag? }. When payload is empty (payload-
//     less VAPID push) we fall back to a generic "New notification" entry.
//   - `notificationclick`: focus an existing Open Magi tab if present, or open
//     a new tab to the notification's `url` (or `/dashboard`).

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch {
      data = { title: "Open Magi", body: event.data.text() };
    }
  }
  const title = data.title || "Open Magi";
  const options = {
    body: data.body || "",
    icon: "/android-chrome-192x192.png",
    badge: "/favicon-32x32.png",
    tag: data.tag || "clawy-notification",
    data: { url: data.url || "/dashboard" },
    requireInteraction: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/dashboard";
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of all) {
        try {
          const u = new URL(client.url);
          if (u.origin === self.location.origin) {
            await client.focus();
            if ("navigate" in client) await client.navigate(targetUrl);
            return;
          }
        } catch {
          // ignore malformed URLs
        }
      }
      await self.clients.openWindow(targetUrl);
    })(),
  );
});
