// Downloads: part of a text kept on the device, to read and hear offline, a recording at a
// time with the books it's for (a book of the Iliad or Odyssey, a Bible book's chapters,
// a dialogue), each named "<slug>/<n>" after its first book. Its books (the reader's API
// for each) go into the "offline" cache and its recording, in pieces fetched by byte range
// (so a download cut short goes on where it stopped), into "audio"; sw.js answers from
// them, and leaves both alone when a new version comes. Each download's record, what
// /api/offline/<slug>/<n> gave, is kept at /offline/<slug>/<n>. Downloads asked for wait
// in a queue and run one at a time.
const offline = (() => {
  const OFFLINE = "offline", AUDIO = "audio";
  const PIECE = 8 << 20;  // bytes per piece of a recording
  const abs = (url) => new URL(url, location.href).href;
  const recordKey = (key) => abs(`/offline/${key}`);
  const pieces = (size) => Math.ceil(size / PIECE);
  const queue = [];  // keys waiting, in order
  let current = null;  // {key, controller, have, size}
  const listeners = new Set();
  const tell = (key, status) => listeners.forEach((f) => f(key, status));
  const supported = "caches" in window && "serviceWorker" in navigator;

  async function records() {
    const cache = await caches.open(OFFLINE);
    const prefix = recordKey("");
    const out = [];
    for (const req of await cache.keys()) {
      const info = req.url.startsWith(prefix) && await (await cache.match(req)).json();
      if (info?.n != null) out.push(info);
    }
    return out;
  }
  async function audioKeys() {
    return new Set((await (await caches.open(AUDIO)).keys()).map((r) => r.url));
  }
  // The bytes of a download's recording saved so far (the pieces there).
  function saved(info, keys) {
    let have = 0;
    for (const a of info.audio) {
      const url = abs(a.url);
      for (let k = 0; k < pieces(a.size); k++) {
        if (keys.has(`${url}?piece=${k}`)) have += Math.min(PIECE, a.size - k * PIECE);
      }
    }
    return have;
  }

  // {state: "partial" | "queued" | "running" | "done", have, size} for each download with
  // a record ("none" for those without is left out).
  async function statuses() {
    const out = new Map();
    if (!supported) return out;
    const keys = await audioKeys();
    for (const info of await records()) {
      const key = `${info.slug}/${info.n}`;
      const have = current?.key === key ? current.have : saved(info, keys);
      const state = current?.key === key ? "running" : queue.includes(key) ? "queued"
        : info.done && have >= info.size ? "done" : "partial";
      out.set(key, { state, have, size: info.size });
    }
    return out;
  }

  async function fetchOk(url, init) {
    const r = await fetch(url, init);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r;
  }

  async function run(job) {
    const { signal } = job.controller;
    navigator.storage?.persist?.().catch(() => {});
    const info = await (await fetchOk(`/api/offline/${job.key}`, { signal })).json();
    const books = await caches.open(OFFLINE), audio = await caches.open(AUDIO);
    await books.put(recordKey(job.key), Response.json({ ...info, done: false }));
    let keys = await audioKeys();
    job.have = saved(info, keys);
    job.size = info.size;
    tell(job.key, { state: "running", have: job.have, size: job.size });
    for (const page of info.pages) {  // (small: fetched again each time, so they're fresh)
      await books.put(abs(page), await fetchOk(page, { signal, cache: "no-cache" }));
    }
    for (const a of info.audio) {
      const url = abs(a.url);
      let meta = await (await audio.match(`${url}?offline`))?.json();
      if (meta && meta.size !== a.size) {  // the recording changed since: start it again
        for (let k = 0; k < meta.pieces; k++) await audio.delete(`${url}?piece=${k}`);
        keys = await audioKeys();
        job.have = saved(info, keys);
        meta = null;
      }
      meta ??= { size: a.size, type: "", piece: PIECE, pieces: pieces(a.size), done: false };
      for (let k = 0; k < meta.pieces; k++) {
        if (keys.has(`${url}?piece=${k}`)) continue;
        const from = k * PIECE, to = Math.min(a.size, from + PIECE) - 1;
        const r = await fetchOk(url, { signal, headers: { Range: `bytes=${from}-${to}` } });
        const blob = await r.blob();
        if (blob.size !== to - from + 1) throw new Error("the server sent the wrong part of the recording");
        meta.type ||= (r.headers.get("Content-Type") ?? "audio/mp4").split(";")[0];
        await audio.put(`${url}?piece=${k}`, new Response(blob, { headers: { "Content-Type": "application/octet-stream" } }));
        if (k === 0) await audio.put(`${url}?offline`, Response.json(meta));
        job.have += blob.size;
        tell(job.key, { state: "running", have: job.have, size: job.size });
      }
      await audio.put(`${url}?offline`, Response.json({ ...meta, done: true }));
    }
    await books.put(recordKey(job.key), Response.json({ ...info, done: true }));
  }

  async function statusOf(key) {
    return (await statuses()).get(key) ?? { state: "none" };
  }

  // The next download in the queue, until it's empty.
  async function next() {
    if (current || !queue.length) return;
    const key = queue.shift();
    current = { key, controller: new AbortController(), have: 0, size: 0 };
    let error = null;
    try {
      await run(current);
    } catch (err) {
      if (!current.controller.signal.aborted) error = err;
    }
    current = null;
    const status = await statusOf(key);
    tell(key, error ? { ...status, state: "failed", error } : status);
    if (error && navigator.onLine === false) {  // offline: the rest wait for the next visit
      const waiting = queue.splice(0);
      for (const k of waiting) tell(k, await statusOf(k));
      return;
    }
    next();
  }

  // Queue downloads (keys "<slug>/<n>"); those done, running or queued already are left.
  async function download(keys) {
    const have = await statuses();
    for (const key of keys) {
      if (current?.key === key || queue.includes(key) || have.get(key)?.state === "done") continue;
      queue.push(key);
      tell(key, { ...(have.get(key) ?? { have: 0, size: 0 }), state: "queued" });
    }
    next();
  }

  // Take downloads out of the queue, and stop the one running if it's among them.
  async function pause(keys) {
    const gone = new Set(keys);
    const taken = queue.filter((key) => gone.has(key));
    for (let i = queue.length - 1; i >= 0; i--) if (gone.has(queue[i])) queue.splice(i, 1);
    if (current && gone.has(current.key)) current.controller.abort();
    if (!taken.length) return;
    const now = await statuses();
    for (const key of taken) tell(key, now.get(key) ?? { state: "none" });
  }

  // Remove downloads: their books and recordings (a recording another download uses is kept).
  async function remove(keys) {
    await pause(keys);
    while (current && keys.includes(current.key)) await new Promise((r) => setTimeout(r, 50));
    const books = await caches.open(OFFLINE), audio = await caches.open(AUDIO);
    const all = await records();
    const gone = new Set(keys);
    const kept = new Set(all.filter((r) => !gone.has(`${r.slug}/${r.n}`)).flatMap((r) => r.audio.map((a) => abs(a.url))));
    for (const info of all.filter((r) => gone.has(`${r.slug}/${r.n}`))) {
      for (const page of info.pages) await books.delete(abs(page));
      for (const a of info.audio) {
        const url = abs(a.url);
        if (kept.has(url)) continue;
        await audio.delete(`${url}?offline`);
        for (let k = 0; k < pieces(a.size); k++) await audio.delete(`${url}?piece=${k}`);
      }
      await books.delete(recordKey(`${info.slug}/${info.n}`));
    }
    for (const key of keys) tell(key, { state: "none" });
  }

  // Downloads cut short (the page closed, the connection lost) go on by themselves.
  async function resume() {
    if (!supported || navigator.onLine === false) return;
    const partial = [...(await statuses())].filter(([, s]) => s.state === "partial").map(([key]) => key);
    if (partial.length) download(partial);
  }

  // iPhones and iPads keep a site's data only while it's used, unless it's on the Home Screen.
  const needsHomeScreen = (/iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1))
    && !(navigator.standalone || matchMedia("(display-mode: standalone)").matches);

  return { supported, statuses, download, pause, remove, resume, needsHomeScreen,
           onChange: (f) => listeners.add(f) };
})();

// "148 MB", "1.1 GB".
function formatSize(bytes) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}
