// Downloads: part of a text kept on the device, to read and hear offline, a recording at a
// time with the books it's for (a book of the Iliad or Odyssey, a Bible book's chapters,
// a dialogue), each named "<slug>/<n>" after its first book. Its books (the reader's API
// for each) go into the "offline" cache and its recording, in pieces fetched by byte range
// (so a download cut short goes on where it stopped), into "audio"; sw.js answers from
// them, and leaves both alone when a new version comes.
//
// Each download's record is kept at /offline/<slug>/<n>: what /api/offline/<slug>/<n>
// gave (empty until it starts), when it was asked for ("queued", its place in the queue),
// whether it's paused and whether it's done. The queue is those not done nor paused, in
// that order, so it outlasts the page: whichever page of the reader is open (the library,
// or a book being read) works through it, one download at a time, and one page at a time
// (a lock), and the next page opened goes on where the last left off.
const offline = (() => {
  const OFFLINE = "offline", AUDIO = "audio";
  const PIECE = 8 << 20;  // bytes per piece of a recording
  const LOCK = "offline-downloads";
  const abs = (url) => new URL(url, location.href).href;
  const recordKey = (key) => abs(`/offline/${key}`);
  const keyOf = (info) => `${info.slug}/${info.n}`;
  const pieces = (size) => Math.ceil(size / PIECE);
  let current = null;  // {key, controller, have, size}: the download running in this page
  let working = false;  // this page is working through the queue
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
    return out.sort((a, b) => (a.queued ?? 0) - (b.queued ?? 0));
  }
  async function record(key) {
    return (await (await caches.open(OFFLINE)).match(recordKey(key)))?.json();
  }
  async function saveRecord(info) {
    await (await caches.open(OFFLINE)).put(recordKey(keyOf(info)), Response.json(info));
  }
  async function audioKeys() {
    return new Set((await (await caches.open(AUDIO)).keys()).map((r) => r.url));
  }
  // The bytes of a download's recording saved so far (the pieces there).
  function saved(info, keys) {
    let have = 0;
    for (const a of info.audio ?? []) {
      const url = abs(a.url);
      for (let k = 0; k < pieces(a.size); k++) {
        if (keys.has(`${url}?piece=${k}`)) have += Math.min(PIECE, a.size - k * PIECE);
      }
    }
    return have;
  }
  const waiting = (info) => !info.done && !info.paused;

  // {state: "partial" | "queued" | "running" | "done", have, size} for each download with
  // a record ("none" for those without is left out). Queued: waiting its turn, here or in
  // another page.
  async function statuses() {
    const out = new Map();
    if (!supported) return out;
    const keys = await audioKeys();
    for (const info of await records()) {
      const key = keyOf(info);
      const have = current?.key === key ? current.have : saved(info, keys);
      const state = current?.key === key ? "running" : info.done && have >= info.size ? "done"
        : waiting(info) ? "queued" : "partial";
      out.set(key, { state, have, size: info.size ?? 0 });
    }
    return out;
  }

  async function fetchOk(url, init) {
    const r = await fetch(url, init);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r;
  }

  async function run(job, asked) {
    const { signal } = job.controller;
    navigator.storage?.persist?.().catch(() => {});
    const info = { ...(await (await fetchOk(`/api/offline/${job.key}`, { signal })).json()),
                   queued: asked.queued, done: false };
    const books = await caches.open(OFFLINE), audio = await caches.open(AUDIO);
    await saveRecord(info);
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
    // (Paused or removed meanwhile in another page: left as that page left it.)
    const now = await record(job.key);
    if (now && !now.paused) await saveRecord({ ...info, done: true });
  }

  async function statusOf(key) {
    return (await statuses()).get(key) ?? { state: "none" };
  }

  // Work through the queue, while this page holds the lock (another page of the reader may
  // be at it: this one waits its turn).
  async function work() {
    if (working || !supported || navigator.onLine === false) return;
    working = true;
    const go = async () => {
      for (;;) {
        const asked = (await records()).find(waiting);
        if (!asked) break;
        const key = keyOf(asked);
        current = { key, controller: new AbortController(), have: 0, size: 0 };
        let error = null;
        try {
          await run(current, asked);
        } catch (err) {
          if (!current.controller.signal.aborted) error = err;
        }
        current = null;
        if (error) {  // set aside (paused) so the queue goes on; tapping again tries it again
          const info = await record(key);
          if (info) await saveRecord({ ...info, paused: true });
        }
        const status = await statusOf(key);
        tell(key, error ? { ...status, state: "failed", error } : status);
        if (error && navigator.onLine === false) break;  // offline: the rest wait for the next visit
      }
    };
    try {
      await (navigator.locks ? navigator.locks.request(LOCK, go) : go());
    } finally {
      working = false;
    }
  }

  // Queue downloads (keys "<slug>/<n>"), after those already queued; those done are left.
  async function download(keys) {
    const have = new Map((await records()).map((r) => [keyOf(r), r]));
    let at = Date.now();
    for (const key of keys) {
      const info = have.get(key);
      if (info?.done) continue;
      const [slug, n] = key.split("/");
      await saveRecord(info ? { ...info, paused: false, queued: info.paused ? at++ : info.queued }
                            : { slug, n: +n, pages: [], audio: [], size: 0, done: false, queued: at++ });
      tell(key, await statusOf(key));
    }
    work();
  }

  // Pause downloads: out of the queue (and stopped, if running in this page).
  async function pause(keys) {
    for (const key of keys) {
      const info = await record(key);
      if (info && !info.done) await saveRecord({ ...info, paused: true });
    }
    if (current && keys.includes(current.key)) current.controller.abort();
    for (const key of keys) tell(key, await statusOf(key));
  }

  // Remove downloads: their books and recordings (a recording another download uses is kept).
  async function remove(keys) {
    await pause(keys);
    while (current && keys.includes(current.key)) await new Promise((r) => setTimeout(r, 50));
    const books = await caches.open(OFFLINE), audio = await caches.open(AUDIO);
    const all = await records();
    const gone = new Set(keys);
    const kept = new Set(all.filter((r) => !gone.has(keyOf(r))).flatMap((r) => r.audio.map((a) => abs(a.url))));
    for (const info of all.filter((r) => gone.has(keyOf(r)))) {
      for (const page of info.pages) await books.delete(abs(page));
      for (const a of info.audio) {
        const url = abs(a.url);
        if (kept.has(url)) continue;
        await audio.delete(`${url}?offline`);
        for (let k = 0; k < pieces(a.size); k++) await audio.delete(`${url}?piece=${k}`);
      }
      await books.delete(recordKey(keyOf(info)));
    }
    for (const key of keys) tell(key, { state: "none" });
  }

  // iPhones and iPads keep a site's data only while it's used, unless it's on the Home Screen.
  const needsHomeScreen = (/iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1))
    && !(navigator.standalone || matchMedia("(display-mode: standalone)").matches);

  // The queue goes on in whichever page is open, and when the connection comes back.
  if (supported) {
    work();
    addEventListener("online", work);
  }

  return { supported, statuses, download, pause, remove, needsHomeScreen, onChange: (f) => listeners.add(f),
           busy: () => current !== null };
})();

