// Library: reading plans, bookmarks, then the texts by category (from /api/library), each linking to the
// reader. A work in several books, or a collection (a Testament), folds out its list
// when its title is tapped; the place last opened in each is marked.
const root = document.getElementById("library");
const marksBox = document.getElementById("bookmarks");
const plansBox = document.getElementById("plans");

// Majuscule in scriptio continua (the Aa menu's Script: shared.js): the texts' names, their
// books', the bookmarks' words and the plans' readings, their Greek only (a book's number
// stays as it is); not the interface's own words.
const NAMES = ".library h1, .work-title, .work-author, .member-row, .book-list a, .book-list-name, .chapters a, "
  + ".mark-label, .mark-snippet, .plan-reading";
keepCarved(document.querySelector("main"), (n) => n.parentElement.closest(NAMES), carveGreek);

// The page's name, Ἀκροατήριον: its meaning in its tooltip, and in a passing message when
// it's tapped (a phone has nothing to hover with).
for (const named of document.querySelectorAll(".library h1, .library .sub")) {
  named.addEventListener("click", () => toast(document.querySelector(".library h1").title, { long: true }));
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const CHEVRON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" ' +
  'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6"/></svg>';

const slugOf = (t) => t.slug;
// The book to open a text at: the one last opened, if it still has it, else its first.
function bookFor(t) {
  const n = lastBook(slugOf(t));
  return t.books.some((b) => b.n === n) ? n : t.books[0]?.n ?? 1;
}
function hrefFor(t) {
  return readerHref(slugOf(t), bookFor(t));
}
// A collection opens at the text last opened in it (at that text's last book).
function memberFor(c) {
  const slug = lastText(c.collection);
  return c.texts.find((t) => slugOf(t) === slug) ?? c.texts[0];
}

// A work in several books (or a collection, or a book of chapters within it): its whole
// title row is a button that folds its list out under it, with a chevron that turns.
// Open folds are remembered for the visit (sessionStorage), so coming back keeps them.
function foldKey(id) { return `fold:${id}`; }
function isOpen(id) {
  try { return sessionStorage.getItem(foldKey(id)) === "1"; } catch { return false; }
}
function foldRow(id, cls, content, panel) {
  const b = el("button", cls);
  b.append(...content);
  b.insertAdjacentHTML("beforeend", CHEVRON);
  b.lastElementChild.classList.add("fold-mark");
  const open = isOpen(id);
  b.setAttribute("aria-expanded", String(open));
  panel.hidden = !open;
  b.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    b.setAttribute("aria-expanded", String(!panel.hidden));
    try { sessionStorage.setItem(foldKey(id), panel.hidden ? "0" : "1"); } catch {}
  });
  return b;
}

// A text's books as links, by their full names (the one last opened marked); a
// collection's chapters as a grid of numbers.
// Where every book's name starts with the same word (Ῥαψῳδία Α, Ῥαψῳδία Β, …), the word is
// said once, above them, and each is just its letter and number: Α (1).
function bookLinks(t, cls) {
  const ul = el("ul", cls);
  const here = bookFor(t);
  const word = cls === "book-list" && t.books.length > 1 && t.books[0].label?.split(" ")[0];
  const shared = word && t.books.every((b) => b.label?.startsWith(`${word} `)) ? word : null;
  if (shared) {
    ul.classList.add("short");
    ul.append(el("li", "book-list-name", shared));
  }
  for (const b of t.books) {
    const name = shared ? `${b.label.slice(shared.length + 1)} (${b.n})` : longLabel(b.label, b.n);
    const a = el("a", null, cls === "chapters" ? String(b.n) : name);
    if (shared) a.title = longLabel(b.label, b.n);
    a.href = readerHref(slugOf(t), b.n);
    if (b.label && cls === "chapters") a.title = b.label;
    if (b.n === here && lastBook(slugOf(t))) a.setAttribute("aria-current", "true");
    const li = el("li");
    li.append(a);
    // A book with a recording of its own (the Iliad's, the Odyssey's): its own download.
    const own = cls === "book-list" && t.downloads?.find((d) => d.n === b.n);
    const button = own && downloadButton([[`${slugOf(t)}/${b.n}`, own.size]], longLabel(b.label, b.n), { compact: true });
    if (button) li.append(button);
    ul.append(li);
  }
  return ul;
}

// Downloads, to read and hear offline (offline.js), a recording at a time: a book of the
// Iliad or Odyssey, a Bible book, a dialogue. A button at the end of a row (downloadButton,
// offline.js); the rows of a work in several books, and of a Testament, have one for all
// their downloads, which queues them; a book of the Iliad or Odyssey has its own.
const downloadsOf = (t) => (t.downloads ?? []).map((d) => [`${slugOf(t)}/${d.n}`, d.size]);

// A row with a download button (if any) at the end.
function withDownload(row, items, name, cls) {
  const b = downloadButton(items, name);
  if (!b) return row;
  const line = el("div", cls);
  line.append(row, b);
  return line;
}

// What's downloaded, under the heading: the space it takes, and on an iPhone not run from
// the Home Screen, that it should be (Safari otherwise clears it after a week unused).
function showDownloads() {
  const box = document.getElementById("downloads");
  if (!box) return;
  const all = [...downloadStatuses.values()];
  const texts = localKept();
  box.hidden = !all.length && !texts;
  box.replaceChildren();
  if (all.length || texts) {
    const waiting = all.filter((s) => s.state === "running" || s.state === "queued").length;
    const open = el("button", "dl-open", tr("Downloaded: {size}", { size: formatSize(all.reduce((n, s) => n + (s.have ?? 0), texts)) })
                    + (waiting ? ` · ${tr("{n} to go", { n: waiting })}` : "") + " ›");
    open.title = tr("Your downloads: see, pause or remove them");
    open.addEventListener("click", openPanel);
    box.append(open);
    if (offline.needsHomeScreen && all.length) {
      box.append(el("div", "dl-note", tr("To keep downloads, add the reader to your Home Screen (Share → Add to Home Screen) and open it from there.")));
    }
  }
  if (panel?.open) fillPanel();
}

