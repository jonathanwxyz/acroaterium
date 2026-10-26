// The whole library kept on this device, to read offline: the compact library's file
// (data/library_compact.sqlite, served at /api/compact), from which the service worker
// makes the API's books, word cards and notes (sw.js, local/library.js). On a website it's
// kept when asked for (the library's "Keep the whole library on this device"); in the app,
// always, from the copy the app comes with (/__app/…, served by the app itself) where it's
// the server's version or the server is out of reach.
//
// It's kept in the cache "local-library", as the service worker reads it: the file in 8 MB
// pieces at /__local/<version>/<k> (fetched by byte range, so a download cut short goes on
// where it stopped), its record at /__local/meta once it's whole, and the sizes of the
// server's recordings (/api/compact/sizes) at /__local/sizes. Its version is the server's
// X-Version (compact_library.py: the same library built anywhere has the same, though its
// bytes and size may differ): when the server's changes, it's fetched anew.
//
// Loaded by the library page only where it's in use (home.js).
window.localLibrary = (() => {
  const CACHE = "local-library", PIECE = 8 << 20, FLAG = "localLibrary";
  const META = "/__local/meta", PENDING = "/__local/pending", SIZES = "/__local/sizes";
  const pieceKey = (version, k) => `/__local/${version}/${k}`;
  const inApp = !!window.nativeApp;
  const listeners = new Set();
  let status = { state: "none" };  // none | checking | running {have, size} | ready {size} | failed {error}
  const tell = (s) => { status = s; listeners.forEach((f) => f(s)); };

  const open = () => caches.open(CACHE);
  // (Looked up without opening the cache: opening it would make it, empty, once let go of.)
  const read = async (key) => (await caches.match(key, { cacheName: CACHE }))?.json() ?? null;
  const write = async (key, value) => (await open()).put(key, Response.json(value));
  // The service worker opens the file anew (or lets it go).
  const notify = () => navigator.serviceWorker?.ready.then((r) => r.active?.postMessage({ type: "local-library" })).catch(() => {});

  const enabled = () => {
    if (inApp) return true;
    try { return localStorage.getItem(FLAG) === "1"; } catch { return false; }
  };
  const setEnabled = (on) => { try { on ? localStorage.setItem(FLAG, "1") : localStorage.removeItem(FLAG); } catch {} };

  // The server's version and size (null: out of reach).
  async function serverVersion() {
    try {
      const r = await fetch("/api/compact", { method: "HEAD", cache: "no-store", signal: AbortSignal.timeout(8000) });
      if (!r.ok) return null;
      return { version: r.headers.get("X-Version"), size: Number(r.headers.get("Content-Length")) };
    } catch { return null; }
  }
  // The app's own copy ({version, size}; null: none, or not the app).
  async function bundled() {
    if (!inApp) return null;
    try {
      const r = await fetch("/__app/library.json", { cache: "no-store" });
      return r.ok ? await r.json() : null;
    } catch { return null; }
  }

  async function refreshSizes() {
    try {
      const r = await fetch("/api/compact/sizes", { cache: "no-store", signal: AbortSignal.timeout(15000) });
      if (r.ok) await write(SIZES, await r.json());
    } catch {}
  }

  // Fetch a version of the file into its pieces, from the server by byte range (resuming
  // where a download stopped), or all at once from the app's copy.
  async function fetchFile({ version, size }, source) {
    const cache = await open();
    const pieces = Math.ceil(size / PIECE);
    await write(PENDING, { version, size, piece: PIECE, pieces });
    let have = 0;
    const missing = [];
    for (let k = 0; k < pieces; k++) {
      if (await cache.match(pieceKey(version, k))) have += Math.min(PIECE, size - k * PIECE);
      else missing.push(k);
    }
    tell({ state: "running", have, size });
    if (source === "app") {
      // (Its copy, streamed, cut into pieces as it comes.)
      const r = await fetch("/__app/library-compact.sqlite", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader();
      let k = 0, buf = new Uint8Array(Math.min(PIECE, size)), at = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        for (let off = 0; off < value.length;) {
          const n = Math.min(value.length - off, buf.length - at);
          buf.set(value.subarray(off, off + n), at);
          at += n; off += n;
          if (at === buf.length) {
            await cache.put(pieceKey(version, k), new Response(buf));
            have = Math.min(size, (k + 1) * PIECE);
            tell({ state: "running", have, size });
            k++;
            at = 0;
            if (k < pieces) buf = new Uint8Array(Math.min(PIECE, size - k * PIECE));
            else if (off < value.length) throw new Error("the app's copy of the library is longer than it says");
          }
        }
      }
      if (k !== pieces) throw new Error("the app's copy of the library is incomplete");
      return;
    }
    for (const k of missing) {
      const from = k * PIECE, to = Math.min(size, from + PIECE) - 1;
      const r = await fetch("/api/compact", { headers: { Range: `bytes=${from}-${to}` }, cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      if (r.headers.get("X-Version") !== version) throw new Error("changed");  // (the server's changed meanwhile)
      const blob = await r.blob();
      if (blob.size !== to - from + 1) throw new Error("the server sent the wrong part of the library");
      await cache.put(pieceKey(version, k), new Response(blob));
      have += blob.size;
      tell({ state: "running", have, size });
    }
  }

  // The new version whole: recorded, the old one's pieces let go, the service worker told.
  async function finish({ version, size }) {
    const cache = await open();
    await write(META, { version, size, piece: PIECE, pieces: Math.ceil(size / PIECE), done: true });
    await cache.delete(PENDING);
    for (const req of await cache.keys()) {
      const m = new URL(req.url).pathname.match(/^\/__local\/([0-9a-f]+)\/\d+$/);
      if (m && m[1] !== version) await cache.delete(req);
    }
    notify();
  }

  // Bring the library on this device up to the server's (or, out of reach, to the app's own
  // copy, if there's none yet). One page at a time (a lock).
  let running = null;
  function check() {
    if (!enabled()) return Promise.resolve();
    running ??= (async () => {
      const work = async () => {
        let meta = await read(META);
        tell(meta?.done ? { state: "ready", size: meta.size } : { state: "checking" });
        const [server, app] = await Promise.all([serverVersion(), bundled()]);
        // (The app's own copy first, where there's none yet: readable at once, and offline.)
        if (!meta?.done && app) {
          await fetchFile(app, "app");
          await finish(app);
          meta = { ...app, done: true };
          tell({ state: "ready", size: app.size });
        }
        if (server) {
          await refreshSizes();
          notify();
        }
        if (!server || meta?.version === server.version) {
          tell(meta?.done ? { state: "ready", size: meta.size } : { state: "none" });
          return;
        }
        // (The app's copy where it's that version: by its own size, maybe not the server's.)
        const source = app && app.version === server.version ? "app" : "server";
        const want = { ...(source === "app" ? app : server) };
        for (let tries = 0; ; tries++) {
          try {
            await fetchFile(want, source);
            break;
          } catch (err) {
            if (err.message === "changed" && tries < 2) {  // (a new version came: that one)
              const now = await serverVersion();
              if (now) { Object.assign(want, now); continue; }
            }
            throw err;
          }
        }
        await finish(want);
        tell({ state: "ready", size: want.size });
      };
      try {
        await (navigator.locks ? navigator.locks.request("local-library", work) : work());
      } catch (err) {
        const meta = await read(META).catch(() => null);
        tell(meta?.done ? { state: "ready", size: meta.size } : { state: "failed", error: String(err.message ?? err) });
      } finally {
        running = null;
      }
    })();
    return running;
  }

  // Asked for (the website): kept from now on.
  function start() {
    setEnabled(true);
    return check();
  }

  // Let go of: the file and its record (not in the app, whose library comes with it).
  async function remove() {
    if (inApp) return;
    setEnabled(false);
    await caches.delete(CACHE);
    notify();
    tell({ state: "none" });
  }

  // Whether the library on this device is whole (offline.js: a book's pages then needn't
  // be saved with its recording).
  const ready = async () => !!(await read(META).catch(() => null))?.done;

  return { check, start, remove, ready, enabled, inApp, status: () => status, onChange: (f) => listeners.add(f) };
})();
