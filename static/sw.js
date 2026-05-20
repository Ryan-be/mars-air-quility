// MLSS service worker — handles push events + notification clicks.
//
// Scope is the entire site (served at /sw.js, not /static/sw.js). No
// offline caching for now — the PWA is online-only over WireGuard.

self.addEventListener("install",  e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  const title = data.title || "MLSS";
  const opts = {
    body: data.body || "",
    icon: data.icon || "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    tag: data.tag,
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.endsWith(url) && "focus" in c) return c.focus();
      }
      return clients.openWindow(url);
    })
  );
});