// The downloads panel: the texts of the whole library, where they're kept on this device (all
// in one, with a way to let them go; not in the app, which comes with them); then the
// recordings: every download saved or on its way, by name (the library's books),
// each with its state, a pause or resume button and a remove button; "Resume all", "Pause all",
// "Remove queued" (every download not finished) and "Remove all" at the foot (a second tap to confirm removing all, no
// system dialog).
const downloadNames = new Map();  // "<slug>/<n>" -> [name, size]
let panel = null;
const PAUSE_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/></svg>';
const RESUME_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v11M7 10.5l5 5 5-5M5.5 19.5h13"/></svg>';
const BIN_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M4.5 6.5h15M9.5 6.5V4.5h5v2M6.5 6.5l1 13h9l1-13M10 10v6M14 10v6"/></svg>';
function openPanel() {
  if (!panel) {
    panel = el("dialog");
    panel.id = "dl-panel";
    panel.addEventListener("click", (e) => { if (e.target === panel) panel.close(); });  // (the backdrop)
    document.body.append(panel);
  }
  fillPanel();
  panel.showModal();
}
function fillPanel() {
  const ORDER = { running: 0, queued: 1, failed: 2, partial: 3, done: 4 };
  const keys = [...downloadStatuses.keys()].sort((a, b) =>
    (ORDER[downloadStatuses.get(a).state] ?? 5) - (ORDER[downloadStatuses.get(b).state] ?? 5)
    || (downloadNames.get(a)?.[0] ?? a).localeCompare(downloadNames.get(b)?.[0] ?? b));
  const total = [...downloadStatuses.values()].reduce((n, s) => n + (s.have ?? 0), 0);
  const texts = localKept();
  const head = el("div", "dl-head");
  const close = el("button", "dl-close", "×");
  close.setAttribute("aria-label", tr("Close"));
  close.addEventListener("click", () => panel.close());
  head.append(el("h2", null, tr("Downloads")), el("span", "dl-total", keys.length || texts ? formatSize(total + texts) : ""), close);
  const list = el("ul", "dl-list");
  if (texts) {
    list.append(el("li", "dl-section", tr("Texts")));
    const row = el("li", "dl-row");
    const what = el("span", "dl-name-box");
    what.append(el("span", "dl-name", tr("Every text in the library")),
                el("span", "dl-sub", window.nativeApp ? tr("With their notes and parsing; they come with the app")
                                                        : tr("With their notes and parsing; no recordings")));
    row.append(what, el("span", "dl-state", formatSize(texts)));
    if (!window.nativeApp) {
      const bin = el("button", "icon-btn remove");
      bin.innerHTML = BIN_ICON;
      bin.title = tr("Remove the texts from this device");
      bin.setAttribute("aria-label", bin.title);
      let armed = null;
      bin.addEventListener("click", () => {
        if (!armed) {  // (a second tap, within a few seconds, to be sure)
          bin.classList.add("armed");
          row.classList.add("armed");
          bin.textContent = tr("Tap again to remove {size}", { size: formatSize(texts) });
          armed = setTimeout(() => { armed = null; bin.classList.remove("armed"); row.classList.remove("armed"); bin.innerHTML = BIN_ICON; }, 4000);
          return;
        }
        clearTimeout(armed);
        loadLocalManager().then((m) => m.remove());
      });
      row.append(bin);
    }
    list.append(row);
    if (keys.length) list.append(el("li", "dl-section", tr("Recordings")));
  }
  for (const key of keys) {
    const s = downloadStatuses.get(key);
    const [name, size] = downloadNames.get(key) ?? [key, s.size];
    const pct = size ? `${Math.floor((100 * (s.state === "done" ? size : s.have)) / size)}%` : "";
    const text = { running: tr("{pct} of {size}", { pct, size: formatSize(size) }), queued: tr("queued"), failed: tr("stopped"),
                   partial: s.have ? tr("paused at {pct}", { pct }) : tr("paused"), done: formatSize(size) }[s.state] ?? "";
    const row = el("li", "dl-row");
    row.append(el("span", "dl-name", name), el("span", "dl-state", text));
    if (s.state !== "done") {
      const go = el("button", "icon-btn");
      const waiting = s.state === "running" || s.state === "queued";
      go.innerHTML = waiting ? PAUSE_ICON : RESUME_ICON;
      go.title = waiting ? tr("Pause {name}", { name }) : tr("Resume {name}", { name });
      go.setAttribute("aria-label", go.title);
      go.addEventListener("click", () => (waiting ? offline.pause([key]) : startDownload([key])));
      row.append(go);
    }
    const bin = el("button", "icon-btn remove");
    bin.innerHTML = BIN_ICON;
    bin.title = tr("Remove {name}", { name });
    bin.setAttribute("aria-label", bin.title);
    bin.addEventListener("click", () => offline.remove([key]));
    row.append(bin);
    list.append(row);
  }
  if (!keys.length && !texts) list.append(el("li", "dl-empty", tr("Nothing downloaded.")));
  const foot = el("div", "dl-foot");
  const waiting = keys.filter((k) => ["running", "queued"].includes(downloadStatuses.get(k).state));
  const paused = keys.filter((k) => ["partial", "failed"].includes(downloadStatuses.get(k).state));
  if (paused.length) {
    const resume = el("button", null, tr("Resume all"));
    resume.addEventListener("click", () => startDownload(paused));
    foot.append(resume);
  }
  if (waiting.length) {
    const pause = el("button", null, tr("Pause all"));
    pause.addEventListener("click", () => offline.pause(waiting));
    foot.append(pause);
  }
  const unfinished = keys.filter((k) => downloadStatuses.get(k).state !== "done");
  if (unfinished.length && unfinished.length < keys.length) {  // (with some finished: else it's "Remove all")
    const clear = el("button", "remove-queued", tr("Remove queued"));
    clear.title = tr("Cancel every download not finished (running, waiting or paused), and what it had saved");
    clear.addEventListener("click", () => offline.remove(unfinished));
    foot.append(clear);
  }
  if (keys.length) {
    const label = texts ? tr("Remove all recordings") : tr("Remove all");  // (the texts aren't among them)
    const all = el("button", "remove-all", label);
    let armed = null;
    all.addEventListener("click", () => {
      if (!armed) {  // (a second tap, within a few seconds, to be sure)
        all.classList.add("armed");
        all.textContent = tr("Tap again to remove {size}", { size: formatSize(total) });
        armed = setTimeout(() => { armed = null; all.classList.remove("armed"); all.textContent = label; }, 4000);
        return;
      }
      clearTimeout(armed);
      offline.remove(keys);
    });
    foot.append(all);
  }
  panel.replaceChildren(head, list, foot);
}

document.addEventListener("downloadschange", showDownloads);

// The texts of the whole library kept on this device, to read every one offline (not the
// recordings: local/manager.js; the service worker reads it): offered under the heading,
// with its size; once asked for, the progress; once kept, nothing here but the space it
// takes, in the downloads line, and in the downloads panel, with a way to let it go. The
// manager is loaded only where it's in use: asked for here, or in the app, which always
// keeps it (from its own copy) and so shows nothing here but an update's progress.
const localBox = document.getElementById("local-library");
let localManager = null;
function loadLocalManager() {
  localManager ??= new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "local/manager.js";
    s.onload = () => {
      window.localLibrary.onChange(showLocal);
      ok(window.localLibrary);
    };
    s.onerror = () => { localManager = null; fail(new Error("not loaded")); };
    document.head.append(s);
  });
  return localManager;
}
function localAsked() {
  if (window.nativeApp) return true;
  try { return localStorage.getItem("localLibrary") === "1"; } catch { return false; }
}
let localSize = 0;  // the file's size, as the server gave it (offered with it)
let localStatus = null;
// The size of the texts kept on this device (0: none, or not yet whole).
const localKept = () => (localStatus?.state === "ready" ? localStatus.size : 0);
// (Its size, for the offer: the server's, asked for cheaply, once.)
let sizeAsked = false;
function offerSize() {
  if (sizeAsked) return;
  sizeAsked = true;
  fetch("/api/compact", { method: "HEAD" })
    .then((r) => { if (r.ok) { localSize = Number(r.headers.get("Content-Length")) || 0; showLocal(); } })
    .catch(() => {});
}
function showLocal(st) {
  if (st) localStatus = st;
  const supported = "caches" in window && "serviceWorker" in navigator;
  let state = st?.state ?? (localAsked() ? "checking" : "off");
  if (state === "none" && !localAsked()) state = "off";  // (let go of: offered again)
  localBox.replaceChildren();
  const app = !!window.nativeApp;
  if (state === "off" && supported) {
    if (!localSize) offerSize();
    const keep = el("button", "ll-keep", localSize ? tr("Keep every text offline ({size}, no recordings)", { size: formatSize(localSize) })
                                                    : tr("Keep every text offline (no recordings)"));
    keep.title = tr("Read every text, with its notes and its words' parsing, offline: the recordings are downloaded separately");
    keep.addEventListener("click", () => {
      keep.disabled = true;
      loadLocalManager().then((m) => m.start()).catch(() => { keep.disabled = false; });
    });
    localBox.append(keep);
  } else if (state === "running") {
    localBox.append(tr("Keeping the texts on this device: {pct} of {size}",
                       { pct: `${Math.floor((100 * st.have) / st.size)}%`, size: formatSize(st.size) }));
  } else if (state === "failed") {
    const again = el("button", "ll-remove", tr("Try again"));
    again.addEventListener("click", () => loadLocalManager().then((m) => m.start()));
    localBox.append(tr("The texts couldn't be kept on this device: {error}", { error: st.error }), " · ", again);
  }
  localBox.hidden = !localBox.childNodes.length;
  showDownloads();
}
showLocal();
if (localAsked()) {
  loadLocalManager().then((m) => m.check()).catch(() => {});
}

