// Shared by the library (home.js) and the reader (app.js): short book labels, the book
// last opened in each text, bookmarks, and the text in majuscule.

// Majuscule in scriptio continua (the Aa menu's Script, theme.js): Greek in capitals without
// accents or breathings (an iota subscript written beside, ΤΩΙ), and without spaces or
// punctuation between the words. Only what's shown is turned: each text node keeps what it
// was (shownText reads that back), and the words stay where they were, to be tapped.
const printed = new WeakMap();  // text node -> its text as printed
// The whole of it (the reader's verses).
const carve = (s) => s.normalize("NFD").replace(/\u0345/g, "ι").replace(/[^\p{L}]/gu, "").toUpperCase();
// Its Greek only, a run of Greek words at a time, leaving the rest (a book's number) as it
// is: "Ῥαψῳδία Α (1)" -> "ΡΑΨΩΙΔΙΑΑ (1)" (the library's names).
const carveGreek = (s) => s.replace(/[\p{Script=Greek}][\p{Script=Greek}\p{M}\s’'᾽·.,;:!]*[\p{Script=Greek}\p{M}]|\p{Script=Greek}/gu, carve);
const carving = () => document.documentElement.dataset.letters === "capitals";
function carveIn(root, accept, how) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (accept(n) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
  });
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (printed.has(n)) continue;
    printed.set(n, n.nodeValue);
    n.nodeValue = how(n.nodeValue);
  }
}
function uncarveIn(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!printed.has(n)) continue;
    n.nodeValue = printed.get(n);
    printed.delete(n);
  }
}
// The text nodes under root that accept() takes, turned (how: carve or carveGreek) while
// the Script is majuscule: now, as it's chosen, and as more is drawn.
function keepCarved(root, accept, how) {
  new MutationObserver((changes) => {
    if (!carving()) return;
    for (const c of changes) {
      for (const n of c.addedNodes) {
        if (n.nodeType === 1) carveIn(n, accept, how);
        else if (n.nodeType === 3 && n.parentNode) carveIn(n.parentNode, accept, how);
      }
    }
  }).observe(root, { childList: true, subtree: true });
  document.addEventListener("letterschange", () => (carving() ? carveIn(root, accept, how) : uncarveIn(root)));
  if (carving()) carveIn(root, accept, how);
}
// An element's text as printed, whatever's shown (the word card's heading, a bookmark's
// words), leaving out what's within `skip`.
function shownText(node, skip) {
  let out = "";
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!skip || !n.parentElement.closest(skip)) out += printed.get(n) ?? n.nodeValue;
  }
  return out;
}

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

// Where reading got to in each book of the Iliad was kept as place:book<N> before the Iliad
// joined the library: now place:iliad:<N>, as every text's. Moved over once.
try {
  for (let i = localStorage.length; i--;) {
    const key = localStorage.key(i), m = key?.match(/^place:book(\d+)$/);
    if (!m) continue;
    if (localStorage.getItem(`place:iliad:${m[1]}`) == null) localStorage.setItem(`place:iliad:${m[1]}`, localStorage.getItem(key));
    localStorage.removeItem(key);
  }
} catch {}

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

// Bookmarks and reading plans are kept on this device, in its browser: each device has
// its own, seen by no one else (and lost with the browser's data). The calls are async,
// as they were when the server kept them, and fail (throw) where the browser won't store.
const deviceStore = {
  get(key) {
    try { return JSON.parse(localStorage.getItem(key)) ?? []; } catch { return []; }
  },
  set(key, items) {
    localStorage.setItem(key, JSON.stringify(items));  // (throws where storage is refused)
  },
};
const newId = () => `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 6)}`;

const bookmarksApi = {
  async list() {
    return deviceStore.get("bookmarks");
  },
  // A bookmark ({text, book, ref, title, label, snippet, plan?}), or the one already on that verse.
  async add(mark) {
    const items = deviceStore.get("bookmarks");
    const have = items.find((b) => b.text === mark.text && b.book === mark.book && b.ref === mark.ref);
    if (have) return have;
    const made = { ...mark, id: newId(), created: Math.floor(Date.now() / 1000) };
    deviceStore.set("bookmarks", [made, ...items]);
    return made;
  },
  async remove(id) {
    deviceStore.set("bookmarks", deviceStore.get("bookmarks").filter((b) => b.id !== id));
  },
};

// Reading plans: a text (or a Testament) in so many days, a reading a day ({portions:
// [{label, parts: [{text, book, v, label}]}], start, days, done}). The server works out
// the days (/api/plans/preview); the plan is kept here.
const plansApi = {
  async list() {
    return deviceStore.get("plans");
  },
  // The plan with a day marked read (or not).
  async mark(id, day, done = true) {
    const plans = deviceStore.get("plans");
    const plan = plans.find((p) => p.id === id);
    if (!plan) throw new Error("no such plan");
    plan.done = done ? [...new Set([...plan.done, day])].sort((a, b) => a - b) : plan.done.filter((d) => d !== day);
    deviceStore.set("plans", plans);
    return plan;
  },
  async create(target, days, start) {
    const r = await fetch(`/api/plans/preview?${new URLSearchParams({ target, days })}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const split = await r.json();
    const plan = { id: newId(), target, title: split.title, start, days: split.portions.length,
                   created: Math.floor(Date.now() / 1000), portions: split.portions, done: [],
                   ...(split.text ? { text: split.text } : {}) };
    deviceStore.set("plans", [...deviceStore.get("plans"), plan]);
    return plan;
  },
  async remove(id) {
    deviceStore.set("plans", deviceStore.get("plans").filter((p) => p.id !== id));
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

// A passing message, in the page's #toast: a line (a bookmark added, the end mode in words),
// or with long, a few lines held longer (the library's name, what it means).
let toastTimer = 0;
function toast(text, { long = false } = {}) {
  const box = document.getElementById("toast");
  if (!box) return;
  box.textContent = text;
  box.classList.toggle("long", long);
  box.hidden = false;
  box.classList.remove("out");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    box.classList.add("out");
    toastTimer = setTimeout(() => { box.hidden = true; }, 250);
  }, long ? 5000 : 1800);
}
