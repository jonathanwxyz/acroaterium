// Service worker: lets the reader be installed and open offline.
//
// The app's files and the API are fetched from the network first (revalidated, so a
// change shows on the next load) and fall back to the cache when offline: books, words
// and scholia already seen stay readable. Fonts (fonts/) never change, so they come from
// the cache once fetched.
//
// Downloads (offline.js, from the library): a text's books, and its recordings in pieces,
// kept in caches of their own that a new version leaves alone. A recording saved whole is
// played from there, the byte ranges the player asks for read out of its pieces; any other
// audio is left to the browser.
const VERSION = "v13";
const SHELL = `shell-${VERSION}`;
const DATA = `data-${VERSION}`;
const FONTS = "fonts";  // kept across versions
const OFFLINE = "offline";  // downloaded texts' books (and their records), kept across versions
const AUDIO = "audio";  // downloaded recordings, in pieces, kept across versions
const SHELL_FILES = ["./", "index.html", "home.js", "read.html", "app.js", "theme.js", "shared.js", "offline.js", "style.css",
                     "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-180.png", "icons/icon-32.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  const keep = new Set([SHELL, DATA, FONTS, OFFLINE, AUDIO]);
  e.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((n) => !keep.has(n)).map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});

async function networkFirst(request, cacheName, cacheKey = request) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request, { cache: "no-cache" });
    if (response.ok) {
      cache.put(cacheKey, response.clone());
      // A downloaded text's book: its saved copy kept up to date too.
      if (cacheName === DATA) {
        const offline = await caches.open(OFFLINE);
        if (await offline.match(cacheKey)) offline.put(cacheKey, response.clone());
      }
    }
    return response;
  } catch (err) {
    const cached = await cache.match(cacheKey) ?? (cacheName === DATA ? await caches.match(cacheKey) : undefined);
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

// A downloaded recording: its record ({size, type, piece, pieces, done}) at <url>?offline
// and its pieces at <url>?piece=<k>, as offline.js saves them. The bytes asked for (a
// Range, as the player asks) are read out piece by piece as the player takes them.
async function savedAudio(request) {
  const url = request.url.split(/[?#]/)[0];
  const cache = await caches.open(AUDIO);
  const record = await cache.match(`${url}?offline`);
  if (!record) return null;
  const meta = await record.json();
  if (!meta.done) return null;
  const { size, piece } = meta;
  let start = 0, end = size - 1, partial = false;
  const m = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get("range") ?? "");
  if (m && (m[1] || m[2])) {
    partial = true;
    if (m[1]) {
      start = +m[1];
      if (m[2]) end = Math.min(+m[2], size - 1);
    } else {  // the last N bytes
      start = Math.max(0, size - +m[2]);
    }
    if (start > end) return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
  }
  let at = start;
  const body = new ReadableStream({
    async pull(controller) {
      if (at > end) return controller.close();
      const k = Math.floor(at / piece);
      const saved = await cache.match(`${url}?piece=${k}`);
      if (!saved) return controller.error(new Error("a piece of the recording is missing"));
      const chunk = (await saved.blob()).slice(at - k * piece, Math.min(end + 1, (k + 1) * piece) - k * piece);
      controller.enqueue(new Uint8Array(await chunk.arrayBuffer()));
      at += chunk.size;
    },
  });
  const headers = { "Content-Type": meta.type, "Content-Length": String(end - start + 1), "Accept-Ranges": "bytes" };
  if (partial) headers["Content-Range"] = `bytes ${start}-${end}/${size}`;
  return new Response(body, { status: partial ? 206 : 200, headers });
}

async function audio(request) {
  try {
    const saved = await savedAudio(request);
    if (saved) return saved;
  } catch {}
  return fetch(request);
}

self.addEventListener("fetch", (e) => {
  const { request } = e;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) {
    return;  // the browser handles it
  } else if (url.pathname.startsWith("/fonts/")) {
    e.respondWith(cacheFirst(request));
  } else if (url.pathname.startsWith("/audio/")) {
    e.respondWith(audio(request));
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