// (The author is left out where the category is all one author's: its heading names him.)
function workEntry(t, showAuthor = true) {
  const work = el("div", "work");
  markHidden(work, t);
  const named = [el("span", "work-title", t.title), ...(t.author && showAuthor ? [el("span", "work-author", t.author)] : [])];
  if (t.books.length > 1) {
    const list = bookLinks(t, "book-list");
    // (with one download, a book's own button is the only one: "all of" it would be wrong)
    const all = downloadsOf(t).length > 1 ? downloadsOf(t) : [];
    work.append(withDownload(foldRow(slugOf(t), "work-head fold-row", named, list), all,
                             tr("all of {title}", { title: t.title }), "work-line"), list);
  } else {  // one book: the title opens it
    const head = el("a", "work-head");
    head.href = hrefFor(t);
    head.append(...named);
    work.append(withDownload(head, downloadsOf(t), t.title, "work-line"));
  }
  return work;
}

// A hidden text, listed only by a local server (serve.py --show-hidden): greyed.
function markHidden(e, t) {
  if (!t.hidden) return;
  e.classList.add("hidden-text");
  e.title = tr("Hidden: listed only here, not on the live site");
}

// A collection (a Testament): its title row folds out its books; a book of several
// chapters folds out those in turn, the one last read marked; a book in one goes to it.
function collectionEntry(c) {
  const work = el("div", "work collection");
  const member = memberFor(c);
  const books = el("ul", "member-list");
  for (const t of c.texts) {
    const li = el("li", "member");
    markHidden(li, t);
    const current = t === member && lastText(c.collection);
    if (t.books.length > 1) {
      const chapters = bookLinks(t, "chapters");
      const row = foldRow(`chapters:${slugOf(t)}`, "member-row fold-row", [el("span", null, t.title)], chapters);
      if (current) row.setAttribute("aria-current", "true");
      li.append(withDownload(row, downloadsOf(t), t.title, "member-line"), chapters);
    } else {
      const a = el("a", "member-row", t.title);
      a.href = hrefFor(t);
      if (current) a.setAttribute("aria-current", "true");
      li.append(withDownload(a, downloadsOf(t), t.title, "member-line"));
    }
    books.append(li);
  }
  work.append(withDownload(foldRow(`collection:${c.collection ?? c.title}`, "work-head fold-row",
                                   [el("span", "work-title", c.title)], books),
                           c.texts.flatMap(downloadsOf), tr("all of {title}", { title: c.title }), "work-line"), books);
  return work;
}

// The day's readings (the Orthodox lectionary), where the catalogue asks for them: a fold
// at the top of their category, each reading a link to its passage, with what it is read
// as (Ἀπόστολος, Εὐαγγέλιον, …). The day is the reader's own.
const today = () => isoDate();
function lectionaryEntry(l) {
  const work = el("div", "work collection lectionary");
  const list = el("ul", "member-list");
  const note = (text) => list.replaceChildren(el("li", "member reading-note", text));
  note("…");
  work.append(foldRow("lectionary", "work-head fold-row", [el("span", "work-title", l.title)], list), list);
  fetch(`/api/lectionary/${today()}`)
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((data) => {
      if (!data.readings.length) return note(data.error ?? tr("No readings today."));
      list.replaceChildren();
      for (const r of data.readings) {
        const row = el(r.href ? "a" : "span", "member-row reading");
        if (r.href) row.href = r.href;
        row.append(el("span", null, r.label), el("span", "reading-kind", r.kind));
        const li = el("li", "member");
        li.append(row);
        list.append(li);
      }
    })
    .catch(() => note(navigator.onLine === false ? tr("The readings need the connection.") : tr("The readings couldn't be loaded.")));
  return work;
}

// A category's name: in Greek and English ("Ὅμηρος · Homer"), just the Greek in a Greek interface.

function render(data) {
  library = data;
  downloadNames.clear();
  for (const t of data.categories.flatMap((c) => c.texts.flatMap((x) => (x.texts ? x.texts : [x])))) {
    for (const d of t.downloads ?? []) {
      const label = t.downloads.length > 1 ? t.books.find((b) => b.n === d.n)?.label : null;
      downloadNames.set(`${slugOf(t)}/${d.n}`, [label ? `${t.title} ${longLabel(label, d.n)}` : t.title, d.size]);
    }
  }
  root.replaceChildren();
  downloadButtons.length = 0;
  if (data.missing) {
    const p = el("p", "notice");
    p.append("No library yet. Copy ", el("code", null, "library/catalogue.example.json"), " to ",
             el("code", null, "library/catalogue.json"), ", edit it, and run ",
             el("code", null, "python3 scripts/library.py"), ".");
    root.append(p);
    return;
  }
  for (const cat of data.categories) {
    const section = el("section", "category");
    section.append(el("h2", null, capitals(categoryName(cat.name))));
    if (data.lectionary?.category === cat.name) section.append(lectionaryEntry(data.lectionary));
    if (!cat.texts.length) section.append(el("p", "empty", tr("Nothing here yet.")));
    const oneAuthor = cat.texts.length > 1 && new Set(cat.texts.map((t) => t.author ?? null)).size === 1;
    for (const t of cat.texts) section.append(t.collection ? collectionEntry(t) : workEntry(t, !oneAuthor));
    root.append(section);
  }
}

// Reading plans: each with how far it has got (a bar, and the days read against the
// days gone), the next day's reading to go on with, and every day folded out under it (to
// open, or tick off). "New plan" opens a dialog: a text (or a Testament), how long, from when.
const TICK = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
let plans = [];
function renderPlans(list) {
  plans = list;
  plansBox.hidden = false;
  if (shownMarks) renderMarks(shownMarks);  // (a day read, or a plan gone: its bookmarks as others)
  // None yet: just a button to make one, no section.
  plansBox.classList.toggle("no-plans", !list.length);
  if (!list.length) {
    const start = el("button", "plan-start", tr("Create reading plan"));
    start.title = tr("Read a text in the time you choose: a reading a day");
    start.addEventListener("click", openPlanDialog);
    plansBox.replaceChildren(start);
    return;
  }
  const head = el("div", "plans-head");
  const add = el("button", "plan-new", tr("+ New plan"));
  add.addEventListener("click", openPlanDialog);
  head.append(el("h2", null, capitals(tr("Reading plans"))), add);
  plansBox.replaceChildren(head);
  for (const plan of list) plansBox.append(planEntry(plan));
}

