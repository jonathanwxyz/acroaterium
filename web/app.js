// Reader: shows one book of a text and, where there is a recording, highlights the verse
// being recited. read.html?text=<slug>&book=<n>; the Iliad (text=iliad, the default) comes from
// its own API, with Gaza's paraphrase, translations, scholia and parsing. Library texts
// have only what their data offers (data.features); prose (Plato) is set as paragraphs
// with its sections (Stephanus) marked.
//
// &v= opens the book at a verse, a line ("40", or "1.40") or a section ("17a"; "17" is its
// first), or shows a range of them alone ("40-60", "17a-18c": "18" ends after all of 18).
const PARAMS = new URLSearchParams(location.search);
const TEXT = PARAMS.get("text") || "iliad";
const BOOK = Number(PARAMS.get("book") || 1);
const [V_FROM, V_TO] = (PARAMS.get("v") ?? "").split(/[-–]/).map((s) => s.trim());
// &at=: carried on from the chapter before, playing (the chapters of a book of the Bible
// share one recording): start there, and play.
const CARRY_AT = Number(PARAMS.get("at")) || null;
let RANGE = null;  // {from, to}: the refs of the first and last verse shown, when showing a range
// A selection of verses in pieces, maybe across chapters (a reading of the lectionary):
// "20-21,40-52", "33-40,12.1-2", "2-5.7". Each end is a verse or chapter.verse; a verse
// alone is in the chapter last named (at first, the book's). A verse numbered from
// another chapter keeps its own ref ("24.31:8", Rahlfs's Proverbs). Bible texts only.
const SELECTION = (() => {
  const spec = PARAMS.get("v") ?? "";
  if (TEXT === "iliad" || !/[,;:]|[-–]\s*\d+\.\d/.test(spec)) return null;
  let ch = BOOK;
  const end = (s) => {
    const m = s.trim().match(/^(?:(\d+)\.)?(\d+(?::\d+)?[a-z]?)$/);
    if (!m) return null;
    if (m[1]) ch = Number(m[1]);
    return { ch, v: m[2] };
  };
  const pieces = [];
  for (const part of spec.split(/[,;]/)) {
    const [a, b, extra] = part.split(/[-–]/);
    const from = end(a), to = b == null ? from : end(b);
    if (!from || !to || extra != null || to.ch < from.ch) return null;
    pieces.push({ from, to });
  }
  return pieces;
})();
// Old links (?book=N, the Iliad) show the full address: ?text=iliad&book=N.
if (!PARAMS.has("text") || CARRY_AT) {
  history.replaceState(null, "", `?${new URLSearchParams({ text: TEXT, book: BOOK, ...(V_FROM ? { v: PARAMS.get("v") } : {}) })}${location.hash}`);
}
const API = TEXT === "iliad" ? `/api/book/${BOOK}` : `/api/text/${encodeURIComponent(TEXT)}/${BOOK}`;
let hasScholia = TEXT === "iliad";  // known for sure once the book has loaded
const FEATURES = ["audio", "paraphrase", "modern", "translation", "scholia", "words"];
// Speaker icon shown beside a verse on hover: clicking the row (not a word) goes to it.
const SEEK_ICON = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">' +
  '<path fill="currentColor" d="M4 9v6h4l5 4V5L8 9H4z"/>' +
  '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
  'd="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12"/></svg>';
// Below the width that fits the scholia column, a button on each verse with scholia opens
// them in a popup instead.
const SCHOLIA_ICON = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
  '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M4 5h16v11H10l-4 3.5V16H4z"/>' +
  '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M8 9h8M8 12.3h5"/></svg>';

const audio = document.getElementById("audio");
const list = document.getElementById("verses");
const position = document.getElementById("position");
const follow = document.getElementById("follow");
const showParaphrase = document.getElementById("show-paraphrase");
const showTranslation = document.getElementById("show-translation");
const showModern = document.getElementById("show-modern");
const gloss = document.getElementById("gloss");
const holdOnHover = document.getElementById("hold-on-hover");
const focused = document.getElementById("focused");
const card = document.getElementById("card");
const cardBody = document.getElementById("card-body");
const bookPicker = document.getElementById("book-picker");
const bookSelect = document.getElementById("book-select");
const bookMenu = document.getElementById("book-menu");

let DATA = null;   // the book as loaded
let verses = [];   // {line, text, start, speechEnd, end, el}, timed ones in recording order
let refs = [];     // {ref, el, entry}: every line or section start, for the position box
let current = -1;  // index into verses
let lastUserScroll = 0;
let passages = [];    // {from, to, el}: Murray's English passages
let activePassage = null;

// Pause on hover: hold playback at the end of the verse whose text the pointer is on.
const entryOf = new WeakMap();  // verse <li> -> its entry in verses
let hovered = null;   // verse entry under the pointer
let heldBy = null;    // verse we paused at because it was hovered
let released = null;  // verse the reader resumed by hand while hovering: don't hold it again

function render(data) {
  DATA = data;
  list.replaceChildren();  // the loading skeleton
  list.classList.remove("loading");
  if (V_TO && !SELECTION) applyRange(data);
  rememberVisit(TEXT, BOOK, data.collection);
  try { localStorage.setItem(`meta:${TEXT}`, JSON.stringify({ title: data.title, eyebrow: data.eyebrow, short: data.short, books: data.books })); } catch {}
  const has = new Set(data.features ?? []);
  for (const f of FEATURES) document.body.classList.toggle(`no-${f}`, !has.has(f));
  // Focused mode shows whole rows: not for prose, where a sentence is what plays.
  document.body.classList.toggle("prose-text", data.form === "prose");
  if (!has.has("audio") || data.form === "prose") document.body.classList.remove("focused");
  list.classList.toggle("prose", data.form === "prose");
  showTitle(data.eyebrow ?? data.title, data.short ?? data.title);
  fillTitles(data.collectionTexts);
  const bookLabel = data.books.find((b) => b.n === data.book)?.label;
  document.title = `${data.title}${bookLabel ? ` · ${bookLabel}` : ""} — Reader`;
  fillBooks(data.books);
  document.getElementById("credits").replaceChildren(...(data.credits ?? []).map((c) => {
    const p = el("p", "credit", c.text);
    if (c.id) p.id = c.id;
    return p;
  }));
  hasScholia = has.has("scholia");
  document.querySelector(".sch-credit").textContent = data.notesCredit ?? "";
  if (data.audio) audio.src = data.audio;
  showDownloadButton(data, bookLabel);
  if (data.form === "prose") renderProse(data, has);
  else renderVerse(data, has);
  applyScholia();
  const first = refs.find((r) => r.ref && r.ref !== "0")?.ref;
  if (!data.audio) position.placeholder = data.cite === "section" ? `go to ${first}` : "go to line";
  if (data.cite === "section") position.title = `Type a section (e.g. ${first}) and press Enter`;
}

// The book's download (offline.js), in the top bar beside the bookmark: this book of
// the Iliad or Odyssey, the whole book a chapter of the Bible is in (they share a
// recording), or the dialogue; as the library shows it, and with the same control.
function showDownloadButton(data, bookLabel) {
  const slot = document.getElementById("download-slot");
  if (!slot || typeof downloadButton !== "function") return;
  const d = data.download;
  const name = !d ? "" : d.books > 1 ? `${data.title} (all ${d.books} chapters)`
    : bookLabel ? `${data.title} ${longLabel(bookLabel, data.book)}` : data.title;
  const b = d && downloadButton([[`${data.text}/${d.n}`, d.size]], name, { compact: true });
  slot.replaceChildren(...(b ? [b] : []));
  slot.hidden = !b;
  for (let i = downloadButtons.length; i--;) if (!downloadButtons[i].b.isConnected) downloadButtons.splice(i, 1);
  if (b) refreshDownloads();
}

// A range (?v=40-60): keep only its verses, and say so above them, with a way back to the
// whole book. A range that doesn't match is ignored.
function applyRange(data) {
  const prose = data.cite === "section";
  let section = null;  // prose: the section each segment falls in
  const cites = data.verses.map((v) => (v.line > 0 ? (section = v.ref ?? (prose ? section : String(v.line))) : null));
  const matches = (typed) => {
    if (prose) {
      const m = typed.toLowerCase().replace(/[\s.§]+/g, "").match(/^(\d+)([a-e]?)$/);
      return m && ((c) => c != null && (m[2] ? c === m[1] + m[2] : c.replace(/[a-e]$/, "") === m[1]));
    }
    const m = typed.match(/^(?:(\d+)\.)?(\d+)$/);
    // (a verse takes in its additions: 18 is 18a-d too)
    return m && (!m[1] || Number(m[1]) === BOOK) && ((c) => c != null && /^\d+[a-z]*$/.test(c) && parseInt(c, 10) === Number(m[2]));
  };
  const isFrom = matches(V_FROM), isTo = matches(V_TO);
  if (!isFrom || !isTo) return;
  const from = cites.findIndex(isFrom);
  let to = cites.findLastIndex(isTo);
  if (to < 0 && !prose && Number(V_TO.split(".").at(-1)) > Number(cites.at(-1))) to = cites.length - 1;  // past the end
  if (from < 0 || to < from) return;
  const first = cites[from], last = cites[to];
  data.verses = data.verses.slice(from, to + 1);
  data.translation = (data.translation ?? []).filter((p) => p.to >= Number(first) && p.from <= Number(last));
  RANGE = { from: first, to: last };
  const verse = data.cite === "verse";  // the Bible: chapter:verse, in its chapter
  const span = first === last ? first : `${first}–${last}`;
  const note = el("p", "range-note", `${verse ? `${BOOK}:${span}` : `${prose ? "Sections" : "Lines"} ${span}`} · `);
  const whole = el("a", null, verse ? "whole chapter" : "whole book");
  whole.href = readerHref(TEXT, BOOK);
  note.append(whole);
  document.getElementById("credits").before(note);
}

