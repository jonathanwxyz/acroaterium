// The reader's API made on the device, from the compact library (data/library_compact.sqlite,
// written by scripts/compact_library.py, whose docstring gives the format; sqlite.js reads
// the file): what scripts/serve.py gives for /api/library, /api/text, /api/lexword,
// /api/notes, /api/words, /api/offline and the Iliad's older /api/book, /api/word and
// /api/scholia, made the same way (its functions are ported here one for one, and a check
// compares the two). What needs the server's own data stays with the server: bookmarks,
// plans, the lectionary, the recordings. The server says which recordings it has, and their
// sizes (/api/compact/sizes); they're given here (sizes: {file: bytes}).
(function (root) {
  // Each chunk kind's columns as stored, and what a dict's value stands for (compact_read.py).
  const CHUNK_COLUMNS = {
    segments: ["seq", "ref", "content", "speaker", "para"],
    translations: ["seq", "lang", "content"],
    translation_passages: ["seq_from", "seq_to", "lang", "content"],
    notes: ["pos_from", "pos_to", "source", "seq", "body?", "parts"],
    words: ["seq", "word_index", "strongs", "words.morph_id", "words.lemma_id"],
    treebank: ["seq", "word_index", "part", "treebank.lemma_id", "treebank.postag_id", "treebank.relation_id",
               "treebank.head_id", "form", "match"],
    auto_words: ["seq", "word_index", "auto.lemma_id", "auto.morph_id", "confidence", "alternative"],
    paraphrase_links: ["seq", "word_index", "para_seq", "para_index", "para_word"],
    units: ["seq", "piece", "content?", "start", "speech_end", "end", "score", "cut"],
  };
  const DICT_COLUMNS = {
    "words.morph": ["morph"],
    "words.lemma": ["lemma", "gloss"],
    "treebank.lemma": ["lemma"],
    "treebank.postag": ["postag"],
    "treebank.relation": ["relation"],
    "treebank.head": ["head"],
    "auto.lemma": ["lemma", "headword", "definition"],
    "auto.morph": ["pos", "morph"],
  };
  const KEEP = 48;  // books' chunks kept decoded (a book is read a while, and its neighbours)

  async function inflate(bytes) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    return JSON.parse(await new Response(stream).text());
  }

  // The compact file: its small tables, a book's chunks, the dicts' values.
  class CompactLibrary {
    constructor(file) {
      this.file = file;
      this.small = new Map();  // table -> Promise of its rows
      this.chunks = new Map();  // "kind/text/div" -> Promise of rows (ids as stored)
      this.dicts = new Map();  // "kind/id" -> value
      this.present = new Map();  // "kind/text" -> whether the text has chunks of that kind
    }

    table(name) {
      if (!this.small.has(name)) this.small.set(name, this.file.all(name));
      return this.small.get(name);
    }

    byKey(table, key) { return this.file.byKey(table, key); }

    // A book's rows of a kind, as stored (null: none).
    raw(kind, text, div) {
      const key = `${kind}\u0000${text}\u0000${div}`;
      let p = this.chunks.get(key);
      if (p) {
        this.chunks.delete(key);
      } else {
        p = (async () => {
          const f = this.file;
          const e = await f.find(f.tables.sqlite_autoindex_chunks_1.root, [kind, text, div]);
          if (!e) return null;
          const row = await f.row(f.tables.chunks.root, e[e.length - 1]);
          return inflate(row[3]);
        })();
        p.catch(() => this.chunks.delete(key));
      }
      this.chunks.set(key, p);
      if (this.chunks.size > KEEP) this.chunks.delete(this.chunks.keys().next().value);
      return p;
    }

    async dict(kind, id) {
      const key = `${kind}\u0000${id}`;
      if (!this.dicts.has(key)) {
        const e = await this.file.find(this.file.tables.dicts.root, [kind, id]);
        this.dicts.set(key, e && e[2] != null ? JSON.parse(e[2]) : null);
      }
      return this.dicts.get(key);
    }

    // A stored row as the full table's (ids resolved, what was left out filled in).
    async full(kind, text, div, r) {
      const cols = CHUNK_COLUMNS[kind], d = {};
      for (let i = 0; i < cols.length; i++) {
        const name = cols[i], value = r[i];
        if (name.endsWith("_id")) {
          const dk = name.slice(0, -3), names = DICT_COLUMNS[dk], v = await this.dict(dk, value);
          if (names.length === 1) d[names[0]] = v;
          else names.forEach((n, k) => { d[n] = v[k]; });
        } else {
          d[name.endsWith("?") ? name.slice(0, -1) : name] = value;
        }
      }
      if (kind === "units" && d.content == null) d.content = (await this.segment(text, div, d.seq))?.content;
      if (kind === "notes" && d.body == null) d.body = JSON.parse(d.parts).map((p) => p[1]).join("");
      return d;
    }

    // A book's rows of a kind, as the full table's.
    async rows(kind, text, div) {
      const raw = await this.raw(kind, text, div);
      if (!raw) return [];
      if (!CHUNK_COLUMNS[kind].some((c) => c.endsWith("_id") || c.endsWith("?"))) {
        return raw.map((r) => Object.fromEntries(CHUNK_COLUMNS[kind].map((c, i) => [c, r[i]])));
      }
      const out = [];
      for (const r of raw) out.push(await this.full(kind, text, div, r));
      return out;
    }

    // One word's rows of a kind (by seq and word_index), resolved; a card looks up one word.
    async word(kind, text, div, seq, index) {
      const raw = await this.raw(kind, text, div);
      if (!raw) return [];
      const out = [];
      for (const r of raw) if (r[0] === seq && r[1] === index) out.push(await this.full(kind, text, div, r));
      return out;
    }

    async segment(text, div, seq) {
      const raw = await this.raw("segments", text, div);
      const r = raw?.find((x) => x[0] === seq);
      return r ? Object.fromEntries(CHUNK_COLUMNS.segments.map((c, i) => [c, r[i]])) : null;
    }

    // Whether a text has chunks of a kind (any book).
    async has(kind, text) {
      const key = `${kind}\u0000${text}`;
      if (!this.present.has(key)) {
        const f = this.file;
        const e = await f.seek(f.tables.sqlite_autoindex_chunks_1.root, [kind, text]);
        this.present.set(key, !!e && e[0] === kind && e[1] === text);
      }
      return this.present.get(key);
    }
  }

  // ---- Python's ways, as serve.py relies on them ----
  // str.split(): on runs of whitespace (str.isspace's), none at either end.
  const PY_SPACE = new RegExp(`[${[0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680,
    0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f,
    0x205f, 0x3000].map((c) => `\\u${c.toString(16).padStart(4, "0")}`).join("")}]+`);
  const pySplit = (s) => s.split(PY_SPACE).filter((w) => w !== "");
  // urllib.parse.quote: all but letters, digits, "_.-~" and "/" percent-encoded.
  function pyQuote(s) {
    let out = "";
    for (const b of new TextEncoder().encode(s)) {
      const c = String.fromCharCode(b);
      out += /[A-Za-z0-9_.\-~/]/.test(c) ? c : `%${b.toString(16).toUpperCase().padStart(2, "0")}`;
    }
    return out;
  }
  const round2 = (x) => Number(x.toFixed(2));
  const truthy = (v) => v != null && v !== "" && v !== 0 && v !== false;
  const rstripDigits = (s) => s.replace(/[0-9]+$/, "");
  const bySeq = (k = "seq") => (a, b) => a[k] - b[k];
  // Punctuation and editorial brackets around a paraphrase word, dropped in glosses.
  const edgePunct = (s) => s.replace(/^[^\p{L}\p{N}_’']+|[^\p{L}\p{N}_’']+$/gu, "");
  // "μῆνιν] παρὰ τὸ μένω …": the lemma (the words commented on) and the note.
  const LEMMA = /^([^\]]{1,90})\]\s*([\s\S]*)$/;
  // A word's letters, without accents or breathings, lowercase: Δαυίδ and Δαυιδ alike.
  const letters = (w) => w.toLowerCase().normalize("NFD").replace(/[^\p{L}]/gu, "");

  const ILIAD_FEATURES = ["audio", "paraphrase", "modern", "translation", "scholia", "words"];

  class LocalApi {
    constructor(lib, sizes) {
      this.db = lib;
      this.sizes = sizes ?? {};  // recordings the server has: {file under recordings/: bytes}
    }

    static handles(path) {
      return /^\/api\/(library|text\/[\w-]+\/\d+|notes\/[\w-]+\/\d+\/\d+-\d+|book\/\d+|lexword\/[\w-]+\/\d+\/\d+\/\d+|word\/\d+\/\d+\/\d+|scholia\/\d+\/\d+|words\/[\w-]+\/\d+|offline\/[\w-]+\/\d+)$/.test(path);
    }

    // A path's JSON (null: not found, as the server's 404).
    route(path) {
      let m;
      const n = (k) => Number(m[k]);
      if (path === "/api/library") return this.libraryJson();
      if ((m = /^\/api\/text\/([\w-]+)\/(\d+)$/.exec(path))) return this.textJson(m[1], n(2));
      if ((m = /^\/api\/notes\/([\w-]+)\/(\d+)\/(\d+)-(\d+)$/.exec(path))) return this.notesJson(m[1], n(2), n(3), n(4));
      if ((m = /^\/api\/book\/(\d+)$/.exec(path))) return this.bookJson(n(1));
      if ((m = /^\/api\/lexword\/([\w-]+)\/(\d+)\/(\d+)\/(\d+)$/.exec(path))) return this.lexwordJson(m[1], n(2), n(3), n(4));
      if ((m = /^\/api\/word\/(\d+)\/(\d+)\/(\d+)$/.exec(path))) return this.lexwordJson("iliad", n(1), n(2), n(3));
      if ((m = /^\/api\/scholia\/(\d+)\/(\d+)$/.exec(path))) return this.scholiaJson(n(1), n(2));
      if ((m = /^\/api\/words\/([\w-]+)\/(\d+)$/.exec(path))) return this.wordsJson(m[1], n(2));
      if ((m = /^\/api\/offline\/([\w-]+)\/(\d+)$/.exec(path))) return this.offlineJson(m[1], n(2));
      throw new Error(`not made here: ${path}`);
    }

    // ---- The Iliad's routes from before it joined the library ----
    async bookJson(book) {
      const d = await this.textJson("iliad", book);
      if (!d) return null;
      const credits = {};
      for (const r of await this.db.table("translation_credits")) if (r.text === "iliad") credits[r.lang] = r.credit;
      const verses = d.verses.map((v) => {
        const o = { line: v.line, text: v.text, paraphrase: v.paraphrase ?? null, english: v.english ?? null, modern: v.modern ?? null };
        for (const k of ["scholia", "start", "speechEnd", "end", "glosses", "groups"]) if (k in v) o[k] = v[k];
        return o;
      });
      return {
        book, audio: d.audio, verses, translation: d.translation, text: "iliad", title: d.title, author: d.author,
        eyebrow: d.eyebrow, form: d.form, cite: d.cite, features: ILIAD_FEATURES, books: d.books,
        credits: [{ id: "translation-credit", text: credits.en ?? null }, { id: "modern-credit", text: credits.el ?? null }],
        notesCredit: d.notesCredit, ...("download" in d ? { download: d.download } : {}),
      };
    }

    async scholiaJson(book, line) {
      const d = await this.notesJson("iliad", book, line, line);
      if (!d) return null;
      for (const src of d.sources) {
        for (const note of src.notes) {
          if ("from" in note) { note.from = parseInt(note.from, 10); note.to = parseInt(note.to, 10); }
        }
      }
      return { book, line, ...d };
    }

    // ---- The library ----
    async libraryJson() {
      const books = {};
      const divisions = [...await this.db.table("divisions")].sort((a, b) => (a.text < b.text ? -1 : a.text > b.text ? 1 : a.n - b.n));
      for (const r of divisions) (books[r.text] ??= []).push({ n: r.n, label: r.label });
      const texts = {}, collections = {};
      const downloads = await this.downloadsOf();
      const all = (await this.db.table("texts")).filter((r) => r.hidden !== 1);
      for (const r of [...all].sort(bySeq("sort"))) {
        const t = { slug: r.slug, title: r.title, author: r.author, reader: r.reader, books: books[r.slug] ?? [] };
        if (r.slug in downloads) t.downloads = downloads[r.slug].map((i) => ({ n: i.n, size: i.size }));
        if (r.collection != null && r.collection !== "") {
          const key = `${r.category}\u0000${r.collection}`;
          if (!collections[key]) {
            collections[key] = { collection: r.collection, title: r.collection, texts: [] };
            (texts[r.category] ??= []).push(collections[key]);
          }
          collections[key].texts.push(t);
        } else {
          (texts[r.category] ??= []).push(t);
        }
      }
      const cats = [...await this.db.table("categories")].sort(bySeq("sort")).map((r) => ({ name: r.name, texts: texts[r.name] ?? [] }));
      const lectionary = await this.setting("lectionary");
      return { categories: cats, ...(lectionary != null ? { lectionary: { category: lectionary.category, title: lectionary.title } } : {}) };
    }

    async setting(key) {
      const r = (await this.db.table("settings")).find((s) => s.key === key);
      return r ? JSON.parse(r.value) : null;
    }

    // What each text can be downloaded in, a recording at a time with the books it's for,
    // in book order; recordings the server has.
    async downloadsOf(slug = null) {
      const out = {};
      const recs = (await this.db.table("recordings")).filter((r) => slug == null || r.text === slug)
        .sort((a, b) => (a.text < b.text ? -1 : a.text > b.text ? 1 : a.div - b.div));
      for (const r of recs) {
        const size = this.sizes[r.file];
        if (size == null) continue;
        const items = (out[r.text] ??= []);
        const item = items.find((i) => i.file === r.file);
        if (item) item.divs.push(r.div);
        else items.push({ n: r.div, divs: [r.div], file: r.file, size });
      }
      return out;
    }

    async downloadOf(slug, n) {
      const item = ((await this.downloadsOf(slug))[slug] ?? []).find((i) => i.divs.includes(n));
      return item ? { download: { n: item.n, size: item.size, books: item.divs.length } } : {};
    }

    // Every word card of a book (or chapter), {"line/index": card}.
    async wordsJson(slug, n) {
      const out = {};
      for (const s of await this.db.rows("segments", slug, n)) {
        if (s.seq <= 0) continue;
        const count = pySplit(s.content).length;
        for (let i = 0; i < count; i++) {
          const card = await this.lexwordJson(slug, n, s.seq, i);
          if (card && (card.tokens.length || "gloss" in card)) out[`${s.seq}/${i}`] = card;
        }
      }
      return out;
    }

    async offlineJson(slug, n) {
      if (!(await this.db.table("texts")).some((t) => t.slug === slug)) return null;
      const item = ((await this.downloadsOf(slug))[slug] ?? []).find((i) => i.n === n);
      if (!item) return null;
      const q = pyQuote(slug);
      return {
        slug, n,
        pages: [...item.divs.map((d) => `/api/text/${q}/${d}`), ...item.divs.map((d) => `/api/words/${q}/${d}`)],
        audio: [{ url: `/audio/${pyQuote(item.file)}`, size: item.size }],
        size: item.size,
      };
    }

    // ---- Notes ----
    async notesJson(slug, n, a, b) {
      const sources = (await this.db.table("note_sources")).filter((s) => s.text === slug).sort(bySeq("sort"));
      const ref = new Map((await this.db.rows("segments", slug, n)).map((s) => [s.seq, s.ref]));
      const rows = (await this.db.rows("notes", slug, n)).filter((r) => r.pos_from <= b && r.pos_to >= a).sort(bySeq());
      const notes = {};
      for (const r of rows) {
        const fa = ref.has(r.pos_from) ? ref.get(r.pos_from) : String(r.pos_from);
        const fb = ref.has(r.pos_to) ? ref.get(r.pos_to) : String(r.pos_to);
        const span = fa !== fb ? { from: fa, to: fb } : {};
        const list = (notes[r.source] ??= []);
        if (r.parts) {
          list.push({ parts: JSON.parse(r.parts), ...span });
        } else {
          const m = LEMMA.exec(r.body);
          list.push({ lemma: m ? m[1].trim() : null, text: m ? m[2] : r.body, ...span });
        }
      }
      return {
        sources: sources.filter((s) => s.id in notes)
          .map((s) => ({ id: s.id, siglum: s.siglum, name: s.name, lang: s.lang, notes: notes[s.id] })),
      };
    }

    // ---- A book of a text ----
    async textJson(slug, n) {
      const t = (await this.db.table("texts")).find((r) => r.slug === slug && r.reader === "text");
      if (!t) return null;
      const rows = [...await this.db.rows("segments", slug, n)].sort(bySeq());
      if (!rows.length) return null;
      const books = (await this.db.table("divisions")).filter((r) => r.text === slug).sort((x, y) => x.n - y.n)
        .map((r) => ({ n: r.n, label: r.label }));
      // How many notes each segment has (a note covering several counts for each).
      const notes = new Map();
      for (const r of await this.db.rows("notes", slug, n)) {
        for (let seq = r.pos_from; seq <= r.pos_to; seq++) notes.set(seq, (notes.get(seq) ?? 0) + 1);
      }
      const hasNotes = (await this.db.table("note_sources")).some((s) => s.text === slug);
      const hasWords = (await this.db.has("words", slug)) || (await this.db.has("treebank", slug)) || (await this.db.has("auto_words", slug));
      const translated = { en: new Map(), el: new Map(), paraphrase: new Map() };
      for (const r of await this.db.rows("translations", slug, n)) (translated[r.lang] ??= new Map()).set(r.seq, r.content);
      const translationCredits = {};
      for (const r of await this.db.table("translation_credits")) if (r.text === slug) translationCredits[r.lang] = r.credit;
      const passages = (await this.db.rows("translation_passages", slug, n)).filter((r) => r.lang === "en")
        .sort(bySeq("seq_from")).map((r) => ({ from: r.seq_from, to: r.seq_to, text: r.content }));
      const { en: english, el: modern, paraphrase } = translated;
      const [glosses, groups] = await this.paraphraseGlosses(slug, n);
      const [recording, timed] = await this.libraryAudio(slug, n);
      const verses = rows.map((r) => {
        const seq = r.seq;
        const v = { line: seq, ref: r.ref, text: r.content };
        if (truthy(r.speaker)) v.speaker = r.speaker;
        if (truthy(r.para)) v.para = true;
        if (notes.has(seq)) v.scholia = notes.get(seq);
        if (english.has(seq)) v.english = english.get(seq);
        if (modern.has(seq)) v.modern = modern.get(seq);
        if (paraphrase.has(seq)) v.paraphrase = paraphrase.get(seq);
        if (glosses.has(seq)) v.glosses = glosses.get(seq);
        if (groups.has(seq)) v.groups = groups.get(seq);
        const pieces = timed.get(seq);
        if (pieces?.length && t.form === "verse") Object.assign(v, pieces[0][1]);
        else if (pieces?.length) v.parts = pieces.map(([text, times]) => ({ text, ...times }));
        return v;
      });
      const credits = truthy(t.credit) ? [{ text: t.credit }] : [];
      if (modern.size && truthy(translationCredits.el)) credits.push({ text: translationCredits.el, id: "modern-credit" });
      if ((english.size || passages.length) && truthy(translationCredits.en)) {
        credits.push({ text: translationCredits.en, id: "translation-credit" });
      }
      if (recording && truthy(recording.credit)) credits.push({ text: recording.credit });
      const hasCollection = truthy(t.collection);
      let members = [];
      if (hasCollection) {
        const last = {};
        for (const d of await this.db.table("divisions")) last[d.text] = Math.max(last[d.text] ?? -Infinity, d.n);
        members = (await this.db.table("texts")).filter((r) => r.collection === t.collection).sort(bySeq("sort"))
          .map((r) => ({ slug: r.slug, title: r.title, last: last[r.slug] ?? null }));
      }
      let goesOn = false;
      if (recording && timed.size) {
        const [following, after] = await this.libraryAudio(slug, n + 1);
        if (following && following.file === recording.file && after.size) {
          const ends = Math.max(...[...timed.values()].flat().map(([, x]) => x.end));
          const starts = Math.min(...[...after.values()].flat().map(([, x]) => x.start));
          goesOn = Math.abs(starts - ends) < 2;  // the recording reads it next
        }
      }
      return {
        book: n, text: slug, title: t.title, author: t.author, form: t.form, cite: t.cite,
        ...(hasCollection ? { collection: t.collection, collectionTexts: members } : {}),
        ...(goesOn ? { next: n + 1 } : {}),
        eyebrow: truthy(t.eyebrow) ? t.eyebrow : truthy(t.author) ? `${t.author} · ${t.title}` : t.title,
        ...(truthy(t.short) ? { short: t.short } : {}),
        features: [
          ...(hasNotes ? ["scholia"] : []), ...(recording ? ["audio"] : []), ...(paraphrase.size ? ["paraphrase"] : []),
          ...(hasWords ? ["words"] : []), ...(english.size || passages.length ? ["translation"] : []),
          ...(modern.size ? ["modern"] : []),
        ],
        notesCredit: t.notes_credit,
        books: books.length > 1 ? books : [],
        audio: recording ? `/audio/${pyQuote(recording.file)}` : null,
        ...(recording && recording.shared > 1 ? { clip: true } : {}),
        ...(await this.downloadOf(slug, n)),
        verses, translation: passages, credits,
      };
    }

    // A book's words as its paraphrase renders them ({seq: {word_index: "words"}}), and the
    // words that share a word of the paraphrase ({seq: {word_index: group id}}).
    async paraphraseGlosses(slug, n) {
      const rows = [...await this.db.rows("paraphrase_links", slug, n)].sort((a, b) =>
        a.seq - b.seq || a.word_index - b.word_index || a.para_seq - b.para_seq || a.para_index - b.para_index);
      const glosses = new Map(), sharers = new Map();
      for (const r of rows) {
        if (!glosses.has(r.seq)) glosses.set(r.seq, new Map());
        const g = glosses.get(r.seq);
        if (!g.has(r.word_index)) g.set(r.word_index, []);
        g.get(r.word_index).push(edgePunct(r.para_word));
        const key = `${r.para_seq}/${r.para_index}`;
        if (!sharers.has(key)) sharers.set(key, []);
        const ws = sharers.get(key);
        if (!ws.some(([s, w]) => s === r.seq && w === r.word_index)) ws.push([r.seq, r.word_index]);
      }
      const out = new Map();
      for (const [seq, g] of glosses) {
        const o = {};
        for (const [wi, words] of g) o[wi] = words.join(" ");
        out.set(seq, o);
      }
      return [out, wordGroups(sharers.values())];
    }

    // A book's aligned recording ({file, credit, shared: how many books share the file}) or
    // null, and its timings {seq: [[text, {start, speechEnd, end, cut?}]]} by sentence.
    async libraryAudio(slug, n) {
      const recs = await this.db.table("recordings");
      const rec = recs.find((r) => r.text === slug && r.div === n);
      if (!rec || !(rec.file in this.sizes)) return [null, new Map()];
      const timed = new Map();
      const units = [...await this.db.rows("units", slug, n)].sort((a, b) => a.seq - b.seq || a.piece - b.piece);
      for (const u of units) {
        if (!timed.has(u.seq)) timed.set(u.seq, []);
        timed.get(u.seq).push([u.content, {
          start: round2(u.start), speechEnd: round2(u.speech_end), end: round2(u.end), ...(truthy(u.cut) ? { cut: true } : {}),
        }]);
      }
      return [{ file: rec.file, credit: rec.credit, shared: recs.filter((r) => r.file === rec.file).length }, timed];
    }

    // ---- A word's card ----
    // Each token's lemma with its references in every text ({lemma, n}, as the server's
    // /api/references knows it), where the compact library has the lemma index's lemmas.
    async lexwordJson(slug, n, seq, index) {
      const card = await this.parsingJson(slug, n, seq, index);
      for (const token of card?.tokens ?? []) {
        const form = token._lemma;
        delete token._lemma;
        const ref = this.db.file.tables.lemma_forms && form != null ? await this.db.byKey("lemma_forms", [form]) : null;
        const lemma = ref && await this.db.byKey("lemmas", [ref.lemma]);
        if (lemma) token.references = { lemma: lemma.lemma, n: lemma.n };
      }
      return card;
    }

    // A word's card, but each token's lemma as its parsing table spells it (_lemma), not
    // its references.
    async parsingJson(slug, n, seq, index) {
      const seg = await this.db.segment(slug, n, seq);
      if (!seg) return null;
      const chunks = pySplit(seg.content);
      if (index < 0 || index >= chunks.length) return null;
      const word = chunks[index];
      const row = (await this.db.word("words", slug, n, seq, index))[0] ?? null;
      const parts = row ? [] : [...await this.db.word("treebank", slug, n, seq, index)].sort(bySeq("part"));
      const gloss = await this.paraphraseWord(slug, n, seq, index);
      // The word in the paraphrase (Gaza's), where the text has one.
      let extra = {};
      if (gloss != null) {
        const joined = gloss.map(edgePunct).join(" ");
        extra = { gloss: gloss.length && joined !== "" ? joined : null };
      }
      if (parts.length) {
        const tokens = [];
        for (const p of parts) {
          tokens.push({
            form: parts.length > 1 || truthy(p.match) ? p.form : word,
            lemma: p.lemma,
            definition: await this.lemmaDef(p.lemma),
            ...(truthy(p.match) ? { match: p.match } : {}),
            morph: postagMorph(p.postag),
            relation: p.relation,
            role: relationLabel(p.relation),
            head: truthy(p.head) ? p.head : null,
            root: p.head === "",
            _lemma: p.lemma,
          });
        }
        return { word, ...extra, tokens };
      }
      if (Object.keys(extra).length) return { word, ...extra, tokens: [] };
      if (!row) {
        const auto = await this.automaticParsing(slug, n, seq, index);
        if (auto) return { word, tokens: [auto] };
        return { word, tokens: [], note: "Not parsed (the parsing doesn't have this word here)." };
      }
      let strongs = row.strongs;
      const morph = row.morph;
      const lex = strongs == null ? null : await this.db.byKey("lexicon", [strongs]);
      let lemma = lex?.lemma ?? null, glossW = lex?.gloss ?? null, definition = lex?.definition ?? null;
      const ownLemma = row.lemma, ownGloss = row.gloss;
      const indexed = truthy(ownLemma) ? ownLemma : lemma;  // (as the lemma index has it)
      const short = await this.lemmaDef(ownLemma);
      if (truthy(ownLemma)) {
        // The Septuagint: its own lemma; Strong's (and Dodson's entry) only where it's the same word.
        if (!truthy(lemma) || letters(lemma) !== letters(ownLemma)) {
          strongs = null;
          definition = null;
        }
        lemma = ownLemma;
        glossW = ownGloss;
        definition = truthy(definition) ? definition : truthy(short) ? short : ownGloss;
      }
      const catss = ownLemma != null;
      return {
        word,
        tokens: [{
          form: word, lemma, strongs, gloss: glossW, definition: truthy(definition) ? definition : glossW,
          morph: truthy(morph) ? (catss ? catssMorph(morph) : robinsonMorph(morph)) : [], code: morph,
          _lemma: indexed,
        }],
      };
    }

    async lemmaDef(lemma) {
      if (lemma == null) return null;
      return (await this.db.byKey("lemma_defs", [lemma]))?.definition ?? null;
    }

    async paraphraseWord(slug, n, seq, index) {
      if (!(await this.db.has("paraphrase_links", slug))) return null;
      const rows = [...await this.db.word("paraphrase_links", slug, n, seq, index)]
        .sort((a, b) => a.para_seq - b.para_seq || a.para_index - b.para_index);
      return rows.map((r) => r.para_word);
    }

    async automaticParsing(slug, n, seq, index) {
      const r = (await this.db.word("auto_words", slug, n, seq, index))[0];
      if (!r) return null;
      let note = "Automatic parsing (may be wrong).";
      const other = rstripDigits(r.alternative ?? "");
      if (r.confidence != null && r.confidence < 0.6 && other && other !== rstripDigits(r.lemma)) {
        note = `Automatic parsing, uncertain: perhaps ${other}.`;
      }
      return {
        form: null,
        lemma: truthy(r.headword) ? r.headword : rstripDigits(r.lemma),
        definition: r.definition,
        morph: morpheusMorph(r.pos, JSON.parse(r.morph ?? "[]")),
        note,
        _lemma: r.lemma,
      };
    }
  }

  // Join words [seq, word_index] that share any paraphrase word into groups (union-find):
  // {seq: {word_index: group id}} for groups of two or more.
  function wordGroups(sharerSets) {
    const parent = new Map();
    const find = (k) => {
      if (!parent.has(k)) parent.set(k, k);
      while (parent.get(k) !== k) {
        parent.set(k, parent.get(parent.get(k)));
        k = parent.get(k);
      }
      return k;
    };
    for (const words of sharerSets) {
      const keys = words.map(([s, w]) => `${s}/${w}`);
      for (const k of keys.slice(1)) parent.set(find(k), find(keys[0]));
    }
    const members = new Map();
    for (const k of [...parent.keys()]) {
      const r = find(k);
      if (!members.has(r)) members.set(r, []);
      members.get(r).push(k);
    }
    const out = new Map();
    let gid = 0;
    for (const m of members.values()) {
      if (m.length < 2) continue;
      for (const k of m) {
        const [line, wi] = k.split("/").map(Number);
        if (!out.has(line)) out.set(line, {});
        out.get(line)[wi] = gid;
      }
      gid++;
    }
    return out;
  }

  // ---- Grammar, spelled out ----
  // AGDT dependency labels; suffixes _CO (coordinated) and _AP (in apposition).
  const RELATIONS = {
    PRED: "predicate", SBJ: "subject", OBJ: "object", ATR: "attribute",
    ADV: "adverbial", ATV: "complement", AtvV: "complement", PNOM: "predicate nominal",
    OCOMP: "object complement", COORD: "coordinator", APOS: "apposition",
    AuxP: "preposition", AuxC: "subordinating conjunction", AuxY: "sentence particle",
    AuxZ: "emphasizing particle", AuxV: "auxiliary verb", AuxX: "comma", AuxK: "end punctuation",
    AuxG: "bracket", ExD: "external (e.g. vocative, ellipsis)", XSEG: "word fragment",
  };
  function relationLabel(rel) {
    if (!rel) return null;
    const parts = rel.split("_");
    let label = RELATIONS[parts[0]] ?? parts[0];
    if (parts.slice(1).includes("AP")) label += ", in apposition";
    if (parts.slice(1).includes("CO")) label += ", coordinated";
    return label;
  }

  const POSTAG = [
    ["pos", { n: "noun", v: "verb", a: "adjective", d: "adverb", l: "article", g: "particle", c: "conjunction",
              r: "preposition", p: "pronoun", m: "numeral", i: "interjection", e: "exclamation", u: "punctuation",
              x: "irregular" }],
    ["person", { 1: "1st", 2: "2nd", 3: "3rd" }],
    ["number", { s: "singular", p: "plural", d: "dual" }],
    ["tense", { p: "present", i: "imperfect", r: "perfect", l: "pluperfect", t: "future perfect", f: "future", a: "aorist" }],
    ["mood", { i: "indicative", s: "subjunctive", o: "optative", n: "infinitive", m: "imperative", p: "participle" }],
    ["voice", { a: "active", p: "passive", m: "middle", e: "medio-passive" }],
    ["gender", { m: "masculine", f: "feminine", n: "neuter" }],
    ["gram_case", { n: "nominative", g: "genitive", d: "dative", a: "accusative", v: "vocative", l: "locative" }],
    ["degree", { c: "comparative", s: "superlative" }],
  ];
  function postagMorph(postag) {
    const s = (postag ?? "").padEnd(9, "-");
    const m = {};
    POSTAG.forEach(([name, table], i) => { m[name] = Object.hasOwn(table, s[i]) ? table[s[i]] : null; });
    const order = m.person
      ? ["pos", "person", "number", "tense", "mood", "voice", "gender", "gram_case", "degree"]
      : ["pos", "tense", "mood", "voice", "gender", "number", "gram_case", "degree"];
    return order.filter((k) => m[k]).map((k) => m[k]);
  }

  const get = (table, k) => (Object.hasOwn(table, k) ? table[k] : undefined);
  const ROBINSON_POS = {
    N: "noun", A: "adjective", T: "article", V: "verb", P: "personal pronoun",
    R: "relative pronoun", C: "reciprocal pronoun", D: "demonstrative pronoun",
    K: "correlative pronoun", I: "interrogative pronoun", X: "indefinite pronoun",
    Q: "correlative or interrogative pronoun", F: "reflexive pronoun", S: "possessive pronoun",
    ADV: "adverb", CONJ: "conjunction", COND: "conditional", PRT: "particle",
    PREP: "preposition", INJ: "interjection", ARAM: "Aramaic word", HEB: "Hebrew word",
  };
  const ROBINSON_CASE = { N: "nominative", G: "genitive", D: "dative", A: "accusative", V: "vocative" };
  const ROBINSON_NUMBER = { S: "singular", P: "plural" };
  const ROBINSON_GENDER = { M: "masculine", F: "feminine", N: "neuter" };
  const ROBINSON_PERSON = { 1: "1st person", 2: "2nd person", 3: "3rd person" };
  const ROBINSON_TENSE = { P: "present", I: "imperfect", F: "future", A: "aorist", R: "perfect", L: "pluperfect" };
  const ROBINSON_VOICE = {
    A: "active", M: "middle", P: "passive", E: "middle or passive",
    D: "middle deponent", O: "passive deponent", N: "middle or passive deponent",
    Q: "impersonal active", X: "no voice",
  };
  const ROBINSON_MOOD = {
    I: "indicative", S: "subjunctive", O: "optative", M: "imperative",
    N: "infinitive", P: "participle", R: "imperative participle",
  };
  const ROBINSON_SUFFIX = {
    C: "comparative", S: "superlative", N: "negative", I: "interrogative",
    K: "crasis", ATT: "Attic form", ABB: "abbreviated", PRI: "proper name, indeclinable",
    NUI: "numeral, indeclinable", LI: "letter, indeclinable", OI: "indeclinable",
  };

  function cng(part, maps) {
    const out = [];
    for (let i = 0; i < maps.length && i < part.length; i++) {
      const v = get(maps[i], part[i]);
      if (v !== undefined) out.push(v);
    }
    return out;
  }

  function robinsonMorph(code) {
    const pieces = code.split("-");
    const head = pieces[0], rest = pieces.slice(1);
    const out = [get(ROBINSON_POS, head) ?? head];
    const cngMaps = [ROBINSON_CASE, ROBINSON_NUMBER, ROBINSON_GENDER];
    if (head === "V" && rest.length) {
      let tvm = rest[0];
      const second = tvm.startsWith("2");
      if (second) tvm = tvm.slice(1);
      if (tvm.length >= 3) {
        out.push((second ? "second " : "") + (get(ROBINSON_TENSE, tvm[0]) ?? tvm[0]),
                 get(ROBINSON_VOICE, tvm[1]) ?? tvm[1], get(ROBINSON_MOOD, tvm[2]) ?? tvm[2]);
      }
      for (const part of rest.slice(1)) {
        if (get(ROBINSON_PERSON, part.slice(0, 1)) !== undefined) {
          out.push(ROBINSON_PERSON[part[0]]);
          for (const c of part.slice(1)) if (get(ROBINSON_NUMBER, c) !== undefined) out.push(ROBINSON_NUMBER[c]);
        } else if (get(ROBINSON_SUFFIX, part) !== undefined) {
          out.push(ROBINSON_SUFFIX[part]);
        } else {
          out.push(...cng(part, cngMaps));
        }
      }
      return out;
    }
    for (const part of rest) {
      if (get(ROBINSON_SUFFIX, part) !== undefined) {
        out.push(ROBINSON_SUFFIX[part]);
      } else if (head === "S" && get(ROBINSON_PERSON, part.slice(0, 1)) !== undefined) {
        const num = part.length > 1 ? get(ROBINSON_NUMBER, part[1]) ?? "" : "";
        out.push(ROBINSON_PERSON[part[0]], `${num} possessor`.trim(), ...cng(part.length > 2 ? part.slice(2) : "", cngMaps));
      } else if (get(ROBINSON_PERSON, part.slice(0, 1)) !== undefined) {
        out.push(ROBINSON_PERSON[part[0]], ...cng(part.slice(1), cngMaps));
      } else {
        out.push(...cng(part, cngMaps));
      }
    }
    return out;
  }

  const CATSS_TYPE = {
    N: "noun", A: "adjective", RA: "article", RD: "demonstrative pronoun",
    RI: "interrogative or indefinite pronoun", RP: "personal pronoun", RR: "relative pronoun",
    RX: "indefinite relative pronoun", C: "conjunction", X: "particle", I: "interjection", M: "numeral",
    P: "preposition", D: "adverb", V: "verb",
  };
  const CATSS_CASE = { N: "nominative", G: "genitive", D: "dative", A: "accusative", V: "vocative" };
  const CATSS_NUMBER = { S: "singular", D: "dual", P: "plural" };
  const CATSS_GENDER = { M: "masculine", F: "feminine", N: "neuter" };
  const CATSS_DEGREE = { C: "comparative", S: "superlative" };
  const CATSS_TENSE = { P: "present", I: "imperfect", F: "future", A: "aorist", X: "perfect", Y: "pluperfect" };
  const CATSS_VOICE = { A: "active", M: "middle", P: "passive" };
  const CATSS_MOOD = { I: "indicative", D: "imperative", S: "subjunctive", O: "optative", N: "infinitive", P: "participle" };
  const CATSS_PERSON = { 1: "1st person", 2: "2nd person", 3: "3rd person" };

  function catssMorph(code) {
    const dot = code.indexOf(".");
    const types = dot < 0 ? code : code.slice(0, dot), parse = dot < 0 ? "" : code.slice(dot + 1);
    const out = [types.split("+").map((t) => get(CATSS_TYPE, t) ?? t).join(" + ")];
    if (types === "N" && !parse) return [...out, "proper name, indeclinable"];
    const cngMaps = [CATSS_CASE, CATSS_NUMBER, CATSS_GENDER, CATSS_DEGREE];
    if (types.endsWith("V") && parse.length >= 3) {
      out.push(...cng(parse.slice(0, 3), [CATSS_TENSE, CATSS_VOICE, CATSS_MOOD]));
      const rest = parse.slice(3);
      if (parse[2] === "P") {
        out.push(...cng(rest, cngMaps));
      } else if (get(CATSS_PERSON, rest.slice(0, 1)) !== undefined) {
        out.push(CATSS_PERSON[rest[0]]);
        if (rest.length > 1 && get(CATSS_NUMBER, rest[1]) !== undefined) out.push(CATSS_NUMBER[rest[1]]);
      }
    } else {
      out.push(...cng(parse, cngMaps));
    }
    return out;
  }

  // Morpheus's abbreviations, spelled out as the treebanks' parsing is (null: left out).
  const MORPHEUS_WORDS = {
    pres: "present", imperf: "imperfect", fut: "future", aor: "aorist", perf: "perfect",
    plup: "pluperfect", futperf: "future perfect", ind: "indicative", subj: "subjunctive",
    opt: "optative", imperat: "imperative", inf: "infinitive", part: "participle",
    act: "active", mid: "middle", pass: "passive", mp: "middle/passive", sg: "singular",
    pl: "plural", dual: "dual", nom: "nominative", gen: "genitive", dat: "dative",
    acc: "accusative", voc: "vocative", masc: "masculine", fem: "feminine", neut: "neuter",
    comp: "comparative", superl: "superlative", indeclinable: null, adverbial: "adverbial",
  };
  const UPOS_WORDS = {
    NOUN: "noun", PROPN: "proper noun", VERB: "verb", AUX: "verb", ADJ: "adjective",
    ADV: "adverb", PRON: "pronoun", DET: "article", ADP: "preposition", CCONJ: "conjunction",
    SCONJ: "conjunction", PART: "particle", INTJ: "interjection", NUM: "numeral",
  };
  const morpheus = (w) => (Object.hasOwn(MORPHEUS_WORDS, w) ? MORPHEUS_WORDS[w] : w);
  function morpheusMorph(pos, readings) {
    const out = Object.hasOwn(UPOS_WORDS, pos ?? "") ? [UPOS_WORDS[pos]] : [];
    let k = 0;
    for (const rs of readings) {
      for (const reading of rs.split(" | ")) {
        if (k++ > 0) out.push("or");
        for (const w of pySplit(reading)) {
          if (morpheus(w) === null) continue;
          out.push(w.split("/").map((p) => morpheus(p) ?? "None").join(" or "));
        }
      }
    }
    return out;
  }

  root.CompactLibrary = CompactLibrary;
  root.LocalApi = LocalApi;
  if (typeof module !== "undefined") module.exports = { CompactLibrary, LocalApi };
})(typeof self !== "undefined" ? self : globalThis);