function planStatus(plan) {
  const today = planDayOf(plan), done = new Set(plan.done);
  if (done.size >= plan.days) return tr("Finished");
  if (today < 1) return tr("Starts {date}", { date: planDate(plan, 1) });
  let behind = 0;
  for (let d = 1; d < Math.min(today, plan.days + 1); d++) if (!done.has(d)) behind++;  // (today's not yet)
  const ahead = [...done].filter((d) => d > today).length;
  if (today > plan.days) return `${tr("Ended {date}", { date: planDate(plan, plan.days) })} · ${tr("{n} to go", { n: behind })}`;
  const day = tr("Day {n} of {days}", { n: today, days: plan.days });
  if (behind) return `${day} · ${tr("{n} behind", { n: behind })}`;
  return `${day} · ${ahead ? tr("{n} ahead", { n: ahead }) : tr("on schedule")}`;
}

function planEntry(plan) {
  const box = el("div", "work plan");
  const next = planNext(plan);
  const top = el("div", "plan-top");
  const title = el("span", "work-title", plan.title);
  top.append(title, el("span", "plan-length", tr(plan.days === 1 ? "{n} day" : "{n} days", { n: plan.days })), planDelete(plan));
  const bar = el("div", "plan-bar");
  bar.style.setProperty("--done", `${(100 * plan.done.length) / plan.days}%`);
  const expected = Math.min(Math.max(planDayOf(plan), 0), plan.days);
  bar.style.setProperty("--due", `${(100 * expected) / plan.days}%`);
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(plan.days));
  bar.setAttribute("aria-valuenow", String(plan.done.length));
  bar.title = tr("{n} of {days} days read", { n: plan.done.length, days: plan.days });
  const status = el("div", "plan-status");
  status.append(el("span", null, planStatus(plan)), el("span", "plan-count", `${plan.done.length}/${plan.days}`));
  box.append(top, bar, status);
  if (next) {
    const go = el("a", "plan-next");
    go.href = planHref(plan, next);
    go.append(el("span", "plan-day", tr("Day {n}", { n: next })), el("span", "plan-reading", planLabel(plan, next)),
              el("span", "plan-go", tr("Continue ›")));
    box.append(go);
  }
  const days = el("ol", "plan-days");
  const fold = foldRow(`plan:${plan.id}`, "plan-fold fold-row", [el("span", null, tr("All days"))], days);
  box.append(fold, days);
  // (Opened at the next day to read, the list scrolled within itself.)
  const toNext = () => {
    const row = days.children[(next ?? plan.days) - 1];
    if (!days.hidden && row) days.scrollTop = Math.max(0, row.offsetTop - days.clientHeight / 3);
  };
  fold.addEventListener("click", toNext);
  requestAnimationFrame(toNext);
  const fill = () => {
    days.replaceChildren();
    const done = new Set(plan.done), today = planDayOf(plan);
    plan.portions.forEach((_, k) => {
      const day = k + 1;
      const li = el("li", "plan-row");
      if (day === today) li.classList.add("today");
      const tick = el("button", "plan-tick");
      tick.innerHTML = TICK;
      tick.setAttribute("aria-pressed", String(done.has(day)));
      tick.title = done.has(day) ? tr("Day {n}: read (tap to unmark)", { n: day }) : tr("Mark day {n} read", { n: day });
      tick.setAttribute("aria-label", tick.title);
      tick.addEventListener("click", async () => {
        const read = !done.has(day);
        tick.disabled = true;
        try {
          const saved = await plansApi.mark(plan.id, day, read);
          Object.assign(plan, saved ?? { done: read ? [...plan.done, day] : plan.done.filter((d) => d !== day) });
          renderPlans(plans);
        } catch {
          tick.disabled = false;
        }
      });
      const a = el("a", "plan-row-link");
      a.href = planHref(plan, day);
      a.append(el("span", "plan-row-day", `${day} · ${planDate(plan, day)}`), el("span", "plan-row-label", planLabel(plan, day)));
      li.append(tick, a);
      days.append(li);
    });
  };
  fill();
  return box;
}

// Deleting a plan: a bin by its title, which asks again (a second tap within a few
// seconds) before it goes.
function planDelete(plan) {
  const b = el("button", "plan-delete");
  const idle = () => {
    b.classList.remove("armed");
    b.innerHTML = BIN_ICON;
    b.title = tr("Delete the plan for {title}", { title: plan.title });
    b.setAttribute("aria-label", b.title);
  };
  idle();
  let armed = null;
  b.addEventListener("click", async () => {
    if (!armed) {
      b.classList.add("armed");
      b.textContent = tr("Delete?");
      b.title = tr("Tap again to delete this plan");
      armed = setTimeout(() => { armed = null; idle(); }, 4000);
      return;
    }
    clearTimeout(armed);
    b.disabled = true;
    try {
      await plansApi.remove(plan.id);
      renderPlans(plans.filter((p) => p !== plan));
    } catch {
      b.disabled = false;
      b.textContent = tr("Offline?");
    }
  });
  return b;
}

// A dialog's row: its name, and its inputs.
function planField(label, ...inputs) {
  const l = el("label", "plan-field");
  const row = el("span", "plan-inputs");
  row.append(...inputs);
  l.append(el("span", "plan-label", label), row);
  return l;
}

// Part of the library, as a reading plan or a deck of flashcards is of, chosen a step at a
// time: a category (or all of it), a collection in it (a Testament) or a text, a text in
// the collection, and of a text in several books (or chapters), from which to which.
// rows(label): the dialog's rows for it; target(): what's chosen, as /api/plans names it;
// inputs: its selects, to listen to.
function textPicker() {
  const pick = () => el("select", "plan-pick");
  const cat = pick(), item = pick(), member = pick(), from = pick(), to = pick();
  const cats = library.categories.filter((c) => c.texts.length);
  cats.forEach((c, i) => cat.append(new Option(categoryName(c.name), i)));
  const fillItems = () => {
    const c = cats[cat.value];
    item.replaceChildren(new Option(tr("All"), `category:${c.name}`),
                         ...c.texts.map((t) => new Option(t.title, t.collection ? `collection:${t.collection}` : slugOf(t))));
  };
  const chosenText = () => {  // the text chosen, if it's one (not a category or collection)
    const c = cats[cat.value];
    const t = c.texts.find((x) => !x.collection && slugOf(x) === item.value);
    if (t) return t;
    const col = c.texts.find((x) => x.collection && `collection:${x.collection}` === item.value);
    return col?.texts.find((x) => slugOf(x) === member.value) ?? null;
  };
  const fillMembers = () => {
    const col = cats[cat.value].texts.find((x) => x.collection && `collection:${x.collection}` === item.value);
    memberRow.hidden = !col;
    if (col) member.replaceChildren(new Option(tr("All"), item.value), ...col.texts.map((t) => new Option(t.title, slugOf(t))));
  };
  const fillBooks = () => {
    const t = chosenText();
    booksRow.hidden = !(t?.books.length > 1);
    if (booksRow.hidden) return;
    const chapters = t.books.every((b) => /\d$/.test(b.label ?? ""));
    booksRow.querySelector(".plan-label").textContent = chapters ? tr("Chapters") : tr("Books");
    for (const s of [from, to]) s.replaceChildren(...t.books.map((b) => new Option(chapters ? String(b.n) : shortLabel(b.label, b.n), b.n)));
    to.value = t.books.at(-1).n;
  };
  const target = () => {
    const t = chosenText();
    if (!t) return memberRow.hidden ? item.value : member.value;
    if (t.books.length < 2 || (Number(from.value) === t.books[0].n && Number(to.value) === t.books.at(-1).n)) return slugOf(t);
    return `${slugOf(t)}:${from.value}-${to.value}`;
  };
  const memberRow = planField("", member), booksRow = planField(tr("Books"), from, el("span", "plan-to", tr("to")), to);
  const rows = (label) => [planField(label, cat), planField("", item), memberRow, booksRow];
  fillItems();
  fillMembers();
  fillBooks();
  cat.addEventListener("change", () => { fillItems(); fillMembers(); fillBooks(); });
  item.addEventListener("change", () => { fillMembers(); fillBooks(); });
  member.addEventListener("change", fillBooks);
  from.addEventListener("change", () => { if (Number(to.value) < Number(from.value)) to.value = from.value; });
  to.addEventListener("change", () => { if (Number(to.value) < Number(from.value)) from.value = to.value; });
  return { rows, target, chosenText, inputs: [cat, item, member, from, to] };
}

