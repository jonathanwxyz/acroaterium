// Library: bookmarks, then the texts by category (from /api/library), each linking to the
// reader. A work in several books, or a collection (a Testament), folds out its list
// when its title is tapped; the place last opened in each is marked.
const root = document.getElementById("library");
const marksBox = document.getElementById("bookmarks");

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const CHEVRON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" ' +
  'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6"/></svg>';

const slugOf = (t) => (t.reader === "iliad" ? "iliad" : t.slug);
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
    const own = cls === "book-list" && t.downloads?.length > 1 && t.downloads.find((d) => d.n === b.n);
    const button = own && downloadButton([[`${slugOf(t)}/${b.n}`, own.size]], longLabel(b.label, b.n), true);
    if (button) li.append(button);
    ul.append(li);
  }
  return ul;
}

// Downloads, to read and hear offline (offline.js), a recording at a time: a book of the
// Iliad or Odyssey, a Bible book, a dialogue. A button at the end of a row shows the size,
// then the progress (tap to pause, and again to go on), then a tick (tap to remove). The
// rows of a work in several books, and of a Testament, have one for all their downloads,
// which queues them; a book of the Iliad or Odyssey has its own.
const ARROW = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>';
const TICK = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" ' +
  'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const statuses = new Map();  // "<slug>/<n>" -> {state, have, size}
let downloadButtons = [];  // {b, items: [[key, size], ...], name, compact}

const downloadsOf = (t) => (t.downloads ?? []).map((d) => [`${slugOf(t)}/${d.n}`, d.size]);

// A button's downloads together: running (or queued) if any is, done if all are.
function together(items) {
  let have = 0, size = 0, running = false, queued = false, failed = false, started = false, done = true;
  for (const [key, bytes] of items) {
    const s = statuses.get(key) ?? { state: "none" };
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
}

function showDownload(d) {
  const { b, items, name, compact } = d;
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
    done: [TICK, "", `${name} ${many ? "are" : "is"} downloaded (${size}): tap to remove`],
  }[s.state];
  b.innerHTML = icon;
  if (label) b.append(el("span", null, label));
  b.title = title;
  b.setAttribute("aria-label", title);
  b.style.setProperty("--done", s.state === "running" || s.state === "queued" ? pct || "0%" : "0%");
}

// On an iPhone or iPad in Safari, before the first download: Safari deletes it after a week
// without a visit, and the Home Screen app keeps its own copy, so download there instead.
// Asked once (remembered on the device).
function homeScreenFirst() {
  if (!offline.needsHomeScreen) return true;
  try { if (localStorage.getItem("downloadWarned")) return true; } catch {}
  const ok = confirm("Downloads made in Safari are deleted after a week without a visit, and the Home Screen " +
                     "app keeps its own separate copy. To keep them, tap Share → Add to Home Screen, open the " +
                     "reader from there, and download in it.\n\nDownload here anyway?");
  if (ok) try { localStorage.setItem("downloadWarned", "1"); } catch {}
  return ok;
}

function downloadButton(items, name, compact = false) {
  if (!items.length || !offline.supported) return null;
  const b = el("button", compact ? "download compact" : "download");
  const d = { b, items, name, compact };
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    const keys = items.map(([key]) => key);
    const { state, size } = together(items);
    if (state === "running" || state === "queued") offline.pause(keys);
    else if (state === "done") {
      if (confirm(`Remove ${name} (${formatSize(size)}) from this device?`)) offline.remove(keys);
    } else if (homeScreenFirst()) {
      offline.download(keys.filter((k) => statuses.get(k)?.state !== "done"));
    }
  });
  downloadButtons.push(d);
  showDownload(d);
  return b;
}

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
  const all = [...statuses.values()];
  box.hidden = !all.length;
  box.replaceChildren();
  if (!all.length) return;
  const waiting = all.filter((s) => s.state === "running" || s.state === "queued").length;
  box.append(`Downloaded: ${formatSize(all.reduce((n, s) => n + (s.have ?? 0), 0))}`
             + (waiting ? ` · ${waiting} to go` : ""));
  if (offline.needsHomeScreen) {
    box.append(el("br"), "To keep downloads, add the reader to your Home Screen (Share → Add to Home Screen) and open it from there.");
  }
}

if (offline.supported) {
  offline.onChange((key, status) => {
    if (status.state === "none") statuses.delete(key);
    else statuses.set(key, status);
    for (const d of downloadButtons) if (d.items.some(([k]) => k === key)) showDownload(d);
    showDownloads();
  });
}

// (The author is left out where the category is all one author's: its heading names him.)
function workEntry(t, showAuthor = true) {
  const work = el("div", "work");
  const named = [el("span", "work-title", t.title), ...(t.author && showAuthor ? [el("span", "work-author", t.author)] : [])];
  if (t.books.length > 1) {
    const list = bookLinks(t, "book-list");
    work.append(withDownload(foldRow(slugOf(t), "work-head fold-row", named, list), downloadsOf(t),
                             `all of ${t.title}`, "work-line"), list);
  } else {  // one book: the title opens it
    const head = el("a", "work-head");
    head.href = hrefFor(t);
    head.append(...named);
    work.append(withDownload(head, downloadsOf(t), t.title, "work-line"));
  }
  return work;
}