// A selection: its pieces' verses, from as many chapters as they reach (the chapters of a
// book share one recording), in order; the last verse of a piece is marked cut, so the
// recording goes straight on to the next piece. One that doesn't match shows the chapter.
async function select(data) {
  const chapters = new Map([[BOOK, data]]);
  for (const { from, to } of SELECTION) {
    for (let c = from.ch; c <= to.ch; c++) {
      if (!chapters.has(c)) {
        const r = await fetch(`/api/text/${encodeURIComponent(TEXT)}/${c}`);
        if (!r.ok) return data;
        chapters.set(c, await r.json());
      }
    }
  }
  // "12" is verse 12 and its additions (12a, 12b); "12a" or "31:10" just that ref.
  const at = (end) => (v) => (/^\d+$/.test(end.v) ? v.ref?.match(/^\d+/)?.[0] === end.v && !v.ref.includes(":") : v.ref === end.v);
  const picked = [], runs = [];
  for (const { from, to } of SELECTION) {
    for (let c = from.ch; c <= to.ch; c++) {
      const vs = chapters.get(c).verses.filter((v) => v.line > 0);
      const i = c === from.ch ? vs.findIndex(at(from)) : 0;
      const j = c === to.ch ? vs.findLastIndex(at(to)) : vs.length - 1;
      if (i < 0 || j < i) return data;
      const before = picked.length ? picked.at(-1).div ?? BOOK : BOOK;
      vs.slice(i, j + 1).forEach((v, k) => picked.push({
        ...v, ...(c !== BOOK ? { div: c } : {}),
        ...(k === 0 && c !== before && !v.ref.includes(":") ? { label: `${c}:${v.ref}` } : {}),  // where another chapter begins
      }));
      picked.at(-1).jump = true;  // (the next piece is elsewhere: skipSilence goes straight to it)
      if (c === from.ch) runs.push([picked.length - (j - i + 1), picked.length - 1]);
      else runs.at(-1)[1] = picked.length - 1;
    }
  }
  delete picked.at(-1).jump;
  data.verses = picked;
  data.translation = [];
  RANGE = { from: picked[0].ref, to: picked.at(-1).ref };
  // The note: the pieces as chapter:verse, a piece's end without its chapter where it's the same.
  const place = (v) => (v.ref.includes(":") ? v.ref : `${v.div ?? BOOK}:${v.ref}`);
  const shown = runs.map(([a, b]) => {
    const [p, q] = [place(picked[a]), place(picked[b])];
    return a === b ? p : `${p}–${p.split(":")[0] === q.split(":")[0] ? q.split(":")[1] : q}`;
  }).join(", ");
  const note = el("p", "range-note", `${shown} · `);
  const whole = el("a", null, "whole chapter");
  whole.href = readerHref(TEXT, BOOK);
  note.append(whole);
  document.getElementById("credits").before(note);
  return data;
}

function renderVerse(data, has) {
  const frag = document.createDocumentFragment();
  for (const v of data.verses) {
    const li = document.createElement("li");
    li.className = "verse" + (v.line <= 0 ? " title" : "") + (v.start == null ? " untimed" : "");
    const ref = v.ref ?? String(v.line);
    const num = document.createElement("span");
    // Every verse of the Bible is numbered; lines of poetry, every fifth.
    const shown = data.cite === "verse" || (Number(ref) % 5 === 0 && Number(ref) > 0);
    num.className = "num" + (shown ? "" : " hidden");
    num.textContent = v.line > 0 ? v.label ?? ref : "";
    const text = document.createElement("span");
    text.className = "text";
    if (v.line > 0 && has.has("words")) {
      // One span per whitespace chunk; chunk indices match word_links/paraphrase_links.word_index.
      v.text.split(" ").forEach((chunk, i) => {
        if (i) text.append(" ");
        const w = document.createElement("span");
        w.className = "w";
        w.textContent = chunk;
        w.dataset.line = v.line;
        w.dataset.index = i;
        if (v.div) w.dataset.div = v.div;
        if (v.glosses) w.dataset.gloss = v.glosses[i] ?? "—";
        if (v.groups?.[i] != null) w.dataset.group = v.groups[i];
        text.append(w);
      });
    } else {
      text.textContent = v.text;
    }
    if (v.start != null) {
      const icon = document.createElement("span");
      icon.className = "seek-icon";
      icon.innerHTML = SEEK_ICON;
      li.append(icon);
    }
    li.append(num, text);
    if (v.line > 0) li.dataset.ref = ref;
    if (v.div) li.dataset.div = v.div;  // a selection's verse from another chapter
    setNoteKey(li, v.line, v.line, v.line <= 0 ? "" : data.books.length > 1 ? `${v.div ?? BOOK}.${ref}` : ref);
    if (v.scholia) li.append(scholiaButton(li, `line ${ref}`, v.scholia));
    if (v.paraphrase) {
      const para = document.createElement("span");
      para.className = "para";
      para.lang = "grc";
      para.textContent = v.paraphrase;
      li.append(para);
    }
    if (v.modern) {
      const mt = document.createElement("span");
      mt.className = "mt";
      mt.lang = "el";
      mt.textContent = v.modern;
      li.append(mt);
    }
    if (v.english) {
      const en = document.createElement("span");
      en.className = "en";
      en.lang = "en";
      en.textContent = v.english;
      li.append(en);
    }
    const entry = { ...v, el: li };
    entryOf.set(li, entry);
    if (v.line > 0) refs.push({ ref, el: li, entry: v.start != null ? entry : null });
    if (v.start != null) {
      li.addEventListener("click", () => seekTo(verses.indexOf(entry)));
      verses.push(entry);
    }
    frag.append(li);
  }
  list.append(frag);
  verses.sort((a, b) => a.start - b.start);

  // Each English passage goes after the last verse it translates.
  const verseEl = new Map(data.verses.map((v, i) => [v.line, list.children[i]]));
  for (const p of data.translation ?? []) {
    const after = verseEl.get(p.to) ?? verseEl.get(Math.max(...[...verseEl.keys()].filter((l) => l <= p.to)));
    if (!after) continue;
    const li = document.createElement("li");
    li.className = "trans";
    li.lang = "en";
    const lines = document.createElement("span");
    lines.className = "lines";
    lines.textContent = p.from === p.to ? `${p.from}` : `${p.from}–${p.to}`;
    li.append(lines, p.text);
    li.addEventListener("click", () => seekTo(verses.findIndex((v) => v.line === p.from)));
    after.after(li);
    passages.push({ ...p, el: li });
  }
}

// Prose: a paragraph per speech (or the editor's paragraph), speaker first. Where a
// section begins, its number stands in the margin if the paragraph opens with it, and
// in the text otherwise.
function renderProse(data, has) {
  const frag = document.createDocumentFragment();
  let li = null, text = null, num = null, lastRef = null, notes = 0, refsIn = [];
  const close = () => {  // the paragraph's notes key: its first to last segment
    if (!li) return;
    const label = refsIn.length > 1 ? `${refsIn[0]}–${refsIn.at(-1)}` : refsIn[0] ?? lastRef ?? "";
    setNoteKey(li, Number(li.dataset.from), Number(li.dataset.to), label);
    if (notes) li.append(scholiaButton(li, label, notes));
  };
  for (const v of data.verses) {
    const opens = v.para || !text;
    if (opens) {
      close();
      li = el("li", "verse prose-para untimed" + (v.line <= 0 ? " title" : ""));  // (lines 0, -1…: a heading read first)
      li.dataset.from = v.line;
      num = el("span", "num");
      text = el("span", "text");
      li.append(num, text);
      frag.append(li);
      notes = 0;
      refsIn = lastRef && !(v.ref && v.ref !== lastRef) ? [lastRef] : [];
    }
    li.dataset.to = v.line;
    notes += v.scholia ?? 0;
    if (v.ref && v.ref !== lastRef) refsIn.push(v.ref);
    if (v.speaker) text.append(el("span", "speaker", v.speaker), " ");
    const seg = el("span", "seg");
    let sectionRef = null;
    if (v.ref && v.ref !== lastRef) {
      if (opens) num.textContent = v.ref;
      else seg.append(el("span", "ref-mark", v.ref), " ");
      sectionRef = { ref: v.ref, el: opens ? text.parentNode : seg, entry: null };
      refs.push(sectionRef);
      lastRef = v.ref;
    }
    // Parsed words: a span per whitespace chunk, numbered through the segment (as the server's word_index).
    let wordIndex = 0;
    const fill = (node, str) => {
      if (!(v.line > 0 && has.has("words"))) return node.append(str);
      str.split(/\s+/).filter(Boolean).forEach((chunk, i) => {
        if (i) node.append(" ");
        const w = el("span", "w", chunk);
        w.dataset.line = v.line;
        w.dataset.index = wordIndex++;
        node.append(w);
      });
    };
    if (v.parts) {
      // With a recording: each sentence is timed, and is what lights up, follows and seeks.
      v.parts.forEach((part, k) => {
        if (k) seg.append(" ");
        const sent = el("span", "sent");
        fill(sent, part.text);
        const entry = { line: v.line, ref: lastRef, start: part.start, speechEnd: part.speechEnd, end: part.end,
                        cut: part.cut, el: sent, row: li };
        entryOf.set(sent, entry);
        sent.addEventListener("click", (e) => {
          if (e.target.closest(".w")) return;
          seekTo(verses.indexOf(entry));
        });
        verses.push(entry);
        if (sectionRef && !sectionRef.entry) sectionRef.entry = entry;
        seg.append(sent);
      });
    } else {
      fill(seg, v.text);
    }
    text.append(seg, " ");
  }
  close();
  list.append(frag);
  verses.sort((a, b) => a.start - b.start);
}

// Each row knows which segments its notes are on (for the scholia panel and popup).
// A timed entry's row: the verse itself, or for prose the paragraph its sentence is in.
const rowOf = (entry) => entry.row ?? entry.el;