// The new plan dialog: what to read (a text, or the whole of a Testament), in how long (days,
// weeks, months, a year), from when; what each day would be is shown as it's chosen.
let planDialog = null;
function openPlanDialog() {
  if (!library?.categories) return;
  if (!planDialog) planDialog = buildPlanDialog();
  planDialog.showModal();
  planDialog.update();
}
function buildPlanDialog() {
  const d = el("dialog");
  d.id = "plan-dialog";
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
  const head = el("div", "dl-head");
  const close = el("button", "dl-close", "×");
  close.setAttribute("aria-label", tr("Close"));
  close.addEventListener("click", () => d.close());
  head.append(el("h2", null, tr("New reading plan")), close);
  const form = el("form", "plan-form");
  const picked = textPicker(), { target } = picked;
  const count = el("input");
  Object.assign(count, { type: "number", min: 1, max: 999, value: 30, name: "count", inputMode: "numeric" });
  const unit = el("select");
  unit.name = "unit";
  for (const v of ["days", "weeks", "months", "years"]) unit.append(new Option(tr(v), v));
  const start = el("input");
  Object.assign(start, { type: "date", name: "start", value: isoDate() });
  const field = planField;
  const preview = el("div", "plan-preview");
  const create = el("button", "plan-create", tr("Start plan"));
  create.type = "submit";
  form.append(...picked.rows(tr("Read")), field(tr("In"), count, unit), field(tr("Starting"), start), preview);
  const foot = el("div", "dl-foot");
  foot.append(create);
  form.append(foot);
  const days = () => {
    const n = Math.max(1, Math.min(999, Math.floor(Number(count.value)) || 1));
    if (unit.value === "days") return n;
    if (unit.value === "weeks") return n * 7;
    const [y, m, dd] = (start.value || isoDate()).split("-").map(Number);
    const from = Date.UTC(y, m - 1, dd);
    const to = unit.value === "months" ? Date.UTC(y, m - 1 + n, dd) : Date.UTC(y + n, m - 1, dd);
    return Math.round((to - from) / 864e5);
  };
  let asked = 0, shown = null;
  d.update = async () => {
    const ask = ++asked, n = days();
    create.disabled = true;
    // (The last days stay, dimmed, until the new ones come: the dialog keeps its size.)
    if (preview.children.length) preview.classList.add("stale");
    else preview.replaceChildren(el("p", "plan-note", "…"));
    try {
      const r = await fetch(`/api/plans/preview?${new URLSearchParams({ target: target(), days: n })}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const p = await r.json();
      if (ask !== asked) return;
      shown = p;
      const words = Math.round(p.portions.reduce((s, q) => s + q.words, 0) / p.days);
      preview.classList.remove("stale");
      preview.replaceChildren(el("p", "plan-note", tr("{days} days, about {words} words a day", { days: p.days, words: words.toLocaleString(UI_LOCALE) })
                                 + (p.days < n ? ` ${tr("(it can't be split finer than {n})", { n: p.days })}` : "")));
      const ol = el("ol", "plan-sample");
      const shownDays = p.days <= 4 ? p.portions.map((q, i) => [i, q]) : [[0, p.portions[0]], [1, p.portions[1]], null, [p.days - 1, p.portions.at(-1)]];
      for (const entry of shownDays) {
        if (!entry) { ol.append(el("li", "plan-gap", "…")); continue; }
        const li = el("li");
        li.append(el("span", "plan-row-day", tr("Day {n}", { n: entry[0] + 1 })),
                  el("span", "plan-row-label", planLabel({ ...p, target: target() }, entry[0] + 1)));
        ol.append(li);
      }
      preview.append(ol);
      create.disabled = false;
    } catch {
      if (ask === asked) preview.classList.remove("stale");
      if (ask === asked) preview.replaceChildren(el("p", "plan-note", navigator.onLine === false ? tr("Plans need the connection.") : tr("Couldn't work out the days.")));
    }
  };
  for (const input of [...picked.inputs, unit, start]) input.addEventListener("change", d.update);
  let typing = null;
  count.addEventListener("input", () => { clearTimeout(typing); typing = setTimeout(d.update, 300); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!shown) return;
    create.disabled = true;
    try {
      const plan = await plansApi.create(target(), days(), start.value || isoDate());
      d.close();
      renderPlans([...plans, plan]);
    } catch {
      create.disabled = false;
      preview.append(el("p", "plan-note", tr("Couldn't start it (offline?)")));
    }
  });
  d.append(head, form);
  document.body.append(d);
  return d;
}

// The marks of the round buttons (the guide's ?, Leave out's i), drawn as the search button's
// icon is (24 units, strokes of 2), centred whatever the fonts; the circle is the button's border.
const INFO_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="5.8" r="1.6" fill="currentColor"/>'
  + '<path d="M12 10.2v9" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round"/></svg>';
const QUESTION_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.8 8.3a4.2 4.2 0 1 1 6.3 3.64'
  + 'c-1.27.73-2.1 1.68-2.1 3.1v.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
  + 'stroke-linejoin="round"/><circle cx="12" cy="19.4" r="1.3" fill="currentColor"/></svg>';

// Flashcards: the words of part of the library, a text or some of its books, and of a text
// some of its verses, as a deck for Anki (/api/anki: an .apkg), a card per lemma, in the
// order they're first met, with its meaning, where asked its picture and on the back the
// sentence it's first met in. A lemma's note is the same in every deck made, so importing
// decks from one text after another adds only the lemmas not had already. The device counts
// the cards it has made (ANKI_MADE), so each deck's new cards come after the last's in Anki.
const ANKI_MADE = "ankiCardsMade", ANKI_SKIP = "ankiSkip";
let ankiDialog = null;
function openAnkiDialog() {
  if (!library?.categories) return;
  if (!ankiDialog) ankiDialog = buildAnkiDialog();
  ankiDialog.showModal();
  ankiDialog.update();
}
function buildAnkiDialog() {
  const d = el("dialog");
  d.id = "anki-dialog";
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
  const head = el("div", "dl-head");
  const close = el("button", "dl-close", "×");
  close.setAttribute("aria-label", tr("Close"));
  close.addEventListener("click", () => d.close());
  // How to get Anki and bring the decks in: a guide that folds out above the choices (?).
  const helpButton = el("button", "dl-help round-icon");
  helpButton.innerHTML = QUESTION_ICON;
  helpButton.type = "button";
  helpButton.title = tr("How to use these decks in Anki");
  helpButton.setAttribute("aria-label", tr("How to use these decks in Anki"));
  helpButton.setAttribute("aria-expanded", "false");
  head.append(el("h2", null, tr("Flashcards for Anki")), helpButton, close);
  // (The guide takes the panel's place while it's open: ? again, or Back, brings the choices back.)
  const help = ankiHelp(() => showHelp(false));
  const showHelp = (yes) => {
    help.hidden = !yes;
    form.hidden = yes;
    helpButton.setAttribute("aria-expanded", String(yes));
    (yes ? help : form).scrollTop = 0;
  };
  helpButton.addEventListener("click", () => showHelp(help.hidden));
  const form = el("form", "plan-form");
  const picked = textPicker();
  // Some of a text's verses, named as it numbers them (lines, verses, Stephanus sections,
  // chapters; the preview's "parts" says which, and its first and last): "1-50, 72",
  // "3:5-4:12" across books. Not for a text whose parts aren't numbered (a service's).
  const verses = el("input");
  Object.assign(verses, { type: "text", name: "verses", inputMode: "text", spellcheck: false, autocomplete: "off" });
  const versesRow = planField(tr("Verses"), verses);
  const versesHint = el("p", "plan-note anki-hint");
  const check = (name, label, on) => {
    const l = el("label", "anki-option");
    const box = el("input");
    Object.assign(box, { type: "checkbox", name, checked: on });
    l.append(box, el("span", null, label));
    return [l, box];
  };
  const [reverseRow, reverse] = check("reverse", tr("Include reversed cards: from the meaning to the Greek"), true);
  const [picturesRow, pictures] = check("pictures", tr("With pictures (where available)"), false);
  const [sentencesRow, sentences] = check("sentences", tr("With the sentence each is found in, on the back"), false);
  const options = el("div", "anki-options");
  options.append(reverseRow, picturesRow, sentencesRow);
  // The library's commonest so many words left out, for someone who knows them (remembered
  // on the device: what's known doesn't change from deck to deck).
  const skip = el("input");
  Object.assign(skip, { type: "number", name: "skip", min: 0, max: 20000, step: 100, placeholder: "0", inputMode: "numeric" });
  try { skip.value = localStorage.getItem(ANKI_SKIP) || ""; } catch {}
  // What it does, in a little box above the (i): while it's hovered or focused, or after a tap.
  const skipInfo = el("button", "anki-info round-icon");
  skipInfo.type = "button";
  skipInfo.innerHTML = INFO_ICON;
  skipInfo.setAttribute("aria-label", tr("What Leave out does"));
  skipInfo.setAttribute("aria-expanded", "false");
  const skipTip = el("div", "anki-pop");
  skipTip.id = "anki-skip-tip";
  skipTip.setAttribute("role", "tooltip");
  skipInfo.setAttribute("aria-describedby", skipTip.id);
  const [before, after] = tr("Exclude the {x} words which occur most frequently in the entire corpus, to avoid repetition of easy words.").split("{x}");
  skipTip.append(before, el("i", "math", "x"), after ?? "");
  skipTip.hidden = true;
  let pinned = false;
  const tip = (yes) => {
    skipTip.hidden = !yes;
    skipInfo.setAttribute("aria-expanded", String(yes));
    if (!yes) return;
    // Above the (i), its pointer at it; below, where there's no room above; inside the dialog.
    const box = d.getBoundingClientRect();
    skipTip.style.maxWidth = `${Math.min(272, box.width - 16)}px`;
    const r = skipInfo.getBoundingClientRect(), t = skipTip.getBoundingClientRect();
    const below = r.top - t.height - 10 < 8;
    const left = Math.max(box.left + 8, Math.min(r.left + r.width / 2 - t.width / 2, box.right - t.width - 8));
    skipTip.classList.toggle("below", below);
    skipTip.style.left = `${left}px`;
    skipTip.style.top = `${below ? r.bottom + 10 : r.top - t.height - 10}px`;
    skipTip.style.setProperty("--arrow", `${r.left + r.width / 2 - left}px`);
  };
  skipInfo.addEventListener("mouseenter", () => tip(true));
  skipInfo.addEventListener("mouseleave", () => tip(pinned));
  skipInfo.addEventListener("focus", () => tip(true));
  skipInfo.addEventListener("blur", () => { pinned = false; tip(false); });
  skipInfo.addEventListener("click", (e) => { e.preventDefault(); pinned = !pinned || skipTip.hidden; tip(pinned); });
  const skipNote = el("span", "anki-skip-note", tr("commonest words"));
  skipNote.append(" ", skipInfo);  // (in the text: level with its small letters)
  const skipRow = planField(tr("Leave out"), skip, skipNote);
  const preview = el("div", "plan-preview");
  const make = el("button", "plan-create", tr("Download deck"));
  make.type = "submit";
  form.append(...picked.rows(tr("Words of")), versesRow, versesHint, skipRow, options, preview);
  form.addEventListener("scroll", () => { pinned = false; tip(false); }, { passive: true });  // (it'd be left behind)
  const foot = el("div", "dl-foot");
  foot.append(make);
  form.append(foot);
  const skipped = () => Math.max(0, Math.min(20000, Math.floor(Number(skip.value)) || 0));
  const made = () => { try { return Math.max(0, parseInt(localStorage.getItem(ANKI_MADE), 10) || 0); } catch { return 0; } };
  const query = (extra) => new URLSearchParams({ target: picked.target(), v: versesRow.hidden ? "" : verses.value.trim(),
                                                 pictures: pictures.checked ? 1 : 0, skip: skipped(), ...extra });
  verses.placeholder = tr("All");
  const PART_NAMES = { lines: "Lines", verses: "Verses", stephanus: "Stephanus", chapters: "Chapters" };
  const showVerses = (parts) => {  // (undefined: a text, until its preview comes)
    versesRow.hidden = versesHint.hidden = parts === undefined ? !picked.chosenText() : !parts;
    if (!parts) return;
    versesRow.querySelector(".plan-label").textContent = tr(PART_NAMES[parts.kind]);
    const first = parts.first === "0" ? tr("0 (prologue)") : parts.first;
    versesHint.textContent = parts.multi
      ? tr(parts.kind === "verses" ? "From {first} to {last}, as chapter:verse (a verse alone: in the first chapter); a-b for a range, commas between; empty for all"
           : "From {first} to {last}, as book:line (a line alone: in the first book); a-b for a range, commas between; empty for all",
           { first, last: parts.last })
      : tr("From {first} to {last}; a-b for a range, commas between; empty for all", { first, last: parts.last });
  };
  let asked = 0, shown = null, asking = null;
  d.update = async () => {
    const ask = ++asked;
    make.disabled = true;
    shown = null;
    asking?.abort();  // (a whole category's takes a moment: one asked since makes it moot)
    asking = new AbortController();
    if (preview.children.length) preview.classList.add("stale");
    else preview.replaceChildren(el("p", "plan-note", "…"));
    try {
      const r = await fetch(`/api/anki/preview?${query()}`, { signal: asking.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const p = await r.json();
      if (ask !== asked) return;
      showVerses(p.parts);
      preview.classList.remove("stale");
      if (p.none) {
        preview.replaceChildren(el("p", "plan-note", tr("Not in the text: see the range above.")));
        return;
      }
      shown = p;
      const n = (x) => x.toLocaleString(UI_LOCALE);
      let note = tr("{lemmas} cards, one for each lemma of {words} words", { lemmas: n(p.lemmas), words: n(p.words) });
      if (p.pictures != null) note += `, ${tr("{n} with a picture", { n: n(p.pictures) })}`;
      preview.replaceChildren(el("p", "plan-note", `${p.title}: ${note}.`));
      if (p.skipped) preview.append(el("p", "plan-note", tr("{n} of its lemmas are among the {k} commonest, and left out.", { n: n(p.skipped), k: n(skipped()) })));
      const ol = el("ol", "plan-sample anki-sample");
      for (const x of p.sample) {
        const li = el("li");
        li.append(el("span", "anki-lemma", x.lemma), el("span", "plan-row-label", x.definition ?? ""));
        ol.append(li);
      }
      if (p.lemmas > p.sample.length) ol.append(el("li", "plan-gap", "…"));
      // (Homer's nouns and adjectives: their genitives and endings are Attic's, Morpheus's, for now)
      if (p.poetry) preview.append(el("p", "plan-note anki-warning", tr("⚠ Genitives and endings are given in Attic, not as Homer has them: ἡ μῆνις, -εως, where Homer has μήνιος.")));
      preview.append(ol, el("p", "plan-note", tr("Lemmas already in your Acroaterium deck are left as they are: Anki adds only the new ones.")));
      make.disabled = !p.lemmas;
    } catch (e) {
      if (ask !== asked) return;
      preview.classList.remove("stale");
      preview.replaceChildren(el("p", "plan-note", navigator.onLine === false ? tr("Flashcards need the connection.")
        : tr("Couldn't work out the cards.")));
    }
  };
  showVerses();
  // (another text, or other books: verses typed for the last are cleared)
  for (const input of picked.inputs) input.addEventListener("change", () => { verses.value = ""; showVerses(); d.update(); });
  pictures.addEventListener("change", d.update);
  let typing = null;
  verses.addEventListener("input", () => { clearTimeout(typing); typing = setTimeout(d.update, 400); });
  skip.addEventListener("input", () => {
    try { localStorage.setItem(ANKI_SKIP, skipped() ? String(skipped()) : ""); } catch {}
    clearTimeout(typing);
    typing = setTimeout(d.update, 400);
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!shown) return;
    const label = make.textContent;
    make.disabled = true;
    make.textContent = tr("Making the deck…");
    try {
      const r = await fetch(`/api/anki?${query({ sentences: sentences.checked ? 1 : 0, reverse: reverse.checked ? 1 : 0, start: made() })}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const a = el("a");
      a.href = URL.createObjectURL(blob);
      a.download = `Acroaterium - ${shown.title}.apkg`;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 60_000);
      try { localStorage.setItem(ANKI_MADE, String(made() + shown.lemmas)); } catch {}
      toast(tr("Deck saved: open it with Anki to import it."));
    } catch {
      preview.append(el("p", "plan-note", tr("Couldn't make the deck (offline?)")));
    } finally {
      make.textContent = label;
      make.disabled = false;
    }
  });
  d.append(head, help, form, skipTip);
  showHelp(false);
  d.addEventListener("close", () => { showHelp(false); pinned = false; tip(false); });  // (opened again: at the choices)
  document.body.append(d);
  return d;
}
document.getElementById("anki-button")?.addEventListener("click", openAnkiDialog);