// A collection (a Testament): its title row folds out its books; a book of several
// chapters folds out those in turn, the one last read marked; a book in one goes to it.
function collectionEntry(c) {
  const work = el("div", "work collection");
  const member = memberFor(c);
  const books = el("ul", "member-list");
  for (const t of c.texts) {
    const li = el("li", "member");
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
                           c.texts.flatMap(downloadsOf), `all of ${c.title}`, "work-line"), books);
  return work;
}

// The day's readings (the Orthodox lectionary), where the catalogue asks for them: a fold
// at the top of their category, each reading a link to its passage, with what it is read
// as (Ἀπόστολος, Εὐαγγέλιον, …). The day is the reader's own.
function today() {
  const d = new Date(), two = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}
function lectionaryEntry(l) {
  const work = el("div", "work collection lectionary");
  const list = el("ul", "member-list");
  const note = (text) => list.replaceChildren(el("li", "member reading-note", text));
  note("…");
  work.append(foldRow("lectionary", "work-head fold-row", [el("span", "work-title", l.title)], list), list);
  fetch(`/api/lectionary/${today()}`)
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((data) => {
      if (!data.readings.length) return note(data.error ?? "No readings today.");
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
    .catch(() => note(navigator.onLine === false ? "The readings need the connection." : "The readings couldn't be loaded."));
  return work;
}

// A heading in capitals, as Greek is written in capitals: without its accents, breathings
// and iota subscript: Η ΑΓΙΑ ΓΡΑΦΗ.
function capitals(text) {
  return text.normalize("NFD").replace(/[\u0300\u0301\u0313\u0314\u0342\u0345]/g, "").normalize("NFC").toUpperCase();
}

function render(data) {
  root.replaceChildren();
  downloadButtons = [];
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
    section.append(el("h2", null, capitals(cat.name)));
    if (data.lectionary?.category === cat.name) section.append(lectionaryEntry(data.lectionary));
    if (!cat.texts.length) section.append(el("p", "empty", "Nothing here yet."));
    const oneAuthor = cat.texts.length > 1 && new Set(cat.texts.map((t) => t.author ?? null)).size === 1;
    for (const t of cat.texts) section.append(t.collection ? collectionEntry(t) : workEntry(t, !oneAuthor));
    root.append(section);
  }
}

// Bookmarks: each opens the reader at its verse; × deletes it.
function renderMarks(marks) {
  marksBox.replaceChildren();
  marksBox.hidden = !marks.length;
  if (!marks.length) return;
  marksBox.append(el("h2", null, "Bookmarks"));
  const ul = el("ul", "marks");
  for (const m of marks) {
    const li = el("li");
    const a = el("a", "mark");
    a.href = readerHref(m.text, m.book, m.ref);
    a.append(el("span", "mark-label", m.label ?? `${m.title ?? m.text} ${m.ref}`));
    if (m.snippet) a.append(el("span", "mark-snippet", m.snippet));
    const del = el("button", "mark-delete", "×");
    del.title = "Delete this bookmark";
    del.setAttribute("aria-label", `Delete the bookmark ${m.label ?? m.ref}`);
    del.addEventListener("click", async () => {
      del.disabled = true;
      try {
        await bookmarksApi.remove(m.id);
        li.remove();
        if (!ul.children.length) marksBox.hidden = true;
      } catch {
        del.disabled = false;
        del.title = "Couldn't delete it (offline?)";
      }
    });
    li.append(a, del);
    ul.append(li);
  }
  marksBox.append(ul);
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

// The bookmarks and the library are shown together, so the bookmarks don't push the
// list down when they arrive after it.
const marksReady = bookmarksApi.list().catch(() => []);
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
  marksBox.before(skel);  // above the bookmarks and the list; removed when they come
}
(window.libraryRequest ?? fetch("/api/library"))
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
  .then(async (data) => {
    const marks = await marksReady;
    dropSkeleton();
    renderMarks(marks);
    render(data);
    // What's downloaded, once the device says (the list doesn't wait for it).
    if (offline.supported) {
      offline.statuses().then((saved) => {
        for (const [key, s] of saved) statuses.set(key, s);
        downloadButtons.forEach(showDownload);
        showDownloads();
        offline.resume();
      }).catch(() => {});
    }
  })
  .catch(async (err) => {
    const marks = await marksReady;
    dropSkeleton();
    renderMarks(marks);
    root.replaceChildren(el("p", "notice", navigator.onLine === false || err instanceof TypeError
      ? "The library isn't available offline yet: open it once while online."
      : `Could not load the library: ${err.message}`));
  });

// Coming back with the back button (a page kept in memory): the places last opened, and
// the bookmarks, may have changed.
window.addEventListener("pageshow", (e) => {
  if (!e.persisted) return;
  bookmarksApi.list().then(renderMarks).catch(() => {});
  fetch("/api/library").then((r) => r.json()).then(render).catch(() => {});
});
