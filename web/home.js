// Library: reading plans, bookmarks, then the texts by category (from /api/library), each linking to the
// reader. A work in several books, or a collection (a Testament), folds out its list
// when its title is tapped; the place last opened in each is marked.
const root = document.getElementById("library");
const marksBox = document.getElementById("bookmarks");
const plansBox = document.getElementById("plans");

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
  box.hidden = !all.length;
  box.replaceChildren();
  if (all.length) {
    const waiting = all.filter((s) => s.state === "running" || s.state === "queued").length;
    const open = el("button", "dl-open", tr("Downloaded: {size}", { size: formatSize(all.reduce((n, s) => n + (s.have ?? 0), 0)) })
                    + (waiting ? ` · ${tr("{n} to go", { n: waiting })}` : "") + " ›");
    open.title = tr("Your downloads: see, pause or remove them");
    open.addEventListener("click", openPanel);
    box.append(open);
    if (offline.needsHomeScreen) {
      box.append(el("div", "dl-note", tr("To keep downloads, add the reader to your Home Screen (Share → Add to Home Screen) and open it from there.")));
    }
  }
  if (panel?.open) fillPanel();
}

// The downloads panel: every download saved or on its way, by name (the library's books),
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
  const head = el("div", "dl-head");
  const close = el("button", "dl-close", "×");
  close.setAttribute("aria-label", tr("Close"));
  close.addEventListener("click", () => panel.close());
  head.append(el("h2", null, tr("Downloads")), el("span", "dl-total", keys.length ? formatSize(total) : ""), close);
  const list = el("ul", "dl-list");
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
  if (!keys.length) list.append(el("li", "dl-empty", tr("Nothing downloaded.")));
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
    const all = el("button", "remove-all", tr("Remove all"));
    let armed = null;
    all.addEventListener("click", () => {
      if (!armed) {  // (a second tap, within a few seconds, to be sure)
        all.classList.add("armed");
        all.textContent = tr("Tap again to remove {size}", { size: formatSize(total) });
        armed = setTimeout(() => { armed = null; all.classList.remove("armed"); all.textContent = tr("Remove all"); }, 4000);
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

// (The author is left out where the category is all one author's: its heading names him.)
function workEntry(t, showAuthor = true) {
  const work = el("div", "work");
  const named = [el("span", "work-title", t.title), ...(t.author && showAuthor ? [el("span", "work-author", t.author)] : [])];
  if (t.books.length > 1) {
    const list = bookLinks(t, "book-list");
    work.append(withDownload(foldRow(slugOf(t), "work-head fold-row", named, list), downloadsOf(t),
                             tr("all of {title}", { title: t.title }), "work-line"), list);
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
const categoryName = (name) => (UI_LANG === "en" ? name : name.split(" · ")[0]);

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
  // What to read, narrowed a step at a time: a category (or all of it), a collection in it
  // (a Testament) or a text, a text in the collection, and of a text in several books (or
  // chapters), from which to which.
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
  const count = el("input");
  Object.assign(count, { type: "number", min: 1, max: 999, value: 30, name: "count", inputMode: "numeric" });
  const unit = el("select");
  unit.name = "unit";
  for (const v of ["days", "weeks", "months", "years"]) unit.append(new Option(tr(v), v));
  const start = el("input");
  Object.assign(start, { type: "date", name: "start", value: isoDate() });
  const field = (label, ...inputs) => {
    const l = el("label", "plan-field");
    const row = el("span", "plan-inputs");
    row.append(...inputs);
    l.append(el("span", "plan-label", label), row);
    return l;
  };
  const preview = el("div", "plan-preview");
  const create = el("button", "plan-create", tr("Start plan"));
  create.type = "submit";
  const memberRow = field("", member), booksRow = field(tr("Books"), from, el("span", "plan-to", tr("to")), to);
  form.append(field(tr("Read"), cat), field("", item), memberRow, booksRow, field(tr("In"), count, unit), field(tr("Starting"), start), preview);
  fillItems();
  fillMembers();
  fillBooks();
  cat.addEventListener("change", () => { fillItems(); fillMembers(); fillBooks(); });
  item.addEventListener("change", () => { fillMembers(); fillBooks(); });
  member.addEventListener("change", fillBooks);
  from.addEventListener("change", () => { if (Number(to.value) < Number(from.value)) to.value = from.value; });
  to.addEventListener("change", () => { if (Number(to.value) < Number(from.value)) from.value = to.value; });
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
  for (const input of [cat, item, member, from, to, unit, start]) input.addEventListener("change", d.update);
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