// "148 MB", "1.1 GB".
function formatSize(bytes) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

// Download buttons, the library's and the reader's: one for some downloads ([key, size]
// pairs: a book's, or all of a work's or a Testament's, which it queues). It shows the
// size, then the progress (tap to pause, and again to go on), then a tick (tap to remove).
// downloadStatuses is what the device has; a "downloadschange" event on document says
// it has changed. While another page of the reader is downloading, it's looked at again
// every few seconds.
const downloadStatuses = new Map();  // "<slug>/<n>" -> {state, have, size}
const downloadButtons = [];  // {b, items, name, compact, labelled}
{
  const ARROW = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>';
  const TICK = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" ' +
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

  // Some downloads together: running (or queued) if any is, done if all are.
  const together = (items) => {
    let have = 0, size = 0, running = false, queued = false, failed = false, started = false, done = true;
    for (const [key, bytes] of items) {
      const s = downloadStatuses.get(key) ?? { state: "none" };
      size += bytes;
      have += s.state === "done" ? bytes : Math.min(bytes, s.have ?? 0);
      running ||= s.state === "running";
      queued ||= s.state === "queued";
      failed ||= s.state === "failed";
      started ||= s.state !== "none";
      done &&= s.state === "done";
    }
    const state = running ? "running" : queued ? "queued" : done ? "done" : failed ? "failed" : started ? "partial" : "none";
    return { state, have, size };
  };

  const show = (d) => {
    const { b, items, name, compact, labelled } = d;
    const s = together(items);
    const pct = s.size ? `${Math.floor((100 * s.have) / s.size)}%` : "";
    const size = formatSize(s.size);
    const many = items.length > 1;
    b.dataset.state = s.state;
    const [icon, label, title] = {
      none: [ARROW, compact ? "" : size, `Download ${name} to read and hear offline (${size})`],
      queued: ["", compact ? "…" : "queued", `${name} is waiting to download: tap to cancel`],
      running: ["", pct, `Downloading ${name} (${pct} of ${size}): tap to pause`],
      partial: [ARROW, pct, `${name} is ${pct} downloaded: tap to go on`],
      failed: [ARROW, compact ? "!" : "retry", `The download of ${name} stopped: tap to try again`],
      done: [TICK, labelled ? "downloaded" : "", `${name} ${many ? "are" : "is"} downloaded (${size}): tap to remove`],
    }[s.state];
    b.innerHTML = icon;
    if (label) b.append(Object.assign(document.createElement("span"), { textContent: label }));
    b.title = title;
    b.setAttribute("aria-label", title);
    b.style.setProperty("--done", s.state === "running" || s.state === "queued" ? pct || "0%" : "0%");
  };
  const showAll = () => {
    downloadButtons.forEach(show);
    document.dispatchEvent(new Event("downloadschange"));
  };

  // On an iPhone or iPad in Safari, before the first download: Safari deletes it after a
  // week without a visit, and the Home Screen app keeps its own copy, so download there
  // instead. Asked once (remembered on the device).
  const homeScreenFirst = () => {
    if (!offline.needsHomeScreen) return true;
    try { if (localStorage.getItem("downloadWarned")) return true; } catch {}
    const ok = confirm("Downloads made in Safari are deleted after a week without a visit, and the Home Screen " +
                       "app keeps its own separate copy. To keep them, tap Share → Add to Home Screen, open the " +
                       "reader from there, and download in it.\n\nDownload here anyway?");
    if (ok) try { localStorage.setItem("downloadWarned", "1"); } catch {}
    return ok;
  };

  // A button for some downloads (null where there are none, or downloads can't be kept):
  // compact, just the icon or progress (a book in the library's grid); labelled, "downloaded"
  // by the tick (the reader's, on its own).
  window.downloadButton = (items, name, { compact = false, labelled = false } = {}) => {
    if (!items.length || !offline.supported) return null;
    const b = document.createElement("button");
    b.className = compact ? "download compact" : "download";
    const d = { b, items, name, compact, labelled };
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const keys = items.map(([key]) => key);
      const { state, size } = together(items);
      if (state === "running" || state === "queued") offline.pause(keys);
      else if (state === "done") {
        if (confirm(`Remove ${name} (${formatSize(size)}) from this device?`)) offline.remove(keys);
      } else if (homeScreenFirst()) {
        offline.download(keys.filter((k) => downloadStatuses.get(k)?.state !== "done"));
      }
    });
    downloadButtons.push(d);
    show(d);
    return b;
  };

  // What the device has, looked at again.
  window.refreshDownloads = async () => {
    if (!offline.supported) return;
    const now = await offline.statuses().catch(() => null);
    if (!now) return;
    downloadStatuses.clear();
    for (const [key, s] of now) downloadStatuses.set(key, s);
    showAll();
  };

  if (offline.supported) {
    offline.onChange((key, status) => {
      if (status.state === "none") downloadStatuses.delete(key);
      else downloadStatuses.set(key, status);
      showAll();
    });
    // Downloads running in another page (another tab): their progress, every few seconds.
    setInterval(() => {
      if (document.hidden || offline.busy()) return;
      if ([...downloadStatuses.values()].some((s) => s.state === "running" || s.state === "queued")) refreshDownloads();
    }, 3000);
  }
}
