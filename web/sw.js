// Service worker: lets the reader be installed and open offline.
//
// The app's files and the API are fetched from the network first (revalidated, so a
// change shows on the next load) and fall back to the cache when offline: books, words
// and scholia already seen stay readable. Fonts (fonts/) never change, so they come from
// the cache once fetched. Audio is left to the browser: it is fetched in byte ranges, which
// a plain cache can't answer.
const VERSION = "v11";
const SHELL = `shell-${VERSION}`;
const DATA = `data-${VERSION}`;
const FONTS = "fonts";  // kept across versions
const SHELL_FILES = ["./", "index.html", "home.js", "read.html", "app.js", "theme.js", "shared.js", "style.css", "manifest.webmanifest",
                     "icons/icon-192.png", "icons/icon-512.png", "icons/icon-180.png", "icons/icon-32.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  const keep = new Set([SHELL, DATA, FONTS]);
  e.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((n) => !keep.has(n)).map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});

async function networkFirst(request, cacheName, cacheKey = request) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request, { cache: "no-cache" });
    if (response.ok) cache.put(cacheKey, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(cacheKey);
    if (cached) return cached;
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(FONTS);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok || response.type === "opaque") cache.put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (e) => {
  const { request } = e;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin === location.origin && url.pathname.startsWith("/fonts/")) {
    e.respondWith(cacheFirst(request));
  } else if (url.origin !== location.origin || url.pathname.startsWith("/audio/")) {
    return;  // the browser handles it
  } else if (request.mode === "navigate" && (url.pathname === "/" || url.pathname === "/index.html")) {
    e.respondWith(networkFirst(request, SHELL, new URL("index.html", self.registration.scope).href));
  } else if (request.mode === "navigate" && url.pathname === "/read.html") {
    // The reader is one page whatever the text and book (?text=…&book=N): one cache key.
    e.respondWith(networkFirst(request, SHELL, new URL("read.html", self.registration.scope).href));
  } else if (url.pathname.startsWith("/api/")) {
    e.respondWith(networkFirst(request, DATA));
  } else {
    e.respondWith(networkFirst(request, SHELL));
  }
});