// The flashcards' guide: getting Anki, bringing a deck in, setting it up (FSRS), and adding more.
function ankiHelp(back) {
  const box = el("section", "anki-help");
  box.setAttribute("aria-label", tr("How to use these decks in Anki"));
  const link = (text, href) => {
    const a = el("a", null, text);
    Object.assign(a, { href, target: "_blank", rel: "noopener" });
    return a;
  };
  const part = (title, ...lines) => {
    const p = el("div", "anki-help-part");
    p.append(el("h3", null, tr(title)));
    for (const line of lines) {
      const para = el("p");
      for (const bit of [].concat(line)) para.append(typeof bit === "string" ? tr(bit) : bit, " ");
      p.append(para);
    }
    box.append(p);
  };
  part("1. Get Anki",
       ["Anki is a free spaced-repetition based flashcard program. On a computer (Windows, Mac, Linux):",
        link("apps.ankiweb.net", "https://apps.ankiweb.net/")],
       ["On Android:", link("AnkiDroid", "https://play.google.com/store/apps/details?id=com.ichi2.anki"),
        "(free). On iPhone and iPad:", link("AnkiMobile", "https://apps.apple.com/app/ankimobile-flashcards/id373493387"), "(paid)."],
       ["To study on more than one device, make a free AnkiWeb account and press Sync in each.",
        link("ankiweb.net", "https://ankiweb.net/")]);
  part("2. Bring a deck in",
       "Choose the words here and press Download deck: you get an .apkg file.",
       "On a computer, double-click the file, or in Anki choose File › Import. The import window's defaults are right: press Import.",
       "On a phone, open the downloaded file with AnkiDroid or AnkiMobile; or import it on the computer and sync.");
  part("3. Setup",
       ["We recommend using FSRS, which is an improved scheduler over the default. Read this article to set it up:",
        link("How to use FSRS in Anki", "https://slidetoanki.com/blog/how-to-use-fsrs-anki-guide")]);
  part("4. Adding more texts",
       "Make a deck from another text and import it the same way: the lemmas you already have are skipped, so only new words are added, and your progress and your own edits are kept.",
       "To skip the commonest words you already know, use the Leave out box.");
  const done = el("button", "anki-help-back", tr("‹ Back"));
  done.type = "button";
  done.addEventListener("click", back);
  box.append(done);
  return box;
}

