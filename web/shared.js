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
