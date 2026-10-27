// References: every verse of the library's texts with a word of one lemma (/api/references),
// by text, a page of verses at a time, each word of the lemma marked and each verse a link
// into the reader. refs.html?lemma=<lemma as the word card gives it>[&text=<slug>: that text only].
// The lemma's words are found by the server (its lemma index): offline, the page says so.
const Q = new URLSearchParams(location.search);
const LEMMA = Q.get("lemma") ?? "";
let only = Q.get("text");  // the text chosen, or null: all
const PAGE = 100;  // verses fetched at a time
const CHIPS = 8;  // texts shown as chips to choose from; more, as a list

const heading = document.getElementById("refs-lemma");
const count = document.getElementById("refs-count");
const textsNav = document.getElementById("refs-texts");
const list = document.getElementById("refs-list");
const status = document.getElementById("refs-status");
const moreButton = document.getElementById("refs-more");

// Majuscule (the Aa menu's Script: shared.js): the lemma, the texts' names and the verses.
keepCarved(document.querySelector("main"),
           (n) => n.parentElement.closest("#refs-lemma, #refs-texts, .refs-text, .ref-cite, .ref-verse"), carveGreek);

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

heading.textContent = LEMMA;
document.title = `${LEMMA} - ${tr("References - Acroaterium")}`;

// Back: to the page that opened this one (the reader, its card), else the library.
document.getElementById("refs-back").addEventListener("click", (e) => {
  if (document.referrer && new URL(document.referrer).origin === location.origin && history.length > 1) {
    e.preventDefault();
    history.back();
  }
});

let texts = [];  // [{slug, title, short, cite, books, n}], in the library's order
let offset = 0;
let drawnText = null;  // the text of the last verse drawn (a heading before each text's first)

const api = (from) => `/api/references/${encodeURIComponent(LEMMA)}?${new URLSearchParams({
  offset: from, limit: PAGE, ...(only ? { text: only } : {}) })}`;

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

function drawTexts(data) {
  texts = data.texts;
  const total = data.n.toLocaleString(UI_LOCALE);
  count.textContent = texts.length === 1 ? tr("{n} words in 1 text", { n: total })
    : tr("{n} words in {texts} texts", { n: total, texts: texts.length.toLocaleString(UI_LOCALE) });
  textsNav.replaceChildren();
  if (texts.length < 2) return;
  textsNav.hidden = false;
  if (texts.length > CHIPS) {  // (many texts: a list to choose from, not a wall of chips)
    const select = el("select", "refs-select");
    select.setAttribute("aria-label", tr("Texts"));
    const option = (label, n, slug) => {
      const o = el("option", null, `${label} (${n.toLocaleString(UI_LOCALE)})`);
      o.value = slug ?? "";
      o.selected = (only ?? null) === slug;
      return o;
    };
    select.append(option(tr("All"), data.n, null), ...texts.map((t) => option(t.title, t.n, t.slug)));
    select.addEventListener("change", () => choose(select.value || null));
    textsNav.append(select);
    return;
  }
  const chip = (label, n, slug) => {
    const b = el("button", "refs-chip");
    b.type = "button";
    b.append(el("span", "refs-chip-name", label), el("span", "refs-chip-n", n.toLocaleString(UI_LOCALE)));
    b.setAttribute("aria-pressed", String((only ?? null) === slug));
    b.addEventListener("click", () => choose(slug));
    return b;
  };
  textsNav.append(chip(tr("All"), data.n, null), ...texts.map((t) => chip(t.short || t.title, t.n, t.slug)));
}

function drawHits(hits) {
  for (const hit of hits) {
    const t = texts.find((x) => x.slug === hit.slug);
    if (!t) continue;
    if (hit.slug !== drawnText) {
      const h = el("h2", "refs-text");
      h.append(el("span", null, t.title), el("span", "refs-text-n", t.n.toLocaleString(UI_LOCALE)));
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
    if (first) drawTexts(data);
    drawHits(data.hits);
    if (!data.offset && !data.hits.length) showError({ none: true });  // (not in the text chosen)
    offset = data.offset + data.hits.length;
    moreButton.hidden = !data.more;
  } catch (err) {
    showError(err);
  } finally {
    moreButton.disabled = false;
  }
}

// A text chosen (or all): the list again from its start, the address kept in step.
function choose(slug) {
  only = slug;
  const q = new URLSearchParams({ lemma: LEMMA });
  if (only) q.set("text", only);
  history.replaceState(null, "", `?${q}`);
  const select = textsNav.querySelector(".refs-select");
  if (select) select.value = only ?? "";
  for (const b of textsNav.querySelectorAll(".refs-chip")) b.setAttribute("aria-pressed", "false");
  textsNav.querySelectorAll(".refs-chip")[only ? texts.findIndex((t) => t.slug === only) + 1 : 0]
    ?.setAttribute("aria-pressed", "true");
  list.replaceChildren();
  status.hidden = true;
  drawnText = null;
  load(fetch(api(0)), false);
}

moreButton.addEventListener("click", () => load(fetch(api(offset)), false));

if (!LEMMA) showError({ none: true });
else load(window.refsRequest ?? fetch(api(0)), true);