// Bookmarks: each opens the reader at its verse; × deletes it.
// One made in a reading plan's day, while that day isn't read: opens the plan's day at
// its verse, and says so.
let shownMarks = null;
function renderMarks(marks) {
  shownMarks = marks;
  marksBox.replaceChildren();
  marksBox.hidden = !marks.length;
  if (!marks.length) return;
  marksBox.append(el("h2", null, capitals(tr("Bookmarks"))));
  const ul = el("ul", "marks");
  for (const m of marks) {
    const li = el("li");
    const a = el("a", "mark");
    a.href = readerHref(m.text, m.book, m.ref);
    const plan = m.plan && plans.find((p) => p.id === m.plan.id);
    const inPlan = plan && m.plan.day <= plan.days && !plan.done.includes(m.plan.day)
      && plan.portions[m.plan.day - 1].parts[m.plan.part];
    const label = el("span", "mark-label", m.label ?? `${m.title ?? m.text} ${m.ref}`);
    if (inPlan) {
      a.href = `${planHref(plan, m.plan.day, m.plan.part)}&mark=${encodeURIComponent(m.ref)}`;
      label.append(el("span", "mark-plan", tr("Reading plan · {title}, day {n}", { title: plan.title, n: m.plan.day })));
    }
    a.append(label);
    if (m.snippet) a.append(el("span", "mark-snippet", m.snippet));
    const del = el("button", "mark-delete", "×");
    del.title = tr("Delete this bookmark");
    del.setAttribute("aria-label", tr("Delete the bookmark {name}", { name: m.label ?? m.ref }));
    del.addEventListener("click", async () => {
      del.disabled = true;
      try {
        await bookmarksApi.remove(m.id);
        shownMarks = shownMarks.filter((x) => x !== m);
        li.remove();
        if (!ul.children.length) marksBox.hidden = true;
      } catch {
        del.disabled = false;
        del.title = tr("Couldn't delete it (offline?)");
      }
    });
    li.append(a, del);
    ul.append(li);
  }
  marksBox.append(ul);
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

// The bookmarks, the plans and the library are shown together, so none pushes the others
// about when it arrives after them. What was shown last is kept on the device and shown at
// once next time (no blank page while the server is asked, coming back from a text), then
// shown again only if the server's has changed.
const SHOWN = "libraryShown";
const marksReady = bookmarksApi.list().catch(() => []);
const plansReady = plansApi.list().catch(() => null);
// What's downloaded, asked for now: the buttons are drawn as they are, not changed after
// (but for those drawn at once from what was shown last).
const downloadsKnown = Promise.race([refreshDownloads(), new Promise((r) => setTimeout(r, 250))]);
// Grey rows until the list comes (they fade in only if it takes a moment).
const dropSkeleton = () => document.querySelector(".lib-skel")?.remove();
{
  const skel = el("div", "lib-skel");
  skel.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 9; i++) {
    const s = el("span", i % 4 === 0 ? "h" : null);
    s.style.setProperty("--w", `${45 + ((i * 29) % 40)}%`);
    skel.append(s);
  }
  plansBox.before(skel);  // above the plans, the bookmarks and the list; removed when they come
}

