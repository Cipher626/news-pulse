// Network first, cached copy when offline. Bump VERSION when you change the shell files.
const VERSION = "pulse-v1";
const SHELL = ["./", "./index.html", "./app.js", "./style.css", "./manifest.webmanifest", "./icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) return;

  event.respondWith(
    (async () => {
      const key = new URL(request.url);
      key.search = ""; // feed.json?t=123 and feed.json share one cache entry
      try {
        const response = await fetch(request);
        if (response.ok) {
          const cache = await caches.open(VERSION);
          await cache.put(key.href, response.clone());
        }
        return response;
      } catch (error) {
        const cached = await caches.match(key.href);
        if (cached) return cached;
        throw error;
      }
    })(),
  );
});
