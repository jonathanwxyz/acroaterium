// Shared by the library (home.js) and the reader (app.js): short book labels, the book
// last opened in each text, and bookmarks.

// A book's short name: the numeral letter at the end of its label with its number
// ("Ῥαψῳδία Α" -> "Α (1)"), a chapter's number ("Κεφάλαιον 5" -> "5"), else the label
// itself (or the number).
function shortLabel(label, n) {
  const m = label?.match(/\s(\p{Script=Greek}{1,2})$/u);
  if (m) return `${m[1]} (${n})`;
  return label?.match(/\s(\d+)$/)?.[1] ?? label ?? String(n);
}

// A book's full name, numbered where its label is a numeral letter ("Ῥαψῳδία Α (1)"):
// for menus, and the top bar on wider screens.
function longLabel(label, n) {
  if (!label) return String(n);
  return /\s\p{Script=Greek}{1,2}$/u.test(label) ? `${label} (${n})` : label;
}

// The reader's address for a text's book, optionally at a verse or range (?v=40, ?v=17a-18c).
function readerHref(slug, book, v) {
  const q = new URLSearchParams({ text: slug, book });
  if (v) q.set("v", v);
  return `read.html?${q}`;
}

// The book last opened in each text, and the text last opened in each collection (a
// Testament): the library's titles go back there. Kept per browser.
function lastBook(slug) {
  try { return Number(localStorage.getItem(`lastBook:${slug}`)) || null; } catch { return null; }
}
function lastText(collection) {
  try { return localStorage.getItem(`lastText:${collection}`); } catch { return null; }
}
function rememberVisit(slug, book, collection) {
  try {
    localStorage.setItem(`lastBook:${slug}`, String(book));
    if (collection) localStorage.setItem(`lastText:${collection}`, slug);
  } catch {}
}

// Bookmarks live on the server (data/bookmarks.json), so every device using it sees them.
const bookmarksApi = {
  async list() {
    const r = await fetch("/api/bookmarks");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return (await r.json()).bookmarks;
  },
  async add(mark) {
    const r = await fetch("/api/bookmarks", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(mark),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async remove(id) {
    const r = await fetch(`/api/bookmarks/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`);
  },
};

// Reading plans live on the server too (data/plans.json): a text in so many days, a reading
// a day ({portions: [{label, parts: [{text, book, v, label}]}], start, days, done}). A day
// marked read while offline is kept here and sent when the plans are next fetched.
const plansApi = {
  pending() {
    try { return JSON.parse(localStorage.getItem("planMarks")) ?? []; } catch { return []; }
  },
  keep(marks) {
    try { localStorage.setItem("planMarks", JSON.stringify(marks)); } catch {}
  },
  async send(id, day, done) {
    const r = await fetch(`/api/plans/${encodeURIComponent(id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ day, done }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async list() {
    let left = [];
    for (const m of this.pending()) {
      try { await this.send(m.id, m.day, m.done); } catch (e) { if (e instanceof TypeError) left.push(m); }
    }
    this.keep(left);
    const r = await fetch("/api/plans");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const plans = (await r.json()).plans;
    for (const m of left) {  // (shown as marked, though not yet sent)
      const p = plans.find((q) => q.id === m.id);
      if (p) p.done = m.done ? [...new Set([...p.done, m.day])] : p.done.filter((d) => d !== m.day);
    }
    return plans;
  },
  async mark(id, day, done = true) {
    try {
      return await this.send(id, day, done);
    } catch (e) {
      if (!(e instanceof TypeError)) throw e;
      this.keep([...this.pending().filter((m) => m.id !== id || m.day !== day), { id, day, done }]);
      return null;
    }
  },
  async create(target, days, start) {
    const r = await fetch("/api/plans", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target, days, start }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async remove(id) {
    const r = await fetch(`/api/plans/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`);
  },
};

// Where reading got to in a plan ({day, part, at}: at, the time in the recording, or
// "scroll:<y>"), kept per browser: a day is opened again at it.
function planPlace(id) {
  try { return JSON.parse(localStorage.getItem(`planPlace:${id}`)); } catch { return null; }
}
function setPlanPlace(id, place) {
  try { localStorage.setItem(`planPlace:${id}`, JSON.stringify(place)); } catch {}
}

// A plan's day (1…) and part of it (0…) in the reader; without a part, the part reading got
// to in that day (else its first).
function planHref(plan, day, part) {
  if (part == null) {
    const at = planPlace(plan.id);
    part = at?.day === day && at.part < plan.portions[day - 1].parts.length ? at.part : 0;
  }
  const p = plan.portions[day - 1].parts[part];
  return `${readerHref(p.text, p.book, p.v)}&plan=${plan.id}&day=${day}&part=${part}`;
}
// The day a date (YYYY-MM-DD, local) falls on: today, the plan's first is 1, before it 0 or less.
function isoDate(d = new Date()) {
  const two = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}
function planDayOf(plan, date = isoDate()) {
  const at = (s) => Date.UTC(...s.split("-").map((x, i) => Number(x) - (i === 1 ? 1 : 0)));
  return Math.round((at(date) - at(plan.start)) / 864e5) + 1;
}
// The date of a plan's day, as a short date ("16 Oct").
function planDate(plan, day) {
  const [y, m, d] = plan.start.split("-").map(Number);
  return new Date(y, m - 1, d + day - 1).toLocaleDateString(UI_LOCALE, { day: "numeric", month: "short" });
}
// The first day not yet read (null: all read).
function planNext(plan) {
  const done = new Set(plan.done);
  for (let d = 1; d <= plan.days; d++) if (!done.has(d)) return d;
  return null;
}
// A day's reading, as a plan lists it: in a plan of one text (or some of its books), without
// the text's name (Γένεσις 1–3 is "1–3"; Ἰλιάς Α · Β is "Α · Β").
function planLabel(plan, day) {
  const label = plan.portions[day - 1].label;
  const text = plan.text ?? (plan.target.includes(":") ? null : plan.title);  // (plans made before "text")
  return text && label.startsWith(`${text} `) ? label.slice(text.length + 1).replaceAll(` · ${text} `, " · ") : label;
}