function setNoteKey(li, from, to, label) {
  Object.assign(li.dataset, { from, to, label });
}
function noteKey(li) {
  return li && { from: Number(li.dataset.from), to: Number(li.dataset.to), label: li.dataset.label, li };
}
function scholiaButton(li, what, count) {
  const b = el("button", "sch-btn");
  b.innerHTML = SCHOLIA_ICON;
  b.title = `Notes on ${what} (${count})`;
  b.setAttribute("aria-label", b.title);
  b.addEventListener("click", (e) => { e.stopPropagation(); openScholiaPopup(noteKey(li)); });
  // Start fetching as the finger or button goes down: often there by the click.
  b.addEventListener("pointerdown", () => prefetchScholia(li));
  li.dataset.scholia = count;
  return b;
}

// Index of the verse whose span [start, end) contains t (binary search). A position a
// hair before a verse's start counts as that verse: browsers snap a seek to the nearest
// audio frame, which can land just short of the time asked for.
const SEEK_SLACK = 0.05;  // seconds
function verseAt(t) {
  let lo = 0, hi = verses.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (verses[mid].start <= t + SEEK_SLACK) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
}

// Focused mode shows verses[focusIdx] with its neighbours (the first verse before playback).
// Stepping one verse animates the change: lines that stay visible glide (and grow or
// shrink) from their old place to their new one, the incoming line fades in from the
// direction of travel and the outgoing one fades out the other way.
const FOCUS_MS = 450;
const FOCUS_EASE = "cubic-bezier(0.2, 0.7, 0.2, 1)";
let focusIdx = -1;

function setFocus(i) {
  if (i === focusIdx) return;
  const dir = i - focusIdx;
  const animate = document.body.classList.contains("focused") && focusIdx >= 0 && Math.abs(dir) === 1
    && typeof Element.prototype.animate === "function"
    && !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const before = new Map();  // el -> {rect, font, opacity} for the lines visible before the change
  if (animate) {
    for (const k of [focusIdx - 1, focusIdx, focusIdx + 1]) {
      const el = verses[k]?.el;
      if (el) {
        const cs = getComputedStyle(el);
        before.set(el, { rect: el.getBoundingClientRect(), font: parseFloat(cs.fontSize), opacity: cs.opacity });
      }
    }
  }

  for (const k of [focusIdx - 1, focusIdx, focusIdx + 1]) verses[k]?.el.classList.remove("f-prev", "f-cur", "f-next");
  focusIdx = i;
  verses[i - 1]?.el.classList.add("f-prev");
  verses[i]?.el.classList.add("f-cur");
  verses[i + 1]?.el.classList.add("f-next");
  if (!animate) return;

  const now = [i - 1, i, i + 1].map((k) => verses[k]?.el).filter(Boolean);
  for (const el of now) {
    const b = before.get(el);
    if (b) {  // stays visible: invert the move and size change, then play it forward
      const r = el.getBoundingClientRect();
      const s = b.font / parseFloat(getComputedStyle(el).fontSize);
      el.animate([
        { transformOrigin: "top left", transform: `translate(${b.rect.left - r.left}px, ${b.rect.top - r.top}px) scale(${s})` },
        { transformOrigin: "top left", transform: "none" },
      ], { duration: FOCUS_MS, easing: FOCUS_EASE });
    } else {  // incoming
      el.animate([
        { opacity: 0, transform: `translateY(${dir * 28}px)` },
        { opacity: getComputedStyle(el).opacity, transform: "none" },
      ], { duration: FOCUS_MS, easing: FOCUS_EASE });
    }
  }
  for (const [el, b] of before) {
    if (now.includes(el)) continue;
    // Outgoing: it is hidden now, so animate a copy left where it was.
    const ghost = el.cloneNode(true);
    ghost.classList.remove("current", "pausing", "held");
    Object.assign(ghost.style, {
      position: "fixed", left: `${b.rect.left}px`, top: `${b.rect.top}px`, width: `${b.rect.width}px`,
      margin: "0", fontSize: `${b.font}px`, pointerEvents: "none", zIndex: "1",
    });
    ghost.setAttribute("aria-hidden", "true");
    document.body.append(ghost);
    ghost.animate([
      { opacity: b.opacity, transform: "none" },
      { opacity: 0, transform: `translateY(${-dir * 28}px)` },
    ], { duration: FOCUS_MS, easing: FOCUS_EASE }).onfinish = () => ghost.remove();
  }
}

// Bring a verse (or a section in prose) to the middle of the screen. Verses off screen
// count as a guessed height until drawn (content-visibility in style.css), so the rows
// passed on the way change size as they are drawn: a long jump goes at once rather than
// gliding, and either way the view settles on the verse once its neighbours are drawn.
let cancelSettle = null;  // the last centerRow's pending correction
function centerRow(el, smooth = false) {
  cancelSettle?.();
  cancelSettle = null;
  const far = Math.abs(el.getBoundingClientRect().top - innerHeight / 2) > innerHeight * 2;
  const glide = smooth && !far;
  el.scrollIntoView({ block: "center", behavior: glide ? "smooth" : "instant" });
  const asked = Date.now();
  const settle = () => requestAnimationFrame(() => requestAnimationFrame(() => {
    if (lastUserScroll > asked) return;  // the reader has scrolled since: leave the view to them
    const r = el.getBoundingClientRect();
    if (Math.abs(r.top + r.height / 2 - innerHeight / 2) > 40) el.scrollIntoView({ block: "center", behavior: "instant" });
  }));
  if (!glide) return settle();
  // After the glide; a glide that doesn't move (already there) never ends, so not for long.
  const done = () => { cancelSettle?.(); cancelSettle = null; settle(); };
  const timer = setTimeout(done, 1000);
  addEventListener("scrollend", done);
  cancelSettle = () => { clearTimeout(timer); removeEventListener("scrollend", done); };
}

function update() {
  const t = audio.currentTime;
  const i = verseAt(t);
  if (verses.length) {
    setFocus(Math.max(i, 0));
    showScholiaFor(noteKey(rowOf(verses[focusIdx])));
  }
  if (i !== current) {
    if (current >= 0) verses[current].el.classList.remove("current", "pausing");
    current = i;
    if (DATA) queueMarkButton();
    if (i >= 0) {
      const el = verses[i].el;
      el.classList.add("current");
      if (follow.checked && !focused.checked && Date.now() - lastUserScroll > 4000) {
        centerRow(el, true);
      }
    }
  }
  const line = i >= 0 ? verses[i].line : null;
  const p = passages.find((q) => line >= q.from && line <= q.to) ?? null;
  if (p !== activePassage) {
    activePassage?.el.classList.remove("active");
    activePassage = p;
    p?.el.classList.add("active");
  }
  if (i >= 0) holdIfHovered(verses[i], t);
  // A range plays to the end of its last verse and stops there.
  if (RANGE && !audio.paused && verses.length && t >= verses.at(-1).end) audio.pause();
  // A chapter whose recording goes on into the next: go on there, playing.
  if (!RANGE && DATA?.next && !audio.paused && verses.length && t >= verses.at(-1).end - 0.05) carryOn(t);
  skipSilence(i, t);
  document.body.classList.toggle("playing", isPlaying());
  showTimeline(audio.currentTime);
  placeCard();  // the selected word may have moved (follow scroll, focused-mode steps)
  if (i >= 0) {
    const v = verses[i];
    v.el.classList.toggle("pausing", t >= v.speechEnd);
    const r = v.ref ?? v.line;
    showPosition((v.line <= 0 ? "title" : DATA.books.length > 1 ? `${v.div ?? BOOK}.${r}` : r) + (heldBy === v ? " · held" : ""));
  } else {
    showPosition(verses.length ? "—" : "");  // no recording: the box is just for jumping
  }
}

function holdIfHovered(v, t) {
  if (released && released !== v) released = null;
  if (holdOnHover.checked && !audio.paused && v === hovered && v !== released && t >= v.speechEnd) {
    audio.pause();
    heldBy = v;
    v.el.classList.add("held");
  }
}

function clearHold() {
  heldBy?.el.classList.remove("held");
  heldBy = null;
}

// A hover-hold is not a pause: logically the recording is still playing and will go on
// when the pointer moves off. Pressing pause during a hold makes it a real pause instead.
function isPlaying() {
  return !audio.paused || heldBy !== null;
}

function togglePlay() {
  if (heldBy) { clearHold(); update(); }  // stay stopped; moving off the word won't resume
  else if (audio.paused && RANGE && verses.length && audio.currentTime >= verses.at(-1).end - 0.05) seekTo(0, { play: true });
  else if (audio.paused) audio.play();
  else audio.pause();
}

// The recording plays as it was read, its pauses as they are: each jump is a seek, which
// phones (iPhones above all) make heard, a moment of the old place, a stop, then the new.
// Only an exceptionally long silence (over LONG_GAP: a recording's lead-in, a long break)
// is cut short, to GAP_KEEP after the verse and LEAD_IN before the next. Speech outside the
// text (a verse marked cut: "part 1 of 3", a LibriVox notice, a repeated title) goes at
// once, if it's long enough to be one (CUT_MIN: a shorter stretch is likelier the reader's
// own wording of the verse than an announcement); after the last verse, playback stops
// there. Between the pieces of a selection (jump), it goes straight on to the next.
const LONG_GAP = 4;    // seconds: a silence longer than this is cut short…
const GAP_KEEP = 1.5;  // …to this after the verse…
const LEAD_IN = 0.6;   // …and this before the next
const CUT_MIN = 3.5;   // seconds: speech outside the text this long is an announcement
const CUT_KEEP = 0.1;  // seconds kept after a verse before an announcement…
const CUT_LEAD = 0.2;  // …and before the verse after it
function skipSilence(i, t) {
  if (audio.paused || !verses.length) return;
  const v = i >= 0 ? verses[i] : null, next = verses[i + 1];
  const quietSince = v ? v.speechEnd : 0;
  if (!next) {
    if (v?.cut && !DATA?.next && t > v.speechEnd + CUT_KEEP) audio.pause();
    return;
  }
  const gap = next.start - quietSince;
  const announced = v?.jump || (v?.cut && gap >= CUT_MIN);
  if (!announced && gap <= LONG_GAP) return;  // a pause as read
  const keep = announced ? CUT_KEEP : GAP_KEEP, lead = announced ? CUT_LEAD : LEAD_IN;
  if (t > quietSince + keep && next.start - t > lead + 0.05) audio.currentTime = next.start - lead;
}

