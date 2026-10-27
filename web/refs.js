// References: every verse of the library's texts with a word of one lemma (/api/references),
// by text, a page of verses at a time, each word of the lemma marked and each verse a link
// into the reader. refs.html?lemma=<lemma as the word card gives it>[&in=<part of the library>].
// The part is narrowed as a reading plan's is (home.js): a category, a collection in it (a
// Testament) or a text, a text in the collection, and of a text in several books, from which
// to which; "in" names it as a plan's target does ("category:<name>", "collection:<name>", a
// text's slug, "<slug>:3-5"). Only the parts with the lemma's words are offered, each with
// how many. The lemma's words are found by the server (its lemma index): offline, the page
// says so.
const Q = new URLSearchParams(location.search);
const LEMMA = Q.get("lemma") ?? "";
let within = Q.get("in") ?? Q.get("text");  // the part chosen, or null: all
const PAGE = 100;  // verses fetched at a time

const heading = document.getElementById("refs-lemma");
const count = document.getElementById("refs-count");
const filter = document.getElementById("refs-filter");
const withinLine = document.getElementById("refs-within");
const list = document.getElementById("refs-list");
const status = document.getElementById("refs-status");
const moreButton = document.getElementById("refs-more");

// Majuscule (the Aa menu's Script: shared.js): the lemma, the texts' names and the verses.
keepCarved(document.querySelector("main"),
           (n) => n.parentElement.closest("#refs-lemma, .refs-text, .ref-cite, .ref-verse"), carveGreek);

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const num = (n) => n.toLocaleString(UI_LOCALE);

heading.textContent = LEMMA;
document.title = `${LEMMA} - ${tr("References - Acroaterium")}`;

// Back: to the page that opened this one (the reader, its card), else the library.
document.getElementById("refs-back").addEventListener("click", (e) => {
  if (document.referrer && new URL(document.referrer).origin === location.origin && history.length > 1) {
    e.preventDefault();
    history.back();
  }
});

let texts = [];  // [{slug, title, short, cite, books, n}]: the texts with the lemma, in the library's order
let counts = new Map();  // slug -> the lemma's words in it
let inPart = null;  // the server's "within" for the part shown: {title, n, texts: {slug: n}}, or null
let offset = 0;
let drawnText = null;  // the text of the last verse drawn (a heading before each text's first)

const api = (from) => `/api/references/${encodeURIComponent(LEMMA)}?${new URLSearchParams({
  offset: from, limit: PAGE, ...(within ? { in: within } : {}) })}`;

