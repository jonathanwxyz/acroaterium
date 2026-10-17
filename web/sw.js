// Service worker: lets the reader be installed and open offline.
//
// The app's files (the pages, scripts and styles) come from the cache at once, and are
// fetched again behind it, so a page opens without waiting on the network and a change
// shows on the load after (a new version, which precaches them all, on the next). The API
// is fetched from the network first and falls back to the cache when offline: books,
// words and scholia already seen stay readable. Fonts (fonts/) never change, so they come
// from the cache once fetched.
//
// Downloads (offline.js, from the library): a text's books, and its recordings in pieces,
// kept in caches of their own that a new version leaves alone. A recording saved whole is
// played from there, the byte ranges the player asks for read out of its pieces; any other
// audio is left to the browser.
//
// The whole library kept on this device (local/manager.js keeps it, where it's asked for,
// and always in the app): the compact library's file (data/library_compact.sqlite), in
// pieces in a cache of its own. The API's books, word cards and notes are then made here,
// from the file (local/library.js, reading only the pages it needs: local/sqlite.js),
// before the network is asked; anything not made here, or failing, goes to the server.
importScripts("local/sqlite.js", "local/library.js");
const VERSION = "v74";
const SHELL = `shell-${VERSION}`;
const DATA = `data-${VERSION}`;
const FONTS = "fonts";  // kept across versions
const OFFLINE = "offline";  // downloaded texts' books (and their records), kept across versions
const AUDIO = "audio";  // downloaded recordings, in pieces, kept across versions
const LOCAL = "local-library";  // the whole library kept on this device (local/manager.js), kept across versions
const SHELL_FILES = ["./", "index.html", "home.js", "read.html", "app.js", "i18n.js", "theme.js", "shared.js", "offline.js", "native.js", "style.css", "local/manager.js",
                     "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-180.png", "icons/icon-32.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  const keep = new Set([SHELL, DATA, FONTS, OFFLINE, AUDIO, LOCAL]);
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
    // A book of the Iliad downloaded (or seen) before it joined the library: kept as
    // /api/book/N, which the reader can read as well.
    const m = cacheName === DATA && new URL(request.url).pathname.match(/^\/api\/text\/iliad\/(\d+)$/);
    const old = m && await caches.match(new URL(`/api/book/${m[1]}`, request.url).href);
    if (old) return old;
    throw err;
  }
}

// From the cache if it's there, fetched again behind it for next time; else from the network.
function cachedThenRevalidated(e, cacheName, cacheKey = e.request) {
  const fresh = caches.open(cacheName).then(async (cache) => {
    const response = await fetch(e.request, { cache: "no-cache" });
    if (response.ok) await cache.put(cacheKey, response.clone());
    return response;
  });
  e.waitUntil(fresh.catch(() => {}));
  return caches.open(cacheName)
    .then((cache) => cache.match(cacheKey))
    .then((cached) => cached ?? fresh);
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

// The whole library on this device, as local/manager.js keeps it: its record at
// /__local/meta ({version, size, piece, pieces, done}), the file's pieces at
// /__local/<version>/<k>, and the sizes of the server's recordings at /__local/sizes.
// Opened once (a page says when it changes), and read a block at a time from the pieces.
let local = null;  // Promise of {version, api}, or of null: none kept
function localLibrary() {
  local ??= (async () => {
    if (!(await caches.has(LOCAL))) return null;
    const cache = await caches.open(LOCAL);
    const meta = await (await cache.match("/__local/meta"))?.json();
    if (!meta?.done) return null;
    const pieces = await Promise.all(Array.from({ length: meta.pieces },
      async (_, k) => (await cache.match(`/__local/${meta.version}/${k}`))?.blob()));
    if (pieces.some((b) => !b)) return null;
    const readAt = async (at, length) => {  // (never across pieces: blocks divide a piece)
      const k = Math.floor(at / meta.piece), from = at - k * meta.piece;
      return new Uint8Array(await pieces[k].slice(from, from + length).arrayBuffer());
    };
    // (Not known yet, as when the app has just put its own copy here offline: which
    // recordings the server has decides whether a book has audio, so then the server is
    // asked first.)
    const sizes = await (await cache.match("/__local/sizes"))?.json();
    const file = await SqliteFile.open(readAt, meta.size);
    return { version: meta.version, sizesKnown: !!sizes, api: new LocalApi(new CompactLibrary(file), sizes) };
  })().catch(() => null);
  return local;
}
self.addEventListener("message", (e) => {
  if (e.data?.type === "local-library") local = null;  // (kept anew, or removed)
});

// The API: made here from the library on this device where it can be, else as before.
async function api(request) {
  const path = decodeURIComponent(new URL(request.url).pathname);
  const local = async () => {
    try {
      const lib = await localLibrary();
      const json = lib && await lib.api.route(path);
      if (json != null) return Response.json(json, { headers: { "X-Local": lib.version } });
    } catch {}
    return null;
  };
  if (!LocalApi.handles(path)) return networkFirst(request, DATA);
  const lib = await localLibrary();
  if (lib && !lib.sizesKnown) {
    try { return await networkFirst(request, DATA); } catch { return (await local()) ?? Response.error(); }
  }
  return (await local()) ?? networkFirst(request, DATA);
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
  if (url.origin !== location.origin || url.pathname.startsWith("/__app/") || url.pathname.startsWith("/api/compact")) {
    // (The browser handles it: another site; the app's own copy of the library; the
    // library's file and its recordings' sizes, fetched for local/manager.js.)
    return;
  } else if (url.pathname.startsWith("/fonts/")) {
    e.respondWith(cacheFirst(request));
  } else if (url.pathname.startsWith("/audio/")) {
    e.respondWith(audio(request));
  } else if (request.mode === "navigate" && (url.pathname === "/" || url.pathname === "/index.html")) {
    e.respondWith(cachedThenRevalidated(e, SHELL, new URL("index.html", self.registration.scope).href));
  } else if (request.mode === "navigate" && url.pathname === "/read.html") {
    // The reader is one page whatever the text and book (?text=…&book=N): one cache key.
    e.respondWith(cachedThenRevalidated(e, SHELL, new URL("read.html", self.registration.scope).href));
  } else if (url.pathname.startsWith("/api/")) {
    e.respondWith(api(request));
  } else {
    e.respondWith(cachedThenRevalidated(e, SHELL));
  }
});