// Jumping keeps the play state by default: a paused reader stays paused on the new verse.
// (Evaluated before clearHold, so a hover-hold counts as playing.)
function seekTo(i, { play = isPlaying() } = {}) {
  if (i < 0 || i >= verses.length) return;
  clearHold();
  released = null;  // replaying a verse while hovering it should hold at its end again
  audio.currentTime = verses[i].start;
  lastUserScroll = 0;  // a deliberate jump: let the view follow again
  update();
  if (play && audio.paused) audio.play();
}

// Position box: shows the current line; type a line ("40" or "1.40") and Enter to jump.
function showPosition(text) {
  if (document.activeElement !== position) position.value = text;
}

// A line ("40", or "1.40" in book 1), or for prose a section ("172a"; "172" is its first).
function findLine(typed) {
  const m = typed.match(/^(?:(\d+)\.)?(\d+)$/);
  if (!m || (m[1] && Number(m[1]) !== BOOK)) return null;
  const line = Number(m[2]);
  const lines = refs.filter((r) => Number(r.ref) > 0);
  const last = lines.at(-1);
  // Past the end of the book: its last line.
  return lines.find((r) => Number(r.ref) === line) ?? (last && line > Number(last.ref) ? last : null);
}
function findSection(typed) {
  const m = typed.toLowerCase().replace(/[\s.§]+/g, "").match(/^(\d+)([a-e]?)$/);
  if (!m) return null;
  return refs.find((r) => r.ref === m[1] + (m[2] || "a")) ?? refs.find((r) => r.ref.startsWith(m[1])) ?? null;
}

function jumpToTyped() {
  const typed = position.value.trim();
  const target = DATA?.cite === "section" ? findSection(typed) : findLine(typed);
  if (!target) {
    position.classList.add("invalid");
    position.select();
    return;
  }
  position.blur();
  if (target.entry) seekTo(verses.indexOf(target.entry), { play: false });
  centerRow(target.el, true);
  target.el.classList.remove("located");
  void target.el.offsetWidth;  // restart the highlight if it is still running
  target.el.classList.add("located");
  setTimeout(() => target.el.classList.remove("located"), 1800);
}

position.addEventListener("focus", () => { position.select(); });
position.addEventListener("input", () => position.classList.remove("invalid"));
position.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); jumpToTyped(); }
  else if (e.key === "Escape") { e.preventDefault(); position.blur(); }
});
position.addEventListener("blur", () => { position.classList.remove("invalid"); update(); });

// Smooth highlighting while playing; timeupdate alone fires only ~4 times a second.
function tick() {
  update();
  if (!audio.paused) requestAnimationFrame(tick);
}
audio.addEventListener("play", () => {
  if (heldBy) { released = heldBy; clearHold(); }  // resumed by hand while still hovering
  update();
  requestAnimationFrame(tick);
});
audio.addEventListener("seeked", update);
audio.addEventListener("timeupdate", update);
audio.addEventListener("pause", update);

// Paraphrase toggle, remembered per browser (storage may be unavailable).
function applyParaphrase() {
  document.body.classList.toggle("hide-paraphrase", !showParaphrase.checked);
  gloss.hidden = true;
  try { localStorage.setItem("showParaphrase", showParaphrase.checked ? "1" : "0"); } catch {}
  if (current >= 0 && follow.checked) centerRow(verses[current].el);
}
try { if (localStorage.getItem("showParaphrase") === "0") showParaphrase.checked = false; } catch {}

function applyTranslation() {
  document.body.classList.toggle("show-translation", showTranslation.checked);
  try { localStorage.setItem("showTranslation", showTranslation.checked ? "1" : "0"); } catch {}
  if (current >= 0 && follow.checked) centerRow(verses[current].el);
}
try { showTranslation.checked = localStorage.getItem("showTranslation") === "1"; } catch {}
showTranslation.addEventListener("change", applyTranslation);
applyTranslation();

function applyModern() {
  document.body.classList.toggle("show-modern", showModern.checked);
  try { localStorage.setItem("showModern", showModern.checked ? "1" : "0"); } catch {}
  if (current >= 0 && follow.checked) centerRow(verses[current].el);
}
try { showModern.checked = localStorage.getItem("showModern") === "1"; } catch {}
showModern.addEventListener("change", applyModern);
applyModern();
showParaphrase.addEventListener("change", applyParaphrase);
applyParaphrase();

// Scholia panel: the ancient notes on the current verse, grouped by manuscript. Sources
// the reader has folded stay folded (remembered per browser).
const showScholia = document.getElementById("show-scholia");
const scholiaPanel = document.getElementById("scholia");
const scholiaBody = document.getElementById("scholia-body");
const scholiaLine = document.getElementById("scholia-line");
const scholiaCache = new Map();  // "from-to" -> response promise
const scholiaLoaded = new Map(); // "from-to" -> response, once it has arrived
let scholiaShown = null;         // "from-to" of the line(s) the panel shows
let scholiaFolded = new Set();
try { scholiaFolded = new Set(JSON.parse(localStorage.getItem("scholiaFolded")) ?? []); } catch {}

function fetchScholia(key) {
  const id = `${key.from}-${key.to}`;
  if (!scholiaCache.has(id)) {
    const url = TEXT === "iliad" ? `/api/scholia/${BOOK}/${key.from}`
      : `/api/notes/${encodeURIComponent(TEXT)}/${BOOK}/${id}`;
    scholiaCache.set(id, fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => { scholiaLoaded.set(id, data); return data; })
      .catch((err) => { scholiaCache.delete(id); throw err; }));
  }
  return scholiaCache.get(id);
}

function renderScholia(data) {
  scholiaBody.replaceChildren();
  scholiaBody.scrollTop = 0;
  if (!data.sources.length) scholiaBody.append(el("p", "note", TEXT === "iliad" ? "No scholia on this line." : "No notes here."));
  for (const src of data.sources) {
    const box = el("details", "sch-src");
    box.open = !scholiaFolded.has(src.id);
    box.dataset.src = src.id;
    box.lang = src.lang;
    const sum = el("summary");
    sum.append(el("span", "siglum", src.siglum), src.name, el("span", "count", src.notes.length));
    box.append(sum);
    for (const n of src.notes) {
      const p = el("p", "sch");
      if (n.from != null) p.append(el("span", "range", `${n.from}–${n.to}`));
      if (n.parts) {  // a commentary note: styled runs (l = lemma, i = italic, t = text)
        for (const [kind, text] of n.parts) {
          p.append(kind === "l" ? el("span", "lemma", text) : kind === "i" ? el("i", null, text) : text);
        }
      } else {
        if (n.lemma) p.append(el("span", "lemma", `${n.lemma}]`), " ");
        p.append(n.text);
      }
      box.append(p);
    }
    box.addEventListener("toggle", () => {
      box.open ? scholiaFolded.delete(src.id) : scholiaFolded.add(src.id);
      try { localStorage.setItem("scholiaFolded", JSON.stringify([...scholiaFolded])); } catch {}
    });
    scholiaBody.append(box);
  }
}

// Screens too narrow for the scholia column use the per-verse buttons and a popup instead
// of the toggle (the breakpoint matches style.css).
const scholiaPopupMode = window.matchMedia?.("(max-width: 89.99rem)");
const popupMode = () => !!scholiaPopupMode?.matches;

let notedRow = null;  // without a recording: the row the notes are on, marked in the text
function loadScholia(key) {
  const id = `${key.from}-${key.to}`;
  scholiaShown = id;
  notedRow?.classList.remove("noted");
  notedRow = DATA && !DATA.audio ? key.li : null;
  notedRow?.classList.add("noted");
  scholiaLine.textContent = key.label;
  if (TEXT === "iliad" && !key.from) {  // the spoken title
    scholiaBody.replaceChildren(el("p", "note", "The scholia begin at line 1."));
    return;
  }
  // Already here: shown at once. Otherwise grey lines until it comes, never the notes of
  // the verse before.
  if (scholiaLoaded.has(id)) return renderScholia(scholiaLoaded.get(id));
  const skel = el("div", "sch-skel");
  for (let i = 0; i < 4; i++) skel.append(el("span"));
  scholiaBody.replaceChildren(skel);
  scholiaBody.scrollTop = 0;
  fetchScholia(key)
    .then((data) => { if (scholiaShown === id) renderScholia(data); })
    .catch((err) => {
      if (scholiaShown === id) scholiaBody.replaceChildren(el("p", "note", `Could not load: ${err.message}`));
    });
}

// The panel follows the current verse (toggle mode, wider screens); without a recording,
// the line or paragraph at the reading position, or the one last clicked.
function showScholiaFor(key) {
  if (!hasScholia || !showScholia.checked || popupMode() || !key || `${key.from}-${key.to}` === scholiaShown) return;
  loadScholia(key);
  // Fetch the next verses' notes too, so the panel keeps up with the recitation.
  if (key.li) {
    let next = key.li;
    for (let n = 0; n < 2 && (next = next.nextElementSibling); ) {
      if (!next.classList.contains("verse")) continue;
      n++;
      if (next.dataset.scholia) prefetchScholia(next);
    }
  }
}
function prefetchScholia(li) {
  const key = noteKey(li);
  if (key && key.from) fetchScholia(key).catch(() => {});
}

function readingRow() {
  const r = list.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, innerHeight * 0.4)?.closest?.("li.verse");
  return hit && list.contains(hit) ? hit : null;
}
let clickedAt = 0;
window.addEventListener("scroll", () => {
  if (DATA && !DATA.audio && Date.now() - clickedAt > 1500) showScholiaFor(noteKey(readingRow()));
}, { passive: true });
list.addEventListener("click", (e) => {
  if (!DATA || DATA.audio || e.target.closest(".sch-btn")) return;
  const li = e.target.closest("li.verse");
  if (!li) return;
  clickedAt = Date.now();
  showScholiaFor(noteKey(li));
});