async function json(request) {
  const r = await request;
  if (r.status === 404) throw Object.assign(new Error("none"), { none: true });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function showError(err) {
  status.textContent = tr(err.none ? "No references to this lemma." : "References need a connection to the server.");
  status.hidden = false;
  moreButton.hidden = true;
}

// A verse's place, as the reader cites it: Γέν. 24:29 (chapter and verse), Ἰλ. 1.75 (book and
// line), Ἀπολ. 17a (a work in one book: its section or line alone).
function citation(t, hit) {
  const name = t.short || t.title;
  if (t.books <= 1 || !hit.ref) return `${name} ${hit.ref ?? ""}`.trim();
  return `${name} ${hit.div}${t.cite === "verse" ? ":" : "."}${hit.ref}`;
}

function drawCount(data) {
  texts = data.texts;
  counts = new Map(texts.map((t) => [t.slug, t.n]));
  count.textContent = texts.length === 1 ? tr("{n} words in 1 text", { n: num(data.n) })
    : tr("{n} words in {texts} texts", { n: num(data.n), texts: num(texts.length) });
}

function drawWithin() {
  withinLine.hidden = !inPart;
  if (inPart) withinLine.textContent = tr("{n} in {where}", { n: num(inPart.n), where: inPart.title });
}

function drawHits(hits) {
  for (const hit of hits) {
    const t = texts.find((x) => x.slug === hit.slug);
    if (!t) continue;
    if (hit.slug !== drawnText) {
      const h = el("h2", "refs-text");
      h.append(el("span", null, t.title), el("span", "refs-text-n", num(inPart?.texts[t.slug] ?? t.n)));
      list.append(h);
      drawnText = hit.slug;
    }
    const row = el("a", "ref");
    row.href = readerHref(hit.slug, hit.div, hit.ref || undefined);
    row.append(el("span", "ref-cite", citation(t, hit)));
    const verse = el("span", "ref-verse");
    verse.lang = "grc";
    const marked = new Set(hit.words);
    hit.content.split(/\s+/).filter(Boolean).forEach((w, i) => {
      if (i) verse.append(" ");
      if (!marked.has(i)) return verse.append(w);
      // (the word marked, not the punctuation around it)
      const [, before, word, after] = w.match(/^([^\p{L}]*)(.*?)([^\p{L}\p{M}’'᾽]*)$/u);
      verse.append(before, el("mark", null, word), after);
    });
    row.append(verse);
    list.append(row);
  }
}

async function load(request, first) {
  moreButton.disabled = true;
  try {
    const data = await json(request);
    if (first) drawCount(data);
    if (!data.offset) {
      inPart = data.within;
      drawWithin();
    }
    drawHits(data.hits);
    if (!data.offset && !data.hits.length) showError({ none: true });  // (none in the part chosen)
    offset = data.offset + data.hits.length;
    moreButton.hidden = !data.more;
    return data;
  } catch (err) {
    showError(err);
  } finally {
    moreButton.disabled = false;
  }
}

// A part chosen (or all): the list again from its start, the address kept in step.
function choose(part) {
  within = part || null;
  const q = new URLSearchParams({ lemma: LEMMA });
  if (within) q.set("in", within);
  history.replaceState(null, "", `?${q}`);
  list.replaceChildren();
  status.hidden = true;
  drawnText = null;
  load(fetch(api(0)), false);
}

moreButton.addEventListener("click", () => load(fetch(api(offset)), false));

// ---- The filter: the library's parts with the lemma's words, narrowed a step at a time ----

// The library (/api/library) as the filter offers it: its categories, each with its texts
// and collections (a collection: its texts), only those with the lemma's words, and how many.
function partsOf(library) {
  const n = (t) => counts.get(t.slug) ?? 0;
  const sum = (xs) => xs.reduce((a, x) => a + x.n, 0);
  return library.categories.map((c) => {
    const items = c.texts.map((x) => {
      if (!x.collection) return { text: x, n: n(x) };
      const members = x.texts.map((t) => ({ text: t, n: n(t) })).filter((m) => m.n);
      return { collection: x.collection, members, n: sum(members) };
    }).filter((x) => x.n);
    return { name: c.name, items, n: sum(items) };
  }).filter((c) => c.n);
}

function buildFilter(library) {
  const cats = partsOf(library);
  if (!cats.length) return;
  const pick = (label) => {
    const s = el("select", "refs-pick");
    s.setAttribute("aria-label", label);
    return s;
  };
  const cat = pick(tr("Where")), item = pick(tr("Where")), member = pick(tr("Where"));
  const from = pick(tr("Books")), to = pick(tr("Books"));
  const option = (label, n, value) => new Option(`${label} (${num(n)})`, value);
  const total = cats.reduce((a, c) => a + c.n, 0);
  cat.append(option(tr("All"), total, ""), ...cats.map((c, i) => option(categoryName(c.name), c.n, i)));
  const books = el("span", "refs-books");
  books.append(from, el("span", "refs-to", tr("to")), to);

  const category = () => cats[cat.value];
  const chosenItem = () => category()?.items.find((x) => (x.collection ? `collection:${x.collection}` : x.text.slug) === item.value);
  // The text chosen, if one is (not a category or collection).
  const chosenText = () => {
    const x = chosenItem();
    if (!x) return null;
    if (x.text) return x.text;
    return x.members.find((m) => m.text.slug === member.value)?.text ?? null;
  };
  const fillItems = () => {
    const c = category();
    item.hidden = !c;
    if (c) {
      item.replaceChildren(option(tr("All"), c.n, `category:${c.name}`),
                           ...c.items.map((x) => (x.collection ? option(x.collection, x.n, `collection:${x.collection}`)
                             : option(x.text.title, x.n, x.text.slug))));
    }
  };
  const fillMembers = () => {
    const x = chosenItem();
    member.hidden = !x?.collection;
    if (x?.collection) member.replaceChildren(option(tr("All"), x.n, item.value), ...x.members.map((m) => option(m.text.title, m.n, m.text.slug)));
  };
  const fillBooks = () => {
    const t = chosenText();
    books.hidden = !(t?.books.length > 1);
    if (books.hidden) return;
    const chapters = t.books.every((b) => /\d$/.test(b.label ?? ""));
    for (const s of [from, to]) s.replaceChildren(...t.books.map((b) => new Option(chapters ? String(b.n) : shortLabel(b.label, b.n), b.n)));
    from.value = t.books[0].n;
    to.value = t.books.at(-1).n;
  };
  // The part chosen, named as a plan's target (null: all).
  const target = () => {
    if (!category()) return null;
    const t = chosenText();
    if (!t) return member.hidden ? item.value : member.value;
    if (books.hidden || (Number(from.value) === t.books[0].n && Number(to.value) === t.books.at(-1).n)) return t.slug;
    return `${t.slug}:${from.value}-${to.value}`;
  };

  // The part the page opened with (?in=), shown as chosen.
  const start = (part) => {
    if (!part) return;
    const [, kind, name] = part.match(/^(category|collection):(.*)$/) ?? [];
    const [, slug, a, b] = kind ? [] : part.match(/^([\w-]+)(?::(\d+)(?:-(\d+))?)?$/) ?? [];
    const has = (x) => (kind === "collection" ? x.collection === name
      : x.text?.slug === slug || x.members?.some((m) => m.text.slug === slug));
    const k = cats.findIndex((c) => (kind === "category" ? c.name === name : c.items.some(has)));
    if (k < 0) return;
    cat.value = k;
    fillItems();
    if (kind === "category") return;
    const x = category().items.find(has);
    item.value = x.collection ? `collection:${x.collection}` : x.text.slug;
    fillMembers();
    if (x.collection && !kind) member.value = slug;
    fillBooks();
    if (a && !books.hidden) {
      from.value = a;
      to.value = b ?? a;
    }
  };

  fillItems();
  fillMembers();
  fillBooks();
  start(within);
  const changed = () => {
    const part = target();
    if (part !== within) choose(part);
  };
  cat.addEventListener("change", () => { fillItems(); fillMembers(); fillBooks(); changed(); });
  item.addEventListener("change", () => { fillMembers(); fillBooks(); changed(); });
  member.addEventListener("change", () => { fillBooks(); changed(); });
  from.addEventListener("change", () => { if (Number(to.value) < Number(from.value)) to.value = from.value; changed(); });
  to.addEventListener("change", () => { if (Number(to.value) < Number(from.value)) from.value = to.value; changed(); });
  filter.append(cat, item, member, books);
  filter.hidden = false;
}

if (!LEMMA) {
  showError({ none: true });
} else {
  load(window.refsRequest ?? fetch(api(0)), true).then(async (data) => {
    if (!data) return;
    try {
      buildFilter(await json(window.libraryRequest ?? fetch("/api/library")));
    } catch {}  // (no filter: the list is still there)
  });
}
