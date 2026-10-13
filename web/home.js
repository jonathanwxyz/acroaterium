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
function bookLinks(t, cls) {
  const ul = el("ul", cls);
  const here = bookFor(t);
  for (const b of t.books) {
    const a = el("a", null, cls === "chapters" ? String(b.n) : longLabel(b.label, b.n));
    a.href = readerHref(slugOf(t), b.n);
    if (b.label && cls === "chapters") a.title = b.label;
    if (b.n === here && lastBook(slugOf(t))) a.setAttribute("aria-current", "true");
    const li = el("li");
    li.append(a);
    ul.append(li);
  }
  return ul;
}

function workEntry(t) {
  const work = el("div", "work");
  const named = [el("span", "work-title", t.title), ...(t.author ? [el("span", "work-author", t.author)] : [])];
  if (t.books.length > 1) {
    const list = bookLinks(t, "book-list");
    work.append(foldRow(slugOf(t), "work-head fold-row", named, list), list);
  } else {  // one book: the title opens it
    const head = el("a", "work-head");
    head.href = hrefFor(t);
    head.append(...named);
    work.append(head);
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
      li.append(row, chapters);
    } else {
      const a = el("a", "member-row", t.title);
      a.href = hrefFor(t);
      if (current) a.setAttribute("aria-current", "true");
      li.append(a);
    }
    books.append(li);
  }
  work.append(foldRow(`collection:${c.collection ?? c.title}`, "work-head fold-row",
                      [el("span", "work-title", c.title)], books), books);
  return work;
}

function render(data) {
  root.replaceChildren();
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
    section.append(el("h2", null, cat.name));
    if (!cat.texts.length) section.append(el("p", "empty", "Nothing here yet."));
    for (const t of cat.texts) section.append(t.collection ? collectionEntry(t) : workEntry(t));
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
  .then(async (data) => { const marks = await marksReady; dropSkeleton(); renderMarks(marks); render(data); })
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