// Popup (narrower screens): pauses the recording, as reading them takes a while.
function openScholiaPopup(key) {
  if (heldBy) { clearHold(); update(); }
  else if (!audio.paused) audio.pause();
  closeCard();
  document.body.classList.add("scholia-popup");
  scholiaPanel.hidden = false;
  loadScholia(key);
}

function closeScholia() {
  notedRow?.classList.remove("noted");
  notedRow = null;
  if (document.body.classList.contains("scholia-popup")) {
    document.body.classList.remove("scholia-popup");
    applyScholia();
  } else {
    showScholia.checked = false;
    applyScholia();
  }
}

function applyScholia() {
  const panel = hasScholia && showScholia.checked && !popupMode();
  if (!popupMode()) document.body.classList.remove("scholia-popup");
  document.body.classList.toggle("show-scholia", panel);
  scholiaPanel.hidden = !panel && !document.body.classList.contains("scholia-popup");
  try { localStorage.setItem("showScholia", showScholia.checked ? "1" : "0"); } catch {}
  if (panel) {
    scholiaShown = null;
    if (focusIdx >= 0) showScholiaFor(noteKey(rowOf(verses[focusIdx])));
    else if (DATA && !DATA.audio) showScholiaFor(noteKey(readingRow() ?? list.querySelector("li.verse")));
  }
}
try { showScholia.checked = localStorage.getItem("showScholia") === "1"; } catch {}
showScholia.addEventListener("change", applyScholia);
scholiaPopupMode?.addEventListener?.("change", applyScholia);
document.getElementById("scholia-close").addEventListener("click", closeScholia);
// A tap outside the popup closes it (and does nothing else, e.g. doesn't seek).
document.addEventListener("click", (e) => {
  if (!document.body.classList.contains("scholia-popup") || scholiaPanel.contains(e.target)
      || e.target.closest?.(".sch-btn")) return;
  closeScholia();
  e.stopPropagation();
  e.preventDefault();
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.body.classList.contains("scholia-popup")) closeScholia();
});
applyScholia();
// A folded source opens with a click; its summary shouldn't keep focus (Space is play/pause).
scholiaBody.addEventListener("mousedown", (e) => { if (e.target.closest("summary")) e.preventDefault(); });

// Word glosses: hovering a Homeric word shows Gaza's equivalent. Not on touch: a tap opens
// the card, which already has it.
// Words sharing a paraphrase word (e.g. κατὰ … ἔκηα → κατέκαυσα) light up together.
function linkGroup(w, on) {
  if (w.dataset.group == null) return;
  // In focused mode only the current line lights up, not a linked word in a neighbour.
  const scope = on && focused.checked ? list.querySelector(".f-cur") ?? list : list;
  for (const el of scope.querySelectorAll(`.w[data-group="${w.dataset.group}"]`)) {
    el.classList.toggle("linked", on);
  }
}

list.addEventListener("pointerover", (e) => {
  const w = e.target.closest(".w");
  if (!w || w.dataset.gloss == null || e.pointerType === "touch") return;
  linkGroup(w, true);
  gloss.textContent = w.dataset.gloss;
  gloss.classList.toggle("none", w.dataset.gloss === "—");
  gloss.hidden = false;
  const r = w.getBoundingClientRect(), g = gloss.getBoundingClientRect();
  const left = Math.min(Math.max(8, r.left + r.width / 2 - g.width / 2), innerWidth - g.width - 8);
  const above = r.top - g.height - 8;
  gloss.style.left = `${left + scrollX}px`;
  gloss.style.top = `${(above > 8 ? above : r.bottom + 8) + scrollY}px`;
});
list.addEventListener("pointerout", (e) => {
  const w = e.target.closest(".w");
  if (!w) return;
  linkGroup(w, false);
  if (!e.relatedTarget?.closest?.(".w")) gloss.hidden = true;
});
window.addEventListener("scroll", () => { gloss.hidden = true; placeCard(); }, { passive: true });

// Parsing card: clicking a word shows its treebank analysis (instead of seeking).
let selected = null;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const MATCH_NOTES = {
  orthographic: "Perseus spells it slightly differently",
  movable_nu: "Perseus prints it without movable ν",
  joined: "Perseus treats this and its neighbour as one word",
  variant: "Perseus has a different reading here",
};

