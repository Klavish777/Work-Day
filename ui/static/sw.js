/* Minimal service worker: offline shell for the PWA build. */
const CACHE = "workday-v1";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/static/style.css", "/static/app.js"])));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => e.waitUntil(clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(e.request).then((hit) => hit || fetch(e.request).then((r) => {
        const copy = r.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return r;
      }))
    );
  }
});
