/* Self-destructing service worker: caching is disabled in v2.2+.
   It clears old caches immediately so the UI is always fresh. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", () => { /* pass-through: never serve from cache */ });