function renderCard(data) {
  cardBody.replaceChildren();
  cardBody.append(el("h2", null, data.word.replace(/^[^\p{L}]+|[^\p{L}’'᾽]+$/gu, "")));
  if (data.gloss) {
    const g = el("p", "gloss-line");
    g.append(el("span", "label", "Gaza"), data.gloss);
    cardBody.append(g);
  }
  if (!data.tokens.length) {
    cardBody.append(el("p", "note", data.note ?? "Not in the Perseus treebank (its text leaves this line out)."));
  }
  for (const t of data.tokens) {
    const box = el("div", "token");
    if (data.tokens.length > 1 || t.match === "variant") box.append(el("div", "form", TEXT === "iliad" ? `Perseus: ${t.form}` : t.form));
    const lemma = el("div", "lemma", t.lemma ?? "(no lemma)");
    if (t.lemma) {
      const a = el("a", null, "Logeion ↗");
      a.href = `https://logeion.uchicago.edu/${encodeURIComponent(t.lemma)}`;
      a.target = "_blank";
      a.rel = "noopener";
      lemma.append(a);
    }
    // The New Testament's words: Strong's number, and Robinson's code under the parsing.
    if (t.strongs) lemma.append(el("code", "strongs", `G${t.strongs}`));
    box.append(lemma);
    if (t.definition) box.append(el("div", "definition", t.definition));
    if (t.morph.length) {
      const m = el("div", "morph");
      for (const f of t.morph) m.append(el("span", null, f));
      if (t.code) m.append(el("code", null, t.code));
      box.append(m);
    }
    if (t.role) {
      const r = el("div", "role");
      r.append(el("span", "label", "role"),
               t.root ? `${t.role} (main clause)` : t.head ? `${t.role} → ${t.head}` : t.role,
               el("code", null, t.relation));
      box.append(r);
    }
    if (MATCH_NOTES[t.match]) box.append(el("div", "note", MATCH_NOTES[t.match]));
    cardBody.append(box);
  }
}

// Words looked up: shown at once when tapped again. Fetching starts as the finger goes
// down, so on a slow connection it is often there by the tap.
const wordCache = new Map();   // "book/line/index" -> response promise
const wordLoaded = new Map();  // "book/line/index" -> response, once it has arrived
// (the book: a selection's verse from another chapter carries its own)
const wordId = (w) => `${w.dataset.div ?? BOOK}/${w.dataset.line}/${w.dataset.index}`;
function fetchWord(w) {
  const id = wordId(w);
  if (!wordCache.has(id)) {
    // The Iliad's words come from its treebank; a library text's from its own parsing.
    const url = TEXT === "iliad" ? `/api/word/${id}` : `/api/lexword/${encodeURIComponent(TEXT)}/${id}`;
    wordCache.set(id, fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => { wordLoaded.set(id, data); return data; })
      .catch((err) => { wordCache.delete(id); throw err; }));
  }
  return wordCache.get(id);
}
list.addEventListener("pointerdown", (e) => {
  const w = e.target.closest(".w");
  if (w) fetchWord(w).catch(() => {});
});

async function openCard(w) {
  selected?.classList.remove("selected");
  selected = w;
  w.classList.add("selected");
  card.hidden = false;
  const id = wordId(w);
  if (wordLoaded.has(id)) {
    renderCard(wordLoaded.get(id));
  } else {
    // Until it comes: the word itself and grey lines, never the last word's card.
    const skel = el("div", "card-skel");
    for (let i = 0; i < 5; i++) skel.append(el("span"));
    cardBody.replaceChildren(el("h2", null, w.textContent.replace(/^[^\p{L}]+|[^\p{L}’'᾽]+$/gu, "")), skel);
  }
  cardBody.scrollTop = 0;
  placeCard();
  if (wordLoaded.has(id)) return;
  try {
    const data = await fetchWord(w);
    if (selected === w) { renderCard(data); placeCard(); }
  } catch (err) {
    if (selected === w) {
      cardBody.replaceChildren(el("p", "note", navigator.onLine === false
        ? "This word isn't available offline yet." : `Could not load: ${err.message}`));
    }
  }
}

// Small and medium screens: the card floats just above the selected word's line (below
// it when there is more room there), centred on the word and kept between the top bar
// and the player. Wide screens keep it in the right-hand margin (CSS).
const cardFloats = window.matchMedia?.("(max-width: 75.99rem)");
const CARD_GAP = 8;    // px between the card and the bars and edges
const CARD_SPACE = 20; // px between the card and the word's line
function placeCard() {
  if (card.hidden || !selected) return;
  Object.assign(card.style, { left: "", top: "", maxHeight: "" });
  if (!cardFloats?.matches) return;
  const r = selected.getBoundingClientRect();
  if (!r.width && !r.height) return closeCard();  // its verse is hidden (focused mode)
  const minTop = document.getElementById("topbar").getBoundingClientRect().bottom + CARD_GAP;
  const maxBottom = document.getElementById("player").getBoundingClientRect().top - CARD_GAP;
  // Above the whole verse, so a wrapped verse isn't half covered; failing that below it;
  // failing both (a verse at the top of the page, a long prose paragraph) beside the word's line.
  const v = selected.closest(".text")?.getBoundingClientRect() ?? r;
  const need = Math.min(card.offsetHeight, 160);
  const room = (top, bottom) => ({ above: top - CARD_SPACE - minTop, below: maxBottom - bottom - CARD_SPACE });
  let { above, below } = room(v.top, v.bottom), from = v;
  if (above < need && below < need) {
    ({ above, below } = room(r.top, r.bottom));
    from = r;
  }
  const fitsAbove = above >= need || above >= below;
  card.style.maxHeight = `${Math.max(fitsAbove ? above : below, 120)}px`;
  card.style.top = `${fitsAbove ? from.top - CARD_SPACE - card.offsetHeight : from.bottom + CARD_SPACE}px`;
  const w = card.offsetWidth;
  card.style.left = `${Math.min(Math.max(CARD_GAP, r.left + r.width / 2 - w / 2), innerWidth - w - CARD_GAP)}px`;
}
window.addEventListener("resize", placeCard);
cardFloats?.addEventListener?.("change", placeCard);

function closeCard() {
  selected?.classList.remove("selected");
  selected = null;
  card.hidden = true;
}

// Capture phase, so a word click doesn't also reach the verse's seek handler.
list.addEventListener("click", (e) => {
  const w = e.target.closest(".w");
  if (!w) return;
  e.stopPropagation();
  w === selected ? closeCard() : openCard(w);
}, true);
document.getElementById("card-close").addEventListener("click", closeCard);

// Clicking off the card closes it (a click on another word opens that word's card instead;
// clicks in the player bar leave it open). On the text, that click only closes the card:
// it doesn't also jump the audio.
document.addEventListener("click", (e) => {
  if (!selected || card.contains(e.target) || e.target.closest?.(".w, #player, #focus-controls, #scholia, .sch-btn")) return;
  closeCard();
  if (list.contains(e.target)) {
    e.stopPropagation();
    e.preventDefault();
  }
}, true);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && selected) closeCard(); });

// Track which verse's text the pointer is on; leaving a held verse resumes playback.
// On a touch screen a tap counts: the tapped verse holds until a tap elsewhere.
list.addEventListener("mouseover", (e) => {
  const text = e.target.closest(".text");
  if (text) hovered = entryOf.get(e.target.closest(".sent")) ?? entryOf.get(text.closest(".verse")) ?? null;
});
list.addEventListener("mouseout", (e) => {
  const text = e.target.closest(".text");
  if (!text || text.contains(e.relatedTarget)) return;
  hovered = null;
  if (heldBy) {
    clearHold();
    audio.play();
  }
});

try { holdOnHover.checked = localStorage.getItem("holdOnHover") !== "0"; } catch {}  // on by default
holdOnHover.addEventListener("change", () => {
  try { localStorage.setItem("holdOnHover", holdOnHover.checked ? "1" : "0"); } catch {}
  if (!holdOnHover.checked) { clearHold(); update(); }
});

// Playback speed, remembered per browser (the pitch kept: the browser's default). The
// default rate too, so a recording loaded later (the next book) plays at it.
const speed = document.getElementById("speed");
function setSpeed(v) {
  audio.defaultPlaybackRate = audio.playbackRate = v;
  speed.value = String(v);
  document.getElementById("speed-label").textContent = `${v}×`;
}
try { const v = parseFloat(localStorage.getItem("speed")); if (v > 0) setSpeed(v); } catch {}
speed.addEventListener("change", () => {
  setSpeed(parseFloat(speed.value));
  try { localStorage.setItem("speed", speed.value); } catch {}
});
audio.addEventListener("ratechange", () => showPositionState());

// The bars' heights, for focused mode to fit the verses between them (style.css). Their
// size on screen, which includes the Interface size (CSS zoom).
function measureBars() {
  for (const [id, name] of [["topbar", "--topbar-h"], ["player", "--player-h"]]) {
    const h = document.getElementById(id).getBoundingClientRect().height;
    document.documentElement.style.setProperty(name, `${h}px`);
  }
}
if (typeof ResizeObserver === "function") {
  const bars = new ResizeObserver(measureBars);
  for (const id of ["topbar", "player"]) bars.observe(document.getElementById(id));
}
measureBars();

// After a size change in the Aa menu (theme.js): refit focused mode, the card and the view.
document.addEventListener("sizechange", () => {
  measureBars();
  placeCard();
  if (!focused.checked && current >= 0 && follow.checked) centerRow(verses[current].el);
});

// Focused mode toggle (F), remembered per browser.
function applyFocused() {
  document.body.classList.toggle("focused", focused.checked && DATA?.form !== "prose");
  try { localStorage.setItem("focused", focused.checked ? "1" : "0"); } catch {}
  if (focused.checked) window.scrollTo(0, 0);
  else if (current >= 0) centerRow(verses[current].el);
}
try { focused.checked = localStorage.getItem("focused") === "1"; } catch {}
focused.addEventListener("change", applyFocused);
applyFocused();

// Don't fight the reader: pause auto-follow for a few seconds after a manual scroll.
for (const ev of ["wheel", "touchmove"]) {
  window.addEventListener(ev, () => { lastUserScroll = Date.now(); }, { passive: true });
}

// Focused mode: scrolling (wheel or swipe) steps through the verses (the page itself
// doesn't scroll there).
const WHEEL_STEP = 60;      // px of wheel travel per verse
const WHEEL_LOCK_MS = 450;  // after a step, one gesture (or trackpad momentum) won't step again...
const WHEEL_QUIET_MS = 150; // ...until the wheel has been still this long
let wheelSum = 0, wheelStepAt = 0, wheelLastAt = 0;

function stepsVerses(e) {
  if (!focused.checked || !verses.length || document.body.classList.contains("no-audio")) return false;
  return !e.target.closest?.("#scholia, #card, #player, #topbar");
}
function stepBy(dir) { seekTo(Math.min(Math.max(focusIdx + dir, 0), verses.length - 1)); }

window.addEventListener("wheel", (e) => {
  if (!e.deltaY || Math.abs(e.deltaX) > Math.abs(e.deltaY) || !stepsVerses(e)) return;
  e.preventDefault();
  const now = Date.now();
  const quiet = now - wheelLastAt > WHEEL_QUIET_MS;
  wheelLastAt = now;
  if (quiet) wheelSum = 0;
  else if (now - wheelStepAt < WHEEL_LOCK_MS) return;
  else if (Math.abs(e.deltaY) < 4) return;  // the dying tail of trackpad momentum
  wheelSum += e.deltaY * (e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? innerHeight : 1);
  if (Math.abs(wheelSum) >= WHEEL_STEP) {
    stepBy(Math.sign(wheelSum));
    wheelSum = 0;
    wheelStepAt = now;
  }
}, { passive: false });

let touchFrom = null;  // {x, y} where a one-finger swipe began
window.addEventListener("touchstart", (e) => {
  touchFrom = e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
}, { passive: true });
window.addEventListener("touchmove", (e) => {
  if (!touchFrom || e.touches.length !== 1) return;
  const dy = touchFrom.y - e.touches[0].clientY;
  if (dy && stepsVerses(e)) e.preventDefault();  // no native scroll or bounce
}, { passive: false });
window.addEventListener("touchend", (e) => {
  if (!touchFrom) return;
  const t = e.changedTouches[0];
  const dx = touchFrom.x - t.clientX, dy = touchFrom.y - t.clientY;
  touchFrom = null;
  if (Math.abs(dy) > 50 && Math.abs(dy) > Math.abs(dx) && stepsVerses(e)) stepBy(Math.sign(dy));
});

// Stepping keeps the play state: a paused reader stays paused on the new verse.
function stepBack() {
  // Within the first 1.5 s of a verse, go to the previous one; otherwise restart this one.
  const i = verseAt(audio.currentTime);
  seekTo(i > 0 && audio.currentTime - verses[i].start < 1.5 ? i - 1 : Math.max(i, 0));
}
function stepForward() {
  seekTo(Math.min(verseAt(audio.currentTime) + 1, verses.length - 1));
}

document.getElementById("prev").addEventListener("click", stepBack);
document.getElementById("next").addEventListener("click", stepForward);

// Focused-mode controls: previous, play/pause, next.
for (const [id, action] of [["fc-prev", stepBack], ["fc-play", togglePlay], ["fc-next", stepForward]]) {
  document.getElementById(id).addEventListener("click", (e) => { e.stopPropagation(); action(); });
}

// Media bar: play/pause (hover-hold aware), timeline and volume.
const seek = document.getElementById("seek");
const timeNow = document.getElementById("time-now");
const timeTotal = document.getElementById("time-total");
const volume = document.getElementById("volume");
let seeking = false;  // the timeline thumb is being dragged

function fmtTime(s) {
  if (!Number.isFinite(s)) return "0:00";
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
function fillRange(input) {
  const max = Number(input.max) || 1;
  input.style.setProperty("--pct", `${(Number(input.value) / max) * 100}%`);
}
// The timeline spans the whole file, or, where the recording reads more than is shown (a
// chapter of a book of the Bible read through, a range of verses), only the verses shown:
// a stretch of the file per run of consecutive verses, the stretches laid end to end.
let spans = null;  // [[from, to], ...] in the file's seconds
function setSpans() {
  spans = null;
  if (!(DATA?.clip || RANGE) || !verses.length) return;
  spans = [];
  for (const v of verses) {  // (in the order heard)
    const run = spans.at(-1);
    if (run && v.start - run[1] < 1) run[1] = Math.max(run[1], v.end);
    else spans.push([v.start, v.end]);
  }
}
const timelineLength = () => (spans ? spans.reduce((n, [a, b]) => n + b - a, 0) : audio.duration || 0);
function toTimeline(t) {  // a time in the file -> a place on the timeline
  if (!spans) return t;
  let at = 0;
  for (const [a, b] of spans) {
    if (t < a) return at;
    if (t <= b) return at + t - a;
    at += b - a;
  }
  return at;
}
function fromTimeline(x) {  // and back
  if (!spans) return x;
  for (const [a, b] of spans) {
    if (x <= b - a) return a + x;
    x -= b - a;
  }
  return spans.at(-1)[1];
}
function setTimelineRange() {
  setSpans();
  seek.max = timelineLength();
  timeTotal.textContent = fmtTime(timelineLength());
  showTimeline(audio.currentTime);
  showPositionState();
}

function showTimeline(t) {
  if (!seeking) seek.value = toTimeline(t);
  timeNow.textContent = fmtTime(seeking ? Number(seek.value) : toTimeline(t));
  fillRange(seek);
}

// The lock screen's position bar, likewise.
function showPositionState() {
  if (!spans || !("mediaSession" in navigator) || !navigator.mediaSession.setPositionState) return;
  try {
    navigator.mediaSession.setPositionState({ duration: timelineLength(), playbackRate: audio.playbackRate,
                                              position: Math.min(toTimeline(audio.currentTime), timelineLength()) });
  } catch {}
}
for (const ev of ["play", "pause", "seeked", "ratechange"]) audio.addEventListener(ev, showPositionState);

document.getElementById("play").addEventListener("click", togglePlay);
audio.addEventListener("durationchange", setTimelineRange);
seek.addEventListener("input", () => { seeking = true; showTimeline(audio.currentTime); });
seek.addEventListener("change", () => {
  seeking = false;
  clearHold();
  released = null;
  audio.currentTime = fromTimeline(Number(seek.value));
  lastUserScroll = 0;
  update();
});

function showVolume() {
  volume.value = audio.muted ? 0 : audio.volume;
  fillRange(volume);
  document.body.classList.toggle("muted", audio.muted || audio.volume === 0);
}
volume.addEventListener("input", () => {
  audio.volume = Number(volume.value);
  audio.muted = audio.volume === 0;
});
document.getElementById("mute").addEventListener("click", () => {
  if (audio.volume === 0) audio.volume = 0.5;  // unmuting from zero: make it audible
  audio.muted = !audio.muted;
});
audio.addEventListener("volumechange", () => {
  showVolume();
  try { localStorage.setItem("volume", JSON.stringify({ v: audio.volume, m: audio.muted })); } catch {}
});
try {
  const saved = JSON.parse(localStorage.getItem("volume"));
  if (saved) { audio.volume = saved.v; audio.muted = saved.m; }
} catch {}
showVolume();

// Buttons don't keep keyboard focus after a click, so Space stays play/pause (rather
// than pressing the last-clicked button again on top of it).
for (const b of document.querySelectorAll(".tbtn, #focus-controls button, #to-start, #theme, #size-button, .size-step, .font-choice, .toggle, #scholia-close, .sch-btn")) {
  b.addEventListener("mousedown", (e) => e.preventDefault());
}

// Back to the beginning of the book (keeps the play state).
document.getElementById("to-start").addEventListener("click", () => {
  seekTo(0);
  window.scrollTo({ top: 0, behavior: "smooth" });
});

// The top bar's title and book: in full on wider screens ("Ὁμήρου Ἰλιάς", "Ῥαψῳδία Α (1)"),
// shortened on phones ("Ἰλιάς", "Α (1)"); style.css shows one or the other.
function setBoth(box, long, short) {
  box.querySelector(".long").textContent = long;
  box.querySelector(".short").textContent = short;
}
function showTitle(long, short) {
  setBoth(document.getElementById("eyebrow"), long, short);
}

// Book picker (hidden for a work in one piece): a menu of links to the other books, by
// their full names.
function fillBooks(books) {
  bookMenu.replaceChildren(...books.map((b) => {
    const q = new URLSearchParams({ text: TEXT, book: b.n });
    const li = document.createElement("li");
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", String(b.n === BOOK));
    const a = document.createElement("a");
    a.href = `?${q}`;
    a.textContent = longLabel(b.label, b.n);
    li.append(a);
    return li;
  }));
  const here = books.find((b) => b.n === BOOK);
  setBoth(document.getElementById("book-label"), here ? longLabel(here.label, here.n) : "",
          here ? shortLabel(here.label, here.n) : "");
  bookPicker.hidden = books.length < 2;
}
// A dropdown: the button opens its menu (kept on screen, at the current item); a tap
// elsewhere, Escape or tabbing away closes it; the arrow keys move through it.
function dropdown(picker, button, menu) {
  const open = () => {
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    menu.style.left = "";  // shift it left if it would run off the right edge
    const over = menu.getBoundingClientRect().right - (innerWidth - 16);
    if (over > 0) menu.style.left = `${-over}px`;
    const here = menu.querySelector('[aria-selected="true"]');
    if (here) menu.scrollTop = here.offsetTop - (menu.clientHeight - here.offsetHeight) / 2;
    (here ?? menu.firstElementChild)?.querySelector("a").focus({ preventScroll: true });
  };
  const close = () => {
    if (menu.hidden) return;
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
  };
  button.addEventListener("click", () => (menu.hidden ? open() : close()));
  menu.addEventListener("click", (e) => { if (e.target.closest("a")) savePlace(); });
  document.addEventListener("pointerdown", (e) => { if (!picker.contains(e.target)) close(); });
  picker.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { e.preventDefault(); close(); button.focus(); }
    else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !menu.hidden) {
      e.preventDefault();
      const links = [...menu.querySelectorAll("a")];
      const i = links.indexOf(document.activeElement) + (e.key === "ArrowDown" ? 1 : -1);
      links[Math.min(Math.max(i, 0), links.length - 1)]?.focus();
    }
  });
  // (A click elsewhere is left to pointerdown: Safari doesn't focus a clicked button, so a
  // click on the picker's own button would look like leaving.)
  picker.addEventListener("focusout", (e) => {
    if (e.relatedTarget && !picker.contains(e.relatedTarget)) close();
  });
}
dropdown(bookPicker, bookSelect, bookMenu);

// The title, in a book of a collection (a Testament): a menu of the collection's books,
// each opening at the chapter last read in it.
const titleSelect = document.getElementById("title-select");
const titleMenu = document.getElementById("title-menu");
function fillTitles(texts) {
  titleSelect.disabled = !texts?.length;
  titleMenu.replaceChildren(...(texts ?? []).map((t) => {
    const li = el("li");
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", String(t.slug === TEXT));
    const a = el("a", null, t.title);
    a.href = readerHref(t.slug, lastBook(t.slug) ?? 1);
    li.append(a);
    return li;
  }));
}
dropdown(document.getElementById("title-picker"), titleSelect, titleMenu);

// Space is play/pause, whatever has the focus: taken before anything else sees it, on its
// way down and up (a focused button or checkbox would act on it as it comes up, a menu as it
// goes down), and once per press (held, it doesn't repeat).
window.addEventListener("keydown", (e) => {
  if (e.key !== " " || e.isComposing) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  if (!e.repeat) togglePlay();
}, true);
window.addEventListener("keyup", (e) => {
  if (e.key !== " ") return;
  e.preventDefault();
  e.stopImmediatePropagation();
}, true);

document.addEventListener("keydown", (e) => {
  // Typing in the line box is left alone; on a slider the arrow keys move the slider.
  if (e.target.closest?.('input[type="text"], textarea, select, #book-picker, #size-picker')) return;
  if (e.target.type === "range" && e.key.startsWith("Arrow")) return;
  if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); stepBack(); }
  else if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); stepForward(); }
  else if (e.ctrlKey || e.metaKey || e.altKey) return;
  else if ((e.key === "f" || e.key === "F") && !document.body.classList.contains("no-audio") && DATA?.form !== "prose") {
    focused.checked = !focused.checked;
    applyFocused();
  } else if ((e.key === "s" || e.key === "S") && hasScholia) {
    if (document.body.classList.contains("scholia-popup")) closeScholia();
    else if (popupMode()) { if (focusIdx >= 0 && verses[focusIdx].scholia) openScholiaPopup(noteKey(rowOf(verses[focusIdx]))); }
    else { showScholia.checked = !showScholia.checked; applyScholia(); }
  }
});