let shownNow = {};  // {data, marks, plans}: as JSON, what's on the page
function show(next) {
  const json = { data: JSON.stringify(next.data), marks: JSON.stringify(next.marks), plans: JSON.stringify(next.plans) };
  dropSkeleton();
  // (Plans before bookmarks: a bookmark made in a plan is shown with it.)
  if (next.plans && json.plans !== shownNow.plans) renderPlans(next.plans);
  if (json.marks !== shownNow.marks || json.plans !== shownNow.plans) renderMarks(next.marks);
  if (json.data !== shownNow.data) render(next.data);
  shownNow = json;
}
// (Kept as the page is left: with a bookmark deleted or a day ticked off here since.)
addEventListener("pagehide", () => {
  if (!library?.categories) return;  // (before the list: the #library element)
  try {
    localStorage.setItem(SHOWN, JSON.stringify({ data: library, marks: shownMarks ?? [], plans }));
  } catch {}
});
let saved = null;
try { saved = JSON.parse(localStorage.getItem(SHOWN)); } catch {}
if (saved?.data?.categories) {
  show(saved);
  showDownloads();  // (the line under the heading, likewise: so it doesn't push the list down after)
}

function fetchAll(request = fetch("/api/library")) {
  return request
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then(async (data) => {
      const [marks, plans] = await Promise.all([marksReady, plansReady, downloadsKnown]);
      return { data, marks, plans };
    });
}
fetchAll(window.libraryRequest ?? undefined)
  .then(show)
  .catch(async (err) => {
    if (shownNow.data) return;  // (what was shown last stands)
    const [marks, planList] = await Promise.all([marksReady, plansReady]);
    dropSkeleton();
    renderMarks(marks);
    if (planList) renderPlans(planList);
    root.replaceChildren(el("p", "notice", navigator.onLine === false || err instanceof TypeError
      ? tr("The library isn't available offline yet: open it once while online.")
      : tr("Could not load the library: {error}", { error: err.message })));
  });

// Coming back with the back button (a page kept in memory): the places last opened, the
// bookmarks and the plans may have changed. Asked again, and shown where they have.
window.addEventListener("pageshow", (e) => {
  if (!e.persisted) return;
  Promise.all([fetch("/api/library").then((r) => r.json()), bookmarksApi.list(), plansApi.list().catch(() => null)])
    .then(([data, marks, plans]) => show({ data, marks, plans: plans ?? undefined }))
    .catch(() => {});
  refreshDownloads();
});

// Search (the magnifier by the text menu, opening a panel): a lemma as it's typed, accents,
// breathings and capitals aside (/api/lemmas: λογ, ΛΟΓ), or a word of it as printed
// (λογου: λόγος, with λόγου beside it), a few suggested at a time, commonest first, each
// with its words in the library and its meaning; a whole word, every lemma Morpheus finds
// it may be of first, with its analyses (masc gen sg), those the library hasn't greyed;
// the one chosen
// (tapped, or picked with the arrows and Enter: the first, without) opens its references
// (refs.html). The lemmas are the server's: offline, it says so.
{
  const picker = document.getElementById("search-picker");
  const button = document.getElementById("search-button");
  const panel = document.getElementById("search-panel");
  const form = document.getElementById("lemma-search");
  const input = document.getElementById("lemma-input");
  const results = document.getElementById("lemma-results");
  let found = [], active = -1, asked = 0, timer = null;

  const open = (yes) => {
    results.hidden = !yes;
    input.setAttribute("aria-expanded", String(yes));
    if (!yes) setActive(-1);
  };
  function setActive(i) {
    active = i;
    [...results.children].forEach((li, k) => li.setAttribute("aria-selected", String(k === i)));
    if (i >= 0) {
      input.setAttribute("aria-activedescendant", results.children[i].id);
      results.children[i].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }
  const go = (lemma) => { location.href = refsHref(lemma); };

  function draw(items, note) {
    found = items;
    results.replaceChildren();
    items.forEach((x, k) => {
      const li = el("li", "lemma-result");
      li.id = `lemma-result-${k}`;
      li.setAttribute("role", "option");
      const top = el("span", "lemma-result-top");
      const name = el("span", "lemma-result-head");
      name.append(el("span", "lemma-result-name", x.lemma));
      if (x.form) name.append(el("span", "lemma-result-form", x.form));
      top.append(name, el("span", "lemma-result-n", x.n.toLocaleString(UI_LOCALE)));
      li.append(top);
      if (x.parses?.length) li.append(el("span", "lemma-result-parses", x.parses.join(" · ")));
      if (x.definition) {
        const def = el("span", "lemma-result-def");
        if (x.register) def.append(el("span", "lemma-result-label register", capitals(tr(x.register))));  // (a Homeric word)
        def.append(x.definition);
        li.append(def);
      }
      // How the Bible and Homer use it, where that adds to the definition (as on the flashcards).
      for (const [label, text] of x.periods ?? []) {
        const p = el("span", "lemma-result-period");
        p.append(el("span", "lemma-result-label", capitals(tr(label))), text);
        li.append(p);
      }
      li.lang = "grc";
      if (x.absent) {  // (Morpheus's, with no words in the library: nothing to open)
        li.classList.add("lemma-result-absent");
        li.setAttribute("aria-disabled", "true");
        top.lastChild.textContent = tr("not in the library");
      } else {
        li.addEventListener("click", () => go(x.lemma));
      }
      results.append(li);
    });
    if (note) results.append(el("li", "lemma-result-note", note));
    setActive(-1);
    open(items.length > 0 || !!note);
  }

  async function ask() {
    const q = input.value.trim(), n = ++asked;
    if (!q) return draw([]);
    try {
      const r = await fetch(`/api/lemmas?${new URLSearchParams({ q, limit: 10 })}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const items = await r.json();
      if (n === asked) draw(items, items.length ? null : tr("No lemma or word begins so."));
    } catch {
      if (n === asked) draw([], tr("Searching needs a connection to the server."));
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(ask, 120);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!found.length) return;
      e.preventDefault();
      if (results.hidden) open(true);
      const step = e.key === "ArrowDown" ? 1 : -1;  // (round from the last to the first, and back)
      setActive(active < 0 ? (step > 0 ? 0 : found.length - 1) : (active + step + found.length) % found.length);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (!results.hidden) open(false);
      else showPanel(false);
    }
  });
  input.addEventListener("focus", () => { if (found.length) open(true); });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const pick = found[active] ?? found.find((x) => !x.absent);
    if (pick && !pick.absent) go(pick.lemma);
    else ask();
  });
  // The panel: the button opens it (at the box, ready to type) and closes it, as do a tap
  // elsewhere and Escape.
  function showPanel(yes) {
    panel.hidden = !yes;
    button.setAttribute("aria-expanded", String(yes));
    if (yes) {
      input.focus();
      input.select();
      if (found.length) open(true);
    } else {
      open(false);
    }
  }
  button.addEventListener("click", () => showPanel(panel.hidden));
  document.addEventListener("pointerdown", (e) => { if (!panel.hidden && !picker.contains(e.target)) showPanel(false); });
  keepCarved(panel, (n) => n.parentElement.closest(".lemma-result-name, .lemma-result-form"), carveGreek);
}