// Remember where you were: the audio position per book, saved while listening and on
// leaving, restored (paused) on the next visit; without a recording, the scroll position.
// Storage may be unavailable.
const PLACE_KEY = TEXT === "iliad" ? `place:book${BOOK}` : `place:${TEXT}:${BOOK}`;
let restored = false;  // don't overwrite the saved place before it has been restored
let lastSaved = 0;

function savePlace() {
  if (!restored) return;
  try {
    if (!DATA?.audio) localStorage.setItem(PLACE_KEY, `scroll:${Math.round(scrollY)}`);
    else if (Number.isFinite(audio.currentTime)) localStorage.setItem(PLACE_KEY, audio.currentTime.toFixed(2));
  } catch {}
  lastSaved = Date.now();
}

// The next chapter, at the same moment of the recording (this chapter's place is set back
// to its start: it has been read through).
let carrying = false;
function carryOn(t) {
  if (carrying) return;
  carrying = true;
  try { localStorage.setItem(PLACE_KEY, verses[0].start.toFixed(2)); } catch {}
  location.href = `${readerHref(TEXT, DATA.next)}&at=${t.toFixed(2)}`;
}

function restorePlace() {
  if (V_FROM) return goToLinked();
  let saved = null;
  try { saved = localStorage.getItem(PLACE_KEY); } catch {}
  if (!DATA.audio) {
    const y = saved?.startsWith("scroll:") ? Number(saved.slice(7)) : 0;
    if (y > 0) window.scrollTo(0, y);
    restored = true;
    return;
  }
  let t = CARRY_AT ?? parseFloat(saved);
  // Not opened before: at the first verse (past any introduction, or the chapters before
  // it in a recording of the whole book).
  if (!(t > 0) && verses.length && verses[0].start > 1) t = Math.max(0, verses[0].start - LEAD_IN);
  // Show the place at once; the recording is moved there once it can be.
  const at = t > 0 ? verseAt(t) : -1;
  if (at >= 0 && !focused.checked) centerRow(verses[at].el);
  const apply = () => {
    if (t > 0 && (!audio.duration || t < audio.duration)) audio.currentTime = t;
    restored = true;
    update();
    if (CARRY_AT) audio.play().catch(() => {});  // the browser may want a tap first: then it waits, there
    if (current >= 0 && !focused.checked) centerRow(verses[current].el);
  };
  whenAudioReady(apply);
}

// Setting currentTime before the audio's metadata is known may be ignored.
function whenAudioReady(fn) {
  if (audio.readyState >= 1) fn();
  else audio.addEventListener("loadedmetadata", fn, { once: true });
}

// Opened at a verse (?v=40): go there, paused, and mark it. Then the address loses the
// verse, so a reload comes back to wherever reading got to. A range (?v=40-60) starts
// at its beginning and keeps its address; its place isn't saved over the book's.
function goToLinked() {
  const target = RANGE ? refs[0] : V_TO ? null : DATA.cite === "section" ? findSection(V_FROM) : findLine(V_FROM);
  if (!target) {
    if (!RANGE) history.replaceState(null, "", readerHref(TEXT, BOOK));
    restored = !RANGE;
    return;
  }
  if (!focused.checked || !target.entry) centerRow(target.el);
  noteTap(target.el.closest("li.verse") ?? target.el, target.el.getBoundingClientRect().top);
  if (!RANGE) {
    target.el.classList.add("located");
    setTimeout(() => target.el.classList.remove("located"), 1800);
    history.replaceState(null, "", readerHref(TEXT, BOOK));
  }
  if (target.entry) {
    whenAudioReady(() => {
      audio.currentTime = target.entry.start;
      restored = !RANGE;
      update();
    });
  } else {
    restored = !RANGE;
  }
}

audio.addEventListener("timeupdate", () => { if (Date.now() - lastSaved > 3000) savePlace(); });
for (const ev of ["pause", "seeked"]) audio.addEventListener(ev, savePlace);
window.addEventListener("pagehide", savePlace);
window.addEventListener("scroll", () => { if (!DATA?.audio && Date.now() - lastSaved > 1000) savePlace(); },
                        { passive: true });
document.addEventListener("visibilitychange", () => { if (document.hidden) savePlace(); });

// Lock screen, notification and headset controls: play/pause, and the track buttons step
// a verse.
function setupMediaSession() {
  if (!("mediaSession" in navigator) || !DATA.audio) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: DATA.books.find((b) => b.n === BOOK)?.label ?? DATA.title, artist: DATA.author ?? "",
    album: DATA.title,
    artwork: [{ src: "icons/icon-512.png", sizes: "512x512", type: "image/png" }],
  });
  const actions = {
    play: () => { if (!isPlaying()) togglePlay(); },
    pause: () => { if (isPlaying()) togglePlay(); },
    previoustrack: stepBack,
    nexttrack: stepForward,
    seekto: (d) => { clearHold(); audio.currentTime = fromTimeline(d.seekTime); update(); },
  };
  for (const [action, handler] of Object.entries(actions)) {
    try { navigator.mediaSession.setActionHandler(action, handler); } catch {}  // not all are supported
  }
}

// Bookmarks: the button in the top bar marks the verse being read, or unmarks it if it is
// marked already: the verse playing if it is on screen, else the one last tapped, else
// the one at the reading position (in prose, the section where it was tapped or where the
// reading position is). A verse opened by a link counts as tapped. Marked verses carry a
// ribbon; the library lists them.
const bookmarkButton = document.getElementById("bookmark");
const toastBox = document.getElementById("toast");
let marks = new Map();  // ref -> bookmark, in this book
let tappedRow = null, tappedAt = 0, tappedY = 0;  // tappedY: in the page (not the viewport)

function noteTap(li, y) {
  tappedRow = li;
  tappedAt = Date.now();
  tappedY = y + scrollY;
}
list.addEventListener("pointerdown", (e) => {
  const li = e.target.closest("li.verse");
  if (li) noteTap(li, e.clientY);
});

function onScreen(row) {
  const r = row.getBoundingClientRect();
  const top = document.getElementById("topbar").getBoundingClientRect().bottom;
  const bottom = document.getElementById("player").getBoundingClientRect().top;
  return r.height > 0 && r.bottom > top && r.top < bottom;
}

// The verse (row) to mark, and its reference: a line, or for prose the section at the
// reading position within the paragraph.
function markTarget() {
  const cur = current >= 0 ? verses[current] : null;
  const tapped = tappedRow && Date.now() - tappedAt < 60000 && onScreen(tappedRow);
  const playing = cur && cur.line > 0 && onScreen(cur.el);
  let li = playing ? rowOf(cur) : tapped ? tappedRow : readingRow();
  // Prose being recited: the section of the sentence playing.
  if (playing && cur.row && cur.ref) return { li, ref: cur.ref, seg: cur.el.closest(".seg") };
  const probe = li === tappedRow ? tappedY - scrollY : innerHeight * 0.4;
  if (li && !li.dataset.ref && !li.classList.contains("prose-para")) li = li.nextElementSibling?.closest("li.verse");
  if (!li) return null;
  if (!li.classList.contains("prose-para")) return li.dataset.ref ? { li, ref: li.dataset.ref } : null;
  const inside = refs.filter((r) => li.contains(r.el));
  let pick = inside[0];
  for (const r of inside) if (r.el.getBoundingClientRect().top <= probe + 1) pick = r;
  const ref = pick?.ref ?? li.dataset.label?.split("–")[0];
  return ref ? { li, ref, seg: pick && pick.el !== li.querySelector(".text") ? pick.el : null } : null;
}

// "Α 40" in a work in books lettered Α–Ω, "5.12" in other books, "17a" in a single work.
function placeLabel(ref) {
  const b = DATA.books.find((x) => x.n === BOOK);
  if (DATA.books.length < 2 || !b) return ref;
  const m = b.label?.match(/\s(\p{Script=Greek}{1,2})$/u);
  return m ? `${m[1]} ${ref}` : `${BOOK}.${ref}`;
}

function rowOfRef(ref) {
  return refs.find((r) => r.ref === ref)?.el.closest("li.verse") ?? null;
}

function showMarks() {
  for (const li of list.querySelectorAll("li.bookmarked")) li.classList.remove("bookmarked");
  for (const ref of marks.keys()) rowOfRef(ref)?.classList.add("bookmarked");
  showMarkButton();
}
function showMarkButton() {
  const t = DATA && markTarget();
  const on = !!(t && marks.has(t.ref));
  bookmarkButton.setAttribute("aria-pressed", String(on));
  bookmarkButton.title = on ? "Remove the bookmark on this verse" : "Bookmark this verse";
}

let toastTimer = 0;
function toast(text) {
  toastBox.textContent = text;
  toastBox.hidden = false;
  toastBox.classList.remove("out");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastBox.classList.add("out");
    toastTimer = setTimeout(() => { toastBox.hidden = true; }, 250);
  }, 1800);
}

async function toggleBookmark() {
  if (!DATA) return;
  const t = markTarget();
  if (!t) return toast("Scroll to a verse to bookmark it");
  if (t.li.dataset.div) return toast("Open its chapter to bookmark this verse");
  const label = `${DATA.title} ${placeLabel(t.ref)}`;
  const have = marks.get(t.ref);
  try {
    if (have) {
      await bookmarksApi.remove(have.id);
      marks.delete(t.ref);
      toast(`Bookmark removed · ${label}`);
    } else {
      const copy = (t.seg ?? t.li.querySelector(".text")).cloneNode(true);
      for (const m of copy.querySelectorAll(".ref-mark")) m.remove();  // the section numbers
      const words = copy.textContent.replace(/\s+/g, " ").trim();
      const mark = await bookmarksApi.add({
        text: TEXT, book: BOOK, ref: t.ref, title: DATA.title, label,
        snippet: words.length > 90 ? `${words.slice(0, 88).trimEnd()}…` : words,
      });
      marks.set(t.ref, mark);
      toast(`Bookmarked · ${label}`);
    }
  } catch {
    return toast(navigator.onLine === false ? "Bookmarks need the connection" : "Couldn't save the bookmark");
  }
  showMarks();
  t.li.classList.remove("located");
  void t.li.offsetWidth;
  t.li.classList.add("located");
  setTimeout(() => t.li.classList.remove("located"), 1800);
}

function loadMarks() {
  bookmarksApi.list()
    .then((all) => {
      marks = new Map(all.filter((b) => b.text === TEXT && b.book === BOOK).map((b) => [b.ref, b]));
      showMarks();
    })
    .catch(() => {});
}
bookmarkButton.addEventListener("click", toggleBookmark);
bookmarkButton.addEventListener("mousedown", (e) => e.preventDefault());
let markFrame = 0;
const queueMarkButton = () => {
  if (!markFrame) markFrame = requestAnimationFrame(() => { markFrame = 0; showMarkButton(); });
};
window.addEventListener("scroll", queueMarkButton, { passive: true });
list.addEventListener("click", queueMarkButton);
document.addEventListener("keydown", (e) => {
  if ((e.key === "b" || e.key === "B") && !e.ctrlKey && !e.metaKey && !e.altKey
      && !e.target.closest?.('input[type="text"], textarea')) toggleBookmark();
});

// Installable app: the service worker caches the reader for offline use (sw.js).
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

// While the book loads: its title and books from the last visit (so the top bar doesn't
// change when moving between books), and grey lines where the text will be (shown only
// if loading takes a moment).
try {
  const meta = JSON.parse(localStorage.getItem(`meta:${TEXT}`));
  if (meta?.title) {
    showTitle(meta.eyebrow ?? meta.title, meta.short ?? meta.title);
    fillBooks(meta.books ?? []);
  }
} catch {}
list.classList.add("loading");
for (let i = 0; i < 14; i++) {
  const li = el("li", "skel");
  li.append(el("span", "sk-num"), el("span", "sk-line"), el("span", "sk-line sk-sub"));
  li.style.setProperty("--w", `${62 + ((i * 37) % 30)}%`);
  list.append(li);
}

(window.bookRequest ?? fetch(API))
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
  .then((data) => (SELECTION ? select(data) : data))
  .then((data) => { render(data); setTimelineRange(); update(); restorePlace(); setupMediaSession(); loadMarks(); })
  .catch((err) => {
    list.classList.remove("loading");
    list.textContent = navigator.onLine === false || err instanceof TypeError
      ? "This isn't available offline yet: open it once while online, and it will be."
      : `Could not load this text: ${err.message}`;
  });
