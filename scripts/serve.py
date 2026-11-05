#!/usr/bin/env python3
"""Local server for the reader front end (web/).

    python3 scripts/serve.py [--port 8000]   then open http://localhost:8000/
    python3 scripts/serve.py --host 0.0.0.0  also reachable from other devices on the network
    python3 scripts/serve.py --show-hidden   the library lists the hidden texts too, greyed

Routes:
  /                    web/index.html, the library (and other files under web/; the
                       reader is web/read.html?text=<slug>&book=<n>)
  /api/library         JSON: the categories and their texts, from data/library.sqlite
                       (built by scripts/library.py from the local catalogue)
  /api/text/<slug>/<n> JSON: one book (division) of a library text: its segments, with
                       the shape of /api/book (verses), for the reader; where a recording
                       is aligned (data/library_audio.sqlite, scripts/align_library.py),
                       its URL, and timings per line (verse) or per sentence (prose:
                       a segment's "parts")
                       (the Iliad, text iliad, is one of them: with Gaza's paraphrase, its
                       verses' "paraphrase" and per-word equivalents, "glosses", Homeric
                       words sharing a paraphrase word, e.g. a verb in tmesis, sharing a
                       "groups" id)
  /api/notes/<slug>/<n>/<a>-<b>  JSON: a library text's notes on its segments a..b (seq),
                       shaped like /api/scholia
  /api/lexword/<slug>/<n>/<seq>/<i>  JSON: the parsing of word i (whitespace chunk) of a
                       segment, for the word card (the Iliad's treebank, with Gaza's word;
                       the New Testament: Robinson's parsing, Dodson's lexicon; ...)
  /api/lemmas?q=…&limit=  JSON: the library's search: the lemmas Morpheus finds what's typed
                       may be of, with its analyses, then those beginning so, accents and
                       capitals aside, or with words printed so, commonest first, each with
                       its count (and the word, where it's by one)
  /api/references/<lemma>?offset=&limit=&in=  JSON: the verses of every listed text with
                       a word of that lemma (as a word card's "references" gives it), by text;
                       in: part of the library only, named as a reading plan's target
  /api/book/<n>, /api/word/<b>/<l>/<i>, /api/scholia/<b>/<l>  the Iliad's, as they were
                       before it joined the library (/api/text/iliad, /api/lexword/iliad,
                       /api/notes/iliad), for clients that still ask for them
  /audio/<path>        a file under recordings/ (subfolders too), with HTTP Range support for seeking
  (/api/text and /api/book give the download each book is in: "download": {n, size, books})
  /api/words/<slug>/<n>  JSON: every word card of a book (or chapter), {"line/index": card},
                       for a download to keep (the cards then open offline)
  /api/offline/<slug>/<n>  JSON: what to save to read and hear part of a text offline,
                       a recording at a time (n: the first book it's for): those books' API
                       URLs ("pages") and the recording ("audio": [{url, size}]); /api/library
                       lists each text's ("downloads": [{n, size}])
  /api/lectionary/<YYYY-MM-DD>  JSON: that day's readings in the Orthodox lectionary
                       (the Ecumenical Patriarchate's, as the Greek Orthodox Archdiocese of
                       America's online chapel gives them), each a link to the passage in
                       the library; where the catalogue asks for it ("lectionary")
  /api/plans/preview?target=…&days=…  JSON: a reading plan's days: a text (or a Testament) in
                       so many days, a reading a day (target: a slug, "<slug>:<from book>-<to
                       book>", "collection:<name>" or "category:<name>"). The plans, and the
                       bookmarks, are kept by each device, in its browser (web/shared.js)
  /api/anki?target=…&v=…&pictures=1&sentences=1&start=  an Anki deck (.apkg, scripts/anki.py) of the
                       lemmas of part of the library (target: as a plan's; v: some of a text's
                       verses, "1-50, 60" or "3:5-4:12"), a note per lemma, with its picture
                       and the sentence it's first met in where asked; a lemma's note is the
                       same in every deck, so Anki adds only the lemmas it hasn't yet
                       (start: the cards the device made before, where its new ones begin)
  /api/anki/preview?…  JSON: what that deck would hold ({title, lemmas, words, pictures, sample}),
                       and of a single text, what its verses are and which there are ("parts")
  /api/compact         data/library_compact.sqlite (scripts/compact_library.py), the whole
                       library in one file, for an app to keep (with Range support; its
                       version, the start of its SHA-256, in X-Version and ETag; HEAD to ask
                       only that)
  /api/compact/sizes   JSON: {file: bytes} of the recordings there are, as the compact
                       library can't know them (the books' audio and downloads depend on them)
"""
import argparse
import datetime
import gzip
import hashlib
import html
import xml.etree.ElementTree as ElementTree
import json
import mimetypes
import re
import urllib.parse
import urllib.request
import sqlite3
import unicodedata
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from import_treebank import POSTAG
import anki
import headwords
import morpheus

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
RECORDINGS = ROOT / "recordings"
LIBRARY = ROOT / "data" / "library.sqlite"
LIBRARY_AUDIO = ROOT / "data" / "library_audio.sqlite"
LIBRARY_COMPACT = ROOT / "data" / "library_compact.sqlite"
LEMMA_IMAGES = ROOT / "data" / "lemma_images"
SHOW_HIDDEN = False  # --show-hidden: list the hidden texts too, marked (locally, never on the live site)

mimetypes.add_type("audio/ogg", ".opus")
mimetypes.add_type("audio/webm", ".webm")
mimetypes.add_type("application/manifest+json", ".webmanifest")

# Punctuation and editorial brackets around a paraphrase word, dropped in glosses.
EDGE_PUNCT = re.compile(r"^[^\w’']+|[^\w’']+$")


# The Iliad's routes from before it joined the library (/api/book, /api/word,
# /api/scholia): its JSON as those gave it, made from the library's.
ILIAD_FEATURES = ["audio", "paraphrase", "modern", "translation", "scholia", "words"]


def book_json(book):
    d = text_json("iliad", book)
    if not d:
        return None
    con = sqlite3.connect(LIBRARY)
    credits = dict(con.execute("SELECT lang, credit FROM translation_credits WHERE text = 'iliad'"))
    con.close()
    verses = [{"line": v["line"], "text": v["text"], "paraphrase": v.get("paraphrase"), "english": v.get("english"),
               "modern": v.get("modern"), **{k: v[k] for k in ("scholia", "start", "speechEnd", "end", "glosses", "groups")
                                             if k in v}}
              for v in d["verses"]]
    return {"book": book, "audio": d["audio"], "verses": verses, "translation": d["translation"], "text": "iliad",
            "title": d["title"], "author": d["author"], "eyebrow": d["eyebrow"], "form": d["form"], "cite": d["cite"],
            "features": ILIAD_FEATURES, "books": d["books"],
            "credits": [{"id": "translation-credit", "text": credits.get("en")},
                        {"id": "modern-credit", "text": credits.get("el")}],
            "notesCredit": d["notesCredit"], **({"download": d["download"]} if "download" in d else {})}


def word_json(book, line, index):
    return lexword_json("iliad", book, line, index)


def scholia_json(book, line):
    d = notes_json("iliad", book, line, line)
    if d is None:
        return None
    for src in d["sources"]:  # (lines as numbers)
        for n in src["notes"]:
            if "from" in n:
                n["from"], n["to"] = int(n["from"]), int(n["to"])
    return {"book": book, "line": line, **d}


def collection_of(con, column="collection"):
    """SQL for a column of texts (collection, short), or NULL in a library built before it existed."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(texts)")}
    return column if column in cols else "NULL"


def listed(con):
    """SQL for the texts the library lists: not the hidden ones, unless --show-hidden."""
    return "1" if SHOW_HIDDEN else f"{collection_of(con, 'hidden')} IS NOT 1"


def recording_sizes():
    """{file under recordings/: bytes} of the aligned recordings there are."""
    if not LIBRARY_AUDIO.exists():
        return {}
    con = sqlite3.connect(LIBRARY_AUDIO)
    files = [f for (f,) in con.execute("SELECT DISTINCT file FROM recordings")]
    con.close()
    return {f: (RECORDINGS / f).stat().st_size for f in files if (RECORDINGS / f).is_file()}


_compact_version = {}  # (size, mtime) -> the compact library's version


def compact_version():
    """The compact library's version, as compact_library.py wrote it in the file's header
    (the start of the SHA-256 of what it holds: the same library, built anywhere or bundled
    in the app, has the same version: mobile/android/app/build.gradle); in a file from
    before that, the start of the file's own SHA-256."""
    st = LIBRARY_COMPACT.stat()
    key = (st.st_size, st.st_mtime_ns)
    if key not in _compact_version:
        with LIBRARY_COMPACT.open("rb") as f:
            header = f.read(72)
        if len(header) == 72 and (header[68:72] != bytes(4) or header[60:64] != bytes(4)):
            _compact_version.clear()
            _compact_version[key] = (header[68:72] + header[60:64]).hex()
            return _compact_version[key]
        digest = hashlib.sha256()
        with LIBRARY_COMPACT.open("rb") as f:
            while chunk := f.read(1 << 20):
                digest.update(chunk)
        _compact_version.clear()
        _compact_version[key] = digest.hexdigest()[:16]
    return _compact_version[key]


def library_json():
    if not LIBRARY.exists():
        return {"categories": [], "missing": True}
    con = sqlite3.connect(LIBRARY)
    books = {}
    for text, n, label in con.execute("SELECT text, n, label FROM divisions ORDER BY text, n"):
        books.setdefault(text, []).append({"n": n, "label": label})
    texts, collections = {}, {}
    downloads = downloads_of()
    for slug, title, author, category, reader, collection, hidden in con.execute(
            f"SELECT slug, title, author, category, reader, {collection_of(con)}, {collection_of(con, 'hidden')}"
            f" FROM texts WHERE {listed(con)} ORDER BY sort"):
        t = {"slug": slug, "title": title, "author": author, "reader": reader, "books": books.get(slug, [])}
        if hidden:  # (listed only with --show-hidden)
            t["hidden"] = True
        if slug in downloads:
            t["downloads"] = [{"n": i["n"], "size": i["size"]} for i in downloads[slug]]
        if collection:  # the texts of a collection (a Testament) go together, where its first one is
            if (category, collection) not in collections:
                collections[category, collection] = {"collection": collection, "title": collection, "texts": []}
                texts.setdefault(category, []).append(collections[category, collection])
            collections[category, collection]["texts"].append(t)
        else:
            texts.setdefault(category, []).append(t)
    cats = [{"name": name, "texts": texts.get(name, [])}
            for (name,) in con.execute("SELECT name FROM categories ORDER BY sort")]
    lectionary = lectionary_settings(con)
    con.close()
    return {"categories": cats,
            **({"lectionary": {k: lectionary.get(k) for k in ("category", "title")}} if lectionary else {})}


def downloads_of(slug=None):
    """{slug: [{"n", "divs", "file", "size"}, ...]}: what each text can be downloaded in, a
    recording at a time with the books it's for (a book of the Iliad or Odyssey; a Bible
    book's chapters, which share one), in book order; recordings that are there."""
    rows = []  # (text, div, file)
    if LIBRARY_AUDIO.exists():
        con = sqlite3.connect(LIBRARY_AUDIO)
        rows += con.execute("SELECT text, div, file FROM recordings WHERE ? IS NULL OR text = ? "
                            "ORDER BY text, div", (slug, slug)).fetchall()
        con.close()
    out, sizes = {}, {}
    for text, div, file in rows:
        if file not in sizes:
            sizes[file] = (RECORDINGS / file).stat().st_size if (RECORDINGS / file).is_file() else None
        if sizes[file] is None:
            continue
        items = out.setdefault(text, [])
        item = next((i for i in items if i["file"] == file), None)
        if item:
            item["divs"].append(div)
        else:
            items.append({"n": div, "divs": [div], "file": file, "size": sizes[file]})
    return out


def download_of(slug, n):
    """{"download": {n, size, books}}: the download a book is in (its recording's: for a
    Bible chapter, the whole book's), for the reader's own download button; or {}."""
    item = next((i for i in downloads_of(slug).get(slug, []) if n in i["divs"]), None)
    return {"download": {"n": item["n"], "size": item["size"], "books": len(item["divs"])}} if item else {}


def words_json(slug, n):
    """{"line/index": word card}: every word card of a book (or chapter), as /api/lexword
    gives each (line: its segment), for a download to keep: the cards then open offline."""
    out = {}
    if not LIBRARY.exists():
        return None
    con = sqlite3.connect(LIBRARY)
    segs = con.execute("SELECT seq, content FROM segments WHERE text = ? AND div = ? AND seq > 0", (slug, n)).fetchall()
    con.close()
    for seq, content in segs:
        for i in range(len(content.split())):
            card = lexword_json(slug, n, seq, i)
            if card and (card.get("tokens") or "gloss" in card):  # (the Iliad's, with Gaza's word, all)
                out[f"{seq}/{i}"] = card
    return out


def images_json(slug, n):
    """The pictures of a book's words' lemmas (lemma_images, from Anki decks: see
    scripts/import_anki_images.py), for the reader to show by a word: {"lemmas": [{lemma,
    images: [{src, source, headword}, …]}, …], "lines": {line: [for each word: a lemma's place
    in "lemmas", or [its place, the paraphrase's word it comes by], or null]}, "sources":
    {source: name}}; a lemma's pictures best deck first (image_sources.rank), but for a deck
    hidden; a word in parts (κἀγώ), its first part with pictures. A word without pictures of
    its own has its paraphrase's (Gaza's Attic for the Iliad's Homeric, paraphrase_links:
    μῆνιν, τὴν ὀργὴν: ὀργή), where a word of it (not the article) is a printed form of a lemma
    with pictures (lemma_inflections: as accented, else the lemma most so printed). Served
    only, never kept offline (the service worker leaves them to the network)."""
    if not LIBRARY.exists():
        return None
    con = sqlite3.connect(LIBRARY)
    try:
        shown = {lemma for (lemma,) in con.execute(
            "SELECT DISTINCT lemma FROM lemma_images JOIN image_sources USING (source) WHERE NOT hidden AND lemma IS NOT NULL")}
        rows = [r for r in con.execute("""
            SELECT w.seq, w.word_index, l.lemma FROM lemma_words w JOIN lemmas l ON l.id = w.lemma
            WHERE w.text = ? AND w.div = ? ORDER BY w.seq, w.word_index, w.part""", (slug, n)) if r[2] in shown]
        own = {(seq, index) for seq, index, _ in rows}
        # The paraphrase's words, for the words without pictures of their own.
        para = [r for r in con.execute(
            "SELECT seq, word_index, para_word FROM paraphrase_links WHERE text = ? AND div = ? "
            "ORDER BY seq, word_index, para_seq, para_index", (slug, n)) if (r[0], r[1]) not in own]
        printed = {}
        for word in {w for *_, w in para}:
            printed.setdefault(search_spelling(word.strip(PARA_PUNCT)), set()).add(word)
        forms = {}
        for i in range(0, len(printed), 500):
            keys = list(printed)[i:i + 500]
            for bare, lemma, form, k in con.execute(f"""
                    SELECT i.bare, l.lemma, i.form, i.n FROM lemma_inflections i JOIN lemmas l ON l.id = i.lemma
                    WHERE i.bare IN ({",".join("?" * len(keys))})""", keys):
                forms.setdefault(bare, []).append((form, k, lemma))
        via = {}
        for seq, index, word in para:
            if (seq, index) in via:
                continue
            w = unicodedata.normalize("NFC", unicodedata.normalize("NFD", word.strip(PARA_PUNCT)).replace("\u0300", "\u0301"))
            hits = forms.get(search_spelling(w), [])
            same = [h for h in hits if h[0] in (w, w.lower())]
            lemma = max(same or hits, key=lambda h: h[1])[2] if same or hits else None
            if lemma in shown and lemma != "ὁ":
                via[seq, index] = (lemma, word.strip(PARA_PUNCT))
        names = sorted({r[2] for r in rows} | {lemma for lemma, _ in via.values()})
        images = {}
        for i in range(0, len(names), 500):
            chunk = names[i:i + 500]
            for lemma, file, source, headword in con.execute(f"""
                    SELECT i.lemma, i.file, i.source, i.headword FROM lemma_images i JOIN image_sources s USING (source)
                    WHERE i.lemma IN ({",".join("?" * len(chunk))}) AND NOT s.hidden
                    ORDER BY s.rank, i.method != 'lemma', i.note, i.ord""", chunk):
                images.setdefault(lemma, []).append({"src": f"/lemma-images/{file}", "source": source, "headword": headword})
        sources = dict(con.execute("SELECT source, name FROM image_sources"))
    except sqlite3.OperationalError:  # a library without pictures
        return {"lemmas": [], "lines": {}, "sources": {}}
    finally:
        con.close()
    place = {lemma: i for i, lemma in enumerate(names)}
    lines = {}

    def put(seq, index, value):
        words = lines.setdefault(seq, [])
        words += [None] * (index + 1 - len(words))
        if words[index] is None:
            words[index] = value
    for seq, index, lemma in rows:
        put(seq, index, place[lemma])
    for (seq, index), (lemma, word) in via.items():
        put(seq, index, [place[lemma], word])
    return {"lemmas": [{"lemma": lemma, "images": images[lemma]} for lemma in names],
            "lines": lines, "sources": sources}


PARA_PUNCT = ".,;·:—–“”‘’'\"()[]⟨⟩«»"  # (round a paraphrase's word)


def offline_json(slug, n):
    """What a text's download n (named by its first book) needs offline: its books (the
    reader's API for each) and its recording."""
    if not LIBRARY.exists():
        return None
    con = sqlite3.connect(LIBRARY)
    row = con.execute("SELECT 1 FROM texts WHERE slug = ?", (slug,)).fetchone()
    con.close()
    item = next((i for i in downloads_of(slug).get(slug, []) if i["n"] == n), None)
    if not row or not item:
        return None
    q = urllib.parse.quote(slug)
    return {"slug": slug, "n": n, "pages": [f"/api/text/{q}/{d}" for d in item["divs"]] + [f"/api/words/{q}/{d}" for d in item["divs"]],
            "audio": [{"url": f"/audio/{urllib.parse.quote(item['file'])}", "size": item["size"]}],
            "size": item["size"]}


# The day's readings: the Greek Orthodox Archdiocese of America's online chapel gives
# each day's, by the Ecumenical Patriarchate's typikon and calendar, as XML (cached in
# data/raw/lectionary/). Each reading, "St. Paul's First Letter to the Corinthians
# 12:27-31; 13:1-8", becomes a link to that selection of its book, if the library has it.
LECTIONARY = "https://onlinechapel.goarch.org/daily.asp?date={d.month}/{d.day}/{d.year}"
LECTIONARY_CACHE = ROOT / "data" / "raw" / "lectionary"
READING_KINDS = {"MG": "Εὐαγγέλιον τοῦ Ὄρθρου", "E": "Ἀπόστολος", "G": "Εὐαγγέλιον", "OT": "Ἀνάγνωσμα"}
READING_NAMES = {"Twelve Gospels": "Τὰ Δώδεκα Εὐαγγέλια τῶν Παθῶν"}  # readings given by name, not place
ORDINALS = {"first": "1", "second": "2", "third": "3", "fourth": "4", "1": "1", "2": "2", "3": "3", "4": "4",
            "i": "1", "ii": "2", "iii": "3", "iv": "4"}
# English names for books the Septuagint names otherwise: "I Kings" is 3 Kingdoms.
SEPTUAGINT_NAMES = {"1samuel": "1kingdoms", "2samuel": "2kingdoms", "1kings": "3kingdoms", "2kings": "4kingdoms"}


def lectionary_settings(con):
    try:
        row = con.execute("SELECT value FROM settings WHERE key = 'lectionary'").fetchone()
    except sqlite3.OperationalError:  # a library built before settings
        return None
    return json.loads(row[0]) if row else None


def book_slug(name, slugs, aliases):
    """The library's slug for a book as the chapel names it: by the name's words ("Acts of
    the Apostles": acts, "Song of Songs": songofsongs), its ordinal ("First Letter to the
    Corinthians": 1corinthians) or, for a letter named by its writer, his name ("St.
    Peter's First Universal Letter": 1peter); or as the catalogue's aliases say."""
    if name in aliases:
        return aliases[name]
    m = re.match(r"(?:St\.?\s+(\w+)['’]s?\s+)?(.*)", name)
    author, rest = (m[1] or "").lower(), m[2].lower()
    words = re.findall(r"[a-z0-9]+", rest)
    ordinal = next((ORDINALS[w] for w in words if w in ORDINALS), "")
    words = [w for w in words if w not in ORDINALS]
    plain = [w for w in words if w not in ("letter", "universal", "epistle", "to", "the", "of", "according", "gospel")]
    for guess in ("".join(words), "".join(plain), plain[0] if plain else "", author):
        for slug in (ordinal + guess, guess):  # (Jude's "First Universal Letter": his only one)
            slug = slug if slug in slugs else SEPTUAGINT_NAMES.get(slug, slug)
            if guess and slug in slugs:
                return slug
    return None


# The chapel numbers the Old Testament as English Bibles do, by the Hebrew; where the
# Septuagint's chapters differ, its verses' English numbers: book -> [(chapter, first
# verse, last verse, English chapter, verse shift)]. (Rahlfs's Proverbs keeps the Hebrew
# numbers for the chapters it moves, as refs "31:10" in another chapter.)
LXX_TO_ENGLISH = {"Joel": [(3, 1, 5, 2, 27), (4, 1, 99, 3, 0)], "Malachi": [(3, 19, 24, 4, -18)]}


def selection(con, slug, book, refs):
    """(chapter, the reader's &v=, the place as shown) for a reading, "12:27-31; 13:1-8"
    (→ 12, "27-31,13.1-8", "12:27-13:8"): the text's verses whose English chapter and
    verse fall in its pieces, as runs of consecutive verses (a missing verse at the end, a
    chapter numbered otherwise, are taken care of). None if it doesn't read."""
    ranges, chapter = [], None
    for part in re.split(r"\s*[,;]\s*", refs.strip()):
        m = re.fullmatch(r"(?:(\d+):)?(\d+)[a-z]?(?:\s*[-–]\s*(?:(\d+):)?(\d+)[a-z]?)?", part)
        if not m or (m[1] is None and chapter is None):
            return None
        chapter = int(m[1]) if m[1] else chapter
        first = (chapter, int(m[2]))
        if m[4]:
            chapter = int(m[3]) if m[3] else chapter
        ranges.append((first, (chapter, int(m[4] or m[2]))))
    verses = con.execute("SELECT div, ref FROM segments WHERE text = ? AND seq > 0 ORDER BY div, seq", (slug,)).fetchall()

    def english(div, ref):
        if m := re.fullmatch(r"(\d+):(\d+)[a-z]*", ref or ""):
            return int(m[1]), int(m[2])
        if not (m := re.fullmatch(r"(\d+)[a-z]*", ref or "")):
            return None
        c, v = div, int(m[1])
        for ch, a, b, to, shift in LXX_TO_ENGLISH.get(book, []):
            if c == ch and a <= v <= b:
                return to, v + shift
        return c, v

    pieces = []  # (first index, last index) of each run, in the order read
    for a, b in ranges:
        hit = [i for i, (div, ref) in enumerate(verses) if (e := english(div, ref)) and a <= e <= b]
        for i in hit:
            if pieces and pieces[-1][1] == i - 1:
                pieces[-1][1] = i
            else:
                pieces.append([i, i])
    if not pieces:
        return None
    def place(div, ref):
        return ref if ":" in ref else f"{div}:{ref}"

    shown, last = [], None  # a chapter named only where it changes: 21:8-9, 25-27
    for i, j in pieces:
        a, b = place(*verses[i]), place(*verses[j])
        start = a.split(":")[1] if a.split(":")[0] == last else a
        shown.append(start if i == j else f"{start}-{b.split(':')[1] if a.split(':')[0] == b.split(':')[0] else b}")
        last = b.split(":")[0]
    out, current = [], verses[pieces[0][0]][0]
    for i, j in pieces:
        (d1, r1), (d2, r2) = verses[i], verses[j]
        # (a ref numbered from another chapter, 31:8, always with its own: "31:8" alone is chapter 31)
        text = (f"{d1}." if d1 != current or ":" in r1 else "") + r1
        if j > i:  # (an end at an addition, 18d, as its verse: the reader takes 18 to include 18a-d)
            text += "-" + (f"{d2}." if d2 != d1 or ":" in r2 else "") + re.sub(r"^(\d+)[a-z]+$", r"\1", r2)
        out.append(text)
        current = d2
    return verses[pieces[0][0]][0], ",".join(out), ", ".join(shown)


def lectionary_json(day):
    """{"date", "readings": [{"kind", "label", "href"}]}: the day's readings, in the order
    read (Matins, the Liturgy, then the Old Testament's), linking where the library has them."""
    try:
        date = datetime.date.fromisoformat(day)
    except ValueError:
        return None
    if not LIBRARY.exists():
        return None
    con = sqlite3.connect(LIBRARY)
    settings = lectionary_settings(con)
    titles = dict(con.execute("SELECT slug, title FROM texts"))
    if not settings:
        con.close()
        return None
    path = LECTIONARY_CACHE / f"{date.isoformat()}.xml"
    if not path.exists():
        try:
            req = urllib.request.Request(LECTIONARY.format(d=date), headers={"User-Agent": "Mozilla/5.0 (acroaterium)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read()
            ElementTree.fromstring(body)  # only a whole answer is kept
        except (OSError, ElementTree.ParseError):
            con.close()
            return {"date": date.isoformat(), "readings": [], "error": "The readings couldn't be fetched."}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError:  # a broken copy: fetched again next time
        path.unlink(missing_ok=True)
        con.close()
        return {"date": date.isoformat(), "readings": [], "error": "The readings couldn't be read."}
    readings = []
    for r in root.iter("reading"):
        kind = r.findtext("type") or ""
        title = next((t.strip() for t in (r.findtext("translation/shorttitle"), r.findtext("translation/title"))
                      if t and re.search(r"\d:\d", t)), (r.findtext("translation/title") or "").strip())
        title = re.sub(r"(\d):\s+(?=\d+:)", r"\1; ", title)  # a slip: "13:31-37: 14:1-2"
        # A reading put together from several books (Holy Thursday's Gospel): a link for each.
        for part in re.split(r"[;,]\s*(?=[A-Za-z])", title):
            m = re.match(r"(.*?)\s+(\d+:\d.*)$", part)
            slug = book_slug(m[1], titles, settings.get("aliases", {})) if m else None
            sel = selection(con, slug, m[1], m[2]) if slug else None
            readings.append({
                "kind": READING_KINDS.get(kind, "Ἀνάγνωσμα"),
                "order": list(READING_KINDS).index(kind) if kind in READING_KINDS else len(READING_KINDS),
                "label": f"{titles[slug]} {sel[2]}" if sel else READING_NAMES.get(part, part),
                "href": f"read.html?{urllib.parse.urlencode({'text': slug, 'book': sel[0], 'v': sel[1]})}" if sel else None})
    con.close()
    readings.sort(key=lambda x: x.pop("order"))
    return {"date": date.isoformat(), "readings": readings}


def notes_json(slug, n, a, b):
    """A library text's notes on segments a..b, shaped like scholia_json."""
    if not LIBRARY.exists():
        return None
    con = sqlite3.connect(LIBRARY)
    sources = con.execute("SELECT id, siglum, name, lang FROM note_sources WHERE text = ? ORDER BY sort",
                          (slug,)).fetchall()
    ref = dict(con.execute("SELECT seq, ref FROM segments WHERE text = ? AND div = ?", (slug, n)))
    rows = con.execute("SELECT pos_from, pos_to, source, body, parts FROM notes "
                       "WHERE text = ? AND div = ? AND pos_from <= ? AND pos_to >= ? ORDER BY seq",
                       (slug, n, b, a)).fetchall()
    con.close()
    notes = {}
    for first, last, source, body, parts in rows:
        a, b = ref.get(first, str(first)), ref.get(last, str(last))  # (a note's lines may run past the book's)
        span = {"from": a, "to": b} if a != b else {}
        if parts:
            notes.setdefault(source, []).append({"parts": json.loads(parts), **span})
        else:
            m = LEMMA.fullmatch(body)
            notes.setdefault(source, []).append({"lemma": m[1].strip() if m else None,
                                                 "text": m[2] if m else body, **span})
    return {"sources": [{"id": sid, "siglum": siglum, "name": name, "lang": lang, "notes": notes[sid]}
                        for sid, siglum, name, lang in sources if sid in notes]}


def text_json(slug, n):
    if not LIBRARY.exists():
        return None
    con = sqlite3.connect(LIBRARY)
    t = con.execute(f"SELECT title, author, form, cite, credit, notes_credit, {collection_of(con)}, "
                    f"{collection_of(con, 'short')}, {collection_of(con, 'eyebrow')} FROM texts "
                    "WHERE slug = ? AND reader = 'text'", (slug,)).fetchone()
    rows = con.execute("SELECT seq, ref, content, speaker, para FROM segments WHERE text = ? AND div = ? "
                       "ORDER BY seq", (slug, n)).fetchall() if t else []
    books = [{"n": b, "label": label} for b, label in con.execute(
        "SELECT n, label FROM divisions WHERE text = ? ORDER BY n", (slug,))]
    # How many notes each segment has (a note covering several counts for each).
    notes = {}
    for a, b in con.execute("SELECT pos_from, pos_to FROM notes WHERE text = ? AND div = ?", (slug, n)):
        for seq in range(a, b + 1):
            notes[seq] = notes.get(seq, 0) + 1
    has_notes = con.execute("SELECT 1 FROM note_sources WHERE text = ? LIMIT 1", (slug,)).fetchone()
    try:  # parsed words (a word card for each): the New Testament, the treebanked texts
        has_words = con.execute("SELECT 1 FROM words WHERE text = ? LIMIT 1", (slug,)).fetchone()
        has_words = has_words or con.execute("SELECT 1 FROM treebank WHERE text = ? LIMIT 1", (slug,)).fetchone()
        try:  # (or the lemmatiser's, where there's no gold parsing)
            has_words = has_words or con.execute("SELECT 1 FROM auto_words WHERE text = ? LIMIT 1", (slug,)).fetchone()
        except sqlite3.OperationalError:  # a library built before automatic parsing
            pass
    except sqlite3.OperationalError:  # a library built before there were any
        has_words = None
    translated, translation_credits = {"en": {}, "el": {}, "la": {}, "paraphrase": {}}, {}
    try:  # a translated verse under each verse: English, modern Greek, Latin
        for seq, lang, content in con.execute("SELECT seq, lang, content FROM translations WHERE text = ? AND div = ?",
                                              (slug, n)):
            translated.setdefault(lang, {})[seq] = content
        translation_credits = dict(con.execute("SELECT lang, credit FROM translation_credits WHERE text = ?", (slug,)))
    except sqlite3.OperationalError:  # a library built before there were translations
        pass
    try:  # or English in passages of several lines, after the last line of each (Murray's Homer); one may
        # end within its last segment, after its word wordTo (a long speech's part, in prose)
        passages = [{"from": a, "to": b, "text": content, **({"wordTo": w} if w is not None else {})}
                    for a, b, content, w in con.execute(
            "SELECT seq_from, seq_to, content, word_to FROM translation_passages WHERE text = ? AND div = ? "
            "AND lang = 'en' ORDER BY seq_to, word_to IS NULL, word_to", (slug, n))]
    except sqlite3.OperationalError:
        try:  # (a library built before word_to)
            passages = [{"from": a, "to": b, "text": content} for a, b, content in con.execute(
                "SELECT seq_from, seq_to, content FROM translation_passages WHERE text = ? AND div = ? AND lang = 'en' "
                "ORDER BY seq_from", (slug, n))]
        except sqlite3.OperationalError:
            passages = []
    english, modern, latin, paraphrase = translated["en"], translated["el"], translated["la"], translated["paraphrase"]
    try:  # a part's label in English, beside the Greek (the prayers: "cite": label)
        ref_labels = dict(con.execute("SELECT ref, label FROM ref_labels WHERE text = ? AND lang = 'en'", (slug,)))
    except sqlite3.OperationalError:  # a library built before there were any
        ref_labels = {}
    try:  # an edition's column where a segment begins (Migne's), greyed in the text
        marks = dict(con.execute("SELECT seq, mark FROM ref_marks WHERE text = ? AND div = ?", (slug, n)))
    except sqlite3.OperationalError:  # a library built before there were any
        marks = {}
    glosses, groups = paraphrase_glosses(con, slug, n)
    con.close()
    if not rows:
        return None
    title, author, form, cite, credit, notes_credit, collection, short, eyebrow = t
    recording, timed = library_audio(slug, n)
    verses = []
    for seq, ref, content, speaker, para in rows:
        v = {"line": seq, "ref": ref, "text": content,
             **({"speaker": speaker} if speaker else {}), **({"para": True} if para == 1 else {}),
             **({"br": True} if para == 2 else {}),  # (a new line in the paragraph)
             **({"scholia": notes[seq]} if seq in notes else {}),
             **({"mark": marks[seq]} if seq in marks else {})}
        if seq in english:
            v["english"] = english[seq]
        if seq in modern:
            v["modern"] = modern[seq]
        if seq in latin:
            v["latin"] = latin[seq]
        if seq in paraphrase:  # a paraphrase under the verse (Gaza's), and its word for each word
            v["paraphrase"] = paraphrase[seq]
        if seq in glosses:
            v["glosses"] = glosses[seq]
        if seq in groups:
            v["groups"] = groups[seq]
        pieces = timed.get(seq)
        if pieces and form == "verse":
            v.update(pieces[0][1])
        elif pieces:  # prose: the segment's sentences, each timed (their text is the segment's)
            v["parts"] = [{"text": text, **times} for text, times in pieces]
        verses.append(v)
    credits = [{"text": credit}] if credit else []
    # Shown while their translation is (as for the Iliad's).
    if modern and translation_credits.get("el"):
        credits.append({"text": translation_credits["el"], "id": "modern-credit"})
    if latin and translation_credits.get("la"):
        credits.append({"text": translation_credits["la"], "id": "latin-credit"})
    if (english or passages) and translation_credits.get("en"):
        credits.append({"text": translation_credits["en"], "id": "translation-credit"})
    if recording and recording[1]:
        credits.append({"text": recording[1]})
    # The chapters of a book of the Bible share its recording: the reader goes on into the
    # next as it plays.
    # The other books of its collection (a Testament): the reader's title is a menu of them.
    con = sqlite3.connect(LIBRARY)
    # (with each one's last book: the reader goes back to it from the start of the one after)
    members = [{"slug": s, "title": tt, "last": last} for s, tt, last in con.execute(
        "SELECT slug, title, (SELECT MAX(n) FROM divisions WHERE text = slug) FROM texts WHERE collection = ? ORDER BY sort",
        (collection,))] if collection else []
    con.close()
    goes_on = False
    if recording and timed:
        following, after = library_audio(slug, n + 1)
        if following and following[0] == recording[0] and after:
            ends = max(t["end"] for pieces in timed.values() for _, t in pieces)
            starts = min(t["start"] for pieces in after.values() for _, t in pieces)
            goes_on = abs(starts - ends) < 2  # the recording reads it next (not, e.g., chapters in reverse)
    return {"book": n, "text": slug, "title": title, "author": author, "form": form, "cite": cite,
            **({"collection": collection, "collectionTexts": members} if collection else {}),
            **({"next": n + 1} if goes_on else {}),
            "eyebrow": eyebrow or (f"{author} · {title}" if author else title), **({"short": short} if short else {}),
            "features": (["scholia"] if has_notes else []) + (["audio"] if recording else [])
                        + (["paraphrase"] if paraphrase else [])
                        + (["words"] if has_words else []) + (["translation"] if english or passages else [])
                        + (["modern"] if modern else []) + (["latin"] if latin else []),
            "notesCredit": notes_credit, "books": books if len(books) > 1 else [],
            "audio": f"/audio/{urllib.parse.quote(recording[0])}" if recording else None,
            # The recording reads other books too (a book of the Bible, its chapters in one
            # file): the reader's timeline spans this one's verses, not the whole file.
            **({"clip": True} if recording and recording[2] > 1 else {}),
            **download_of(slug, n),
            **({"refLabels": {r: ref_labels[r] for r in dict.fromkeys(v["ref"] for v in verses) if r in ref_labels}}
               if ref_labels else {}),
            "verses": verses, "translation": passages, "credits": credits}


def paraphrase_glosses(con, slug, n):
    """A book's words as its paraphrase renders them: {seq: {word_index: "words"}}, and the
    words that share a word of the paraphrase: {seq: {word_index: group id}} (word_groups)."""
    glosses, sharers = {}, {}
    try:
        rows = con.execute("SELECT seq, word_index, para_seq, para_index, para_word FROM paraphrase_links "
                           "WHERE text = ? AND div = ? ORDER BY seq, word_index, para_seq, para_index", (slug, n)).fetchall()
    except sqlite3.OperationalError:  # a library built before paraphrases
        return {}, {}
    for seq, wi, para_seq, para_index, word in rows:
        glosses.setdefault(seq, {}).setdefault(wi, []).append(EDGE_PUNCT.sub("", word))
        sharers.setdefault((para_seq, para_index), set()).add((seq, wi))
    return ({seq: {i: " ".join(w) for i, w in ws.items()} for seq, ws in glosses.items()},
            word_groups(sharers.values()))


def cut_of(con):
    """SQL for units.cut, or 0 in timings made before there was one."""
    return "cut" if "cut" in {r[1] for r in con.execute("PRAGMA table_info(units)")} else "0"


def library_audio(slug, n):
    """A library book's aligned recording, (file under recordings/, credit, how many
    books share the file) or None, and
    its timings: {seq: [(text, {start, speechEnd, end}), ...]} by sentence (one per line
    of verse)."""
    if not LIBRARY_AUDIO.exists():
        return None, {}
    con = sqlite3.connect(LIBRARY_AUDIO)
    try:
        rec = con.execute("SELECT file, credit, (SELECT count(*) FROM recordings o WHERE o.file = r.file) "
                          "FROM recordings r WHERE text = ? AND div = ?", (slug, n)).fetchone()
        if not rec or not (RECORDINGS / rec[0]).is_file():
            return None, {}
        timed = {}
        for seq, content, start, speech_end, end, cut in con.execute(
                f"SELECT seq, content, start, speech_end, end, {cut_of(con)} FROM units WHERE text = ? AND div = ? "
                "ORDER BY seq, piece", (slug, n)):
            timed.setdefault(seq, []).append(
                (content, {"start": round(start, 2), "speechEnd": round(speech_end, 2), "end": round(end, 2),
                           **({"cut": True} if cut else {})}))
        return rec, timed
    finally:
        con.close()


# "μῆνιν] παρὰ τὸ μένω …": the lemma (the words commented on) and the note.
LEMMA = re.compile(r"([^\]]{1,90})\]\s*(.*)", re.S)


# AGDT dependency labels; suffixes _CO (coordinated) and _AP (in apposition).
RELATIONS = {
    "PRED": "predicate", "SBJ": "subject", "OBJ": "object", "ATR": "attribute",
    "ADV": "adverbial", "ATV": "complement", "AtvV": "complement", "PNOM": "predicate nominal",
    "OCOMP": "object complement", "COORD": "coordinator", "APOS": "apposition",
    "AuxP": "preposition", "AuxC": "subordinating conjunction", "AuxY": "sentence particle",
    "AuxZ": "emphasizing particle", "AuxV": "auxiliary verb", "AuxX": "comma", "AuxK": "end punctuation",
    "AuxG": "bracket", "ExD": "external (e.g. vocative, ellipsis)", "XSEG": "word fragment",
}


def relation_label(rel):
    if not rel:
        return None
    base, *suffixes = rel.split("_")
    label = RELATIONS.get(base, base)
    if "AP" in suffixes:
        label += ", in apposition"
    if "CO" in suffixes:
        label += ", coordinated"
    return label


# Robinson's morphology codes (the New Testament's parsing): part of speech, then for
# nominals case-number-gender (N-NSF), for verbs tense-voice-mood and person-number or
# case-number-gender (V-AAI-3S, V-2AAP-NSM), for pronouns person where it has one (P-1GS).
ROBINSON_POS = {
    "N": "noun", "A": "adjective", "T": "article", "V": "verb", "P": "personal pronoun",
    "R": "relative pronoun", "C": "reciprocal pronoun", "D": "demonstrative pronoun",
    "K": "correlative pronoun", "I": "interrogative pronoun", "X": "indefinite pronoun",
    "Q": "correlative or interrogative pronoun", "F": "reflexive pronoun", "S": "possessive pronoun",
    "ADV": "adverb", "CONJ": "conjunction", "COND": "conditional", "PRT": "particle",
    "PREP": "preposition", "INJ": "interjection", "ARAM": "Aramaic word", "HEB": "Hebrew word"}
ROBINSON_CASE = {"N": "nominative", "G": "genitive", "D": "dative", "A": "accusative", "V": "vocative"}
ROBINSON_NUMBER = {"S": "singular", "P": "plural"}
ROBINSON_GENDER = {"M": "masculine", "F": "feminine", "N": "neuter"}
ROBINSON_PERSON = {"1": "1st person", "2": "2nd person", "3": "3rd person"}
ROBINSON_TENSE = {"P": "present", "I": "imperfect", "F": "future", "A": "aorist", "R": "perfect", "L": "pluperfect"}
ROBINSON_VOICE = {"A": "active", "M": "middle", "P": "passive", "E": "middle or passive",
                  "D": "middle deponent", "O": "passive deponent", "N": "middle or passive deponent",
                  "Q": "impersonal active", "X": "no voice"}
ROBINSON_MOOD = {"I": "indicative", "S": "subjunctive", "O": "optative", "M": "imperative",
                 "N": "infinitive", "P": "participle", "R": "imperative participle"}
ROBINSON_SUFFIX = {"C": "comparative", "S": "superlative", "N": "negative", "I": "interrogative",
                   "K": "crasis", "ATT": "Attic form", "ABB": "abbreviated", "PRI": "proper name, indeclinable",
                   "NUI": "numeral, indeclinable", "LI": "letter, indeclinable", "OI": "indeclinable"}


def robinson_morph(code):
    """["verb", "aorist", "active", "indicative", "3rd person", "singular"] for V-AAI-3S."""
    head, *rest = code.split("-")
    out = [ROBINSON_POS.get(head, head)]

    def cng(part):  # case, number, gender
        return [m[c] for m, c in zip((ROBINSON_CASE, ROBINSON_NUMBER, ROBINSON_GENDER), part) if c in m]

    if head == "V" and rest:
        tvm = rest[0]
        second = tvm[0] == "2"
        tvm = tvm[1:] if second else tvm
        if len(tvm) >= 3:
            out += [("second " if second else "") + ROBINSON_TENSE.get(tvm[0], tvm[0]),
                    ROBINSON_VOICE.get(tvm[1], tvm[1]), ROBINSON_MOOD.get(tvm[2], tvm[2])]
        for part in rest[1:]:
            if part[:1] in ROBINSON_PERSON:
                out += [ROBINSON_PERSON[part[0]], *(ROBINSON_NUMBER[c] for c in part[1:] if c in ROBINSON_NUMBER)]
            elif part in ROBINSON_SUFFIX:
                out.append(ROBINSON_SUFFIX[part])
            else:
                out += cng(part)
        return out
    for part in rest:
        if part in ROBINSON_SUFFIX:
            out.append(ROBINSON_SUFFIX[part])
        elif head == "S" and part[:1] in ROBINSON_PERSON:  # S-1SNSM: person, the possessor's number, then CNG
            out += [ROBINSON_PERSON[part[0]], f"{ROBINSON_NUMBER.get(part[1:2], '')} possessor".strip(), *cng(part[2:])]
        elif part[:1] in ROBINSON_PERSON:  # P-1GS: person, then case and number
            out += [ROBINSON_PERSON[part[0]], *cng(part[1:])]
        else:
            out += cng(part)
    return out


# CATSS's morphology codes (the Septuagint's parsing): a type (N, V, RA, ...; C+RP for a
# crasis), a dot, then case-number-gender (N.DSF; A.ASMC with a degree) or for verbs
# tense-voice-mood and person-number or, for participles, case-number-gender (V.AAI3S, V.PAPNSM).
CATSS_TYPE = {
    "N": "noun", "A": "adjective", "RA": "article", "RD": "demonstrative pronoun", "RI": "interrogative or indefinite pronoun",
    "RP": "personal pronoun", "RR": "relative pronoun", "RX": "indefinite relative pronoun", "C": "conjunction",
    "X": "particle", "I": "interjection", "M": "numeral", "P": "preposition", "D": "adverb", "V": "verb"}
CATSS_CASE = {"N": "nominative", "G": "genitive", "D": "dative", "A": "accusative", "V": "vocative"}
CATSS_NUMBER = {"S": "singular", "D": "dual", "P": "plural"}
CATSS_GENDER = {"M": "masculine", "F": "feminine", "N": "neuter"}
CATSS_DEGREE = {"C": "comparative", "S": "superlative"}
CATSS_TENSE = {"P": "present", "I": "imperfect", "F": "future", "A": "aorist", "X": "perfect", "Y": "pluperfect"}
CATSS_VOICE = {"A": "active", "M": "middle", "P": "passive"}
CATSS_MOOD = {"I": "indicative", "D": "imperative", "S": "subjunctive", "O": "optative", "N": "infinitive", "P": "participle"}
CATSS_PERSON = {"1": "1st person", "2": "2nd person", "3": "3rd person"}


def catss_morph(code):
    """["verb", "aorist", "active", "indicative", "3rd person", "singular"] for V.AAI3S."""
    types, _, parse = code.partition(".")
    out = [" + ".join(CATSS_TYPE.get(t, t) for t in types.split("+"))]
    if types == "N" and not parse:
        return out + ["proper name, indeclinable"]

    def cng(p):  # case, number, gender, degree
        return [m[c] for m, c in zip((CATSS_CASE, CATSS_NUMBER, CATSS_GENDER, CATSS_DEGREE), p) if c in m]

    if types.endswith("V") and len(parse) >= 3:
        out += [m[c] for m, c in zip((CATSS_TENSE, CATSS_VOICE, CATSS_MOOD), parse) if c in m]
        rest = parse[3:]
        if parse[2] == "P":
            out += cng(rest)
        elif rest[:1] in CATSS_PERSON:
            out += [CATSS_PERSON[rest[0]], *[CATSS_NUMBER[c] for c in rest[1:2] if c in CATSS_NUMBER]]
    else:
        out += cng(parse)
    return out


def letters(word):
    """A word's letters, without accents or breathings, lowercase: Δαυίδ and Δαυιδ alike."""
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if unicodedata.category(c)[0] == "L")


def postag_morph(postag):
    """["verb", "3rd", "singular", "aorist", ...] for an AGDT postag, in word_json's order."""
    m = {name: table.get(c) for c, (name, table) in zip((postag or "").ljust(9, "-"), POSTAG)}
    order = (("pos", "person", "number", "tense", "mood", "voice", "gender", "gram_case", "degree") if m["person"]
             else ("pos", "tense", "mood", "voice", "gender", "number", "gram_case", "degree"))
    return [m[k] for k in order if m[k]]


# Morpheus's abbreviations, spelled out as the treebanks' parsing is.
MORPHEUS_WORDS = {
    "pres": "present", "imperf": "imperfect", "fut": "future", "aor": "aorist", "perf": "perfect",
    "plup": "pluperfect", "futperf": "future perfect", "ind": "indicative", "subj": "subjunctive",
    "opt": "optative", "imperat": "imperative", "inf": "infinitive", "part": "participle",
    "act": "active", "mid": "middle", "pass": "passive", "mp": "middle/passive", "sg": "singular",
    "pl": "plural", "dual": "dual", "nom": "nominative", "gen": "genitive", "dat": "dative",
    "acc": "accusative", "voc": "vocative", "masc": "masculine", "fem": "feminine", "neut": "neuter",
    "comp": "comparative", "superl": "superlative", "indeclinable": None, "adverbial": "adverbial",
}
UPOS_WORDS = {
    "NOUN": "noun", "PROPN": "proper noun", "VERB": "verb", "AUX": "verb", "ADJ": "adjective",
    "ADV": "adverb", "PRON": "pronoun", "DET": "article", "ADP": "preposition", "CCONJ": "conjunction",
    "SCONJ": "conjunction", "PART": "particle", "INTJ": "interjection", "NUM": "numeral",
}


def morpheus_morph(pos, readings):
    """["verb", "aorist", "indicative", ...]: a part of speech and Morpheus's reading(s),
    spelled out; readings the context couldn't decide between joined by "or"."""
    out = [UPOS_WORDS[pos]] if pos in UPOS_WORDS else []
    for k, reading in enumerate(r for rs in readings for r in rs.split(" | ")):
        if k:
            out.append("or")
        out += [" or ".join(MORPHEUS_WORDS.get(p, p) for p in w.split("/")) for w in reading.split()
                if MORPHEUS_WORDS.get(w, w) is not None]  # (nom/voc: nominative or vocative)
    return out


def automatic_parsing(slug, n, seq, index):
    """The lemmatiser's parsing of a word without gold parsing (auto_words), as a card's token."""
    con = sqlite3.connect(LIBRARY)
    try:
        columns = {r[1] for r in con.execute("PRAGMA table_info(auto_words)")}
        row = con.execute(f"SELECT lemma, headword, definition, pos, morph, confidence, alternative, "
                          f"{'also' if 'also' in columns else 'NULL'}, {'checked' if 'checked' in columns else 'NULL'} FROM auto_words "
                          "WHERE text = ? AND div = ? AND seq = ? AND word_index = ?", (slug, n, seq, index)).fetchone()
    except sqlite3.OperationalError:  # a library built before automatic parsing
        return None
    finally:
        con.close()
    if not row:
        return None
    lemma, headword, definition, pos, morph, confidence, alternative, also, checked = row
    note = "Automatic parsing (may be wrong)."
    # The other readings the lemmatiser thinks worth showing, each in full (the card lists them).
    others = [{"lemma": a.get("headword") or (a.get("lemma") or "").rstrip("0123456789"), "definition": a.get("definition"),
               "morph": morpheus_morph(None, a.get("morph") or [])} for a in json.loads(also or "[]")]
    other = (alternative or "").rstrip("0123456789")
    if checked:
        pass
    elif others:
        note = "Automatic parsing, uncertain."
    elif confidence is not None and confidence < 0.6 and other and other != lemma.rstrip("0123456789"):
        note = f"Automatic parsing, uncertain: perhaps {other}."  # (not where it's only which λέγω)
    return {"form": None, "lemma": headword or lemma.rstrip("0123456789"), "definition": definition,
            "morph": morpheus_morph(pos, json.loads(morph or "[]")), "note": note, "_lemma": lemma,
            **({"also": others} if others else {})}


def treebank_match(con):
    """SQL for treebank.match, or NULL in a library built before it."""
    return "t.match" if "match" in {r[1] for r in con.execute("PRAGMA table_info(treebank)")} else "NULL"


def paraphrase_word(con, slug, n, seq, index):
    """The words of a text's paraphrase rendering a word ([] if none), or None where the
    text has no paraphrase."""
    try:
        if not con.execute("SELECT 1 FROM paraphrase_links WHERE text = ? LIMIT 1", (slug,)).fetchone():
            return None
    except sqlite3.OperationalError:  # a library built before paraphrases
        return None
    return [w for (w,) in con.execute("SELECT para_word FROM paraphrase_links WHERE text = ? AND div = ? AND seq = ? "
                                       "AND word_index = ? ORDER BY para_seq, para_index", (slug, n, seq, index))]


def lexword_json(slug, n, seq, index):
    """A library text's word: its parsing (the Iliad's and others': a hand-made treebank's;
    the New Testament: Robinson's, with Dodson's lexicon; the Septuagint: CATSS's, with its
    own lemmas and glosses) for the reader's card; each token's lemma with its references
    in every text ("references": {lemma, n}, for /api/references), where it has some."""
    card = parsing_json(slug, n, seq, index)
    if card and card.get("tokens"):
        con = sqlite3.connect(LIBRARY)
        try:
            for token in card["tokens"]:
                row = con.execute("SELECT l.lemma, l.n FROM lemma_forms f JOIN lemmas l ON l.id = f.lemma "
                                  "WHERE f.form = ?", (token.pop("_lemma", None) or "",)).fetchone()
                if row:
                    token["references"] = {"lemma": row[0], "n": row[1]}
        except sqlite3.OperationalError:  # a library built before the lemma index
            for token in card["tokens"]:
                token.pop("_lemma", None)
        finally:
            con.close()
    return card


def references_json(lemma, query):
    """Every word of a lemma (as the lemma index spells it: a card's "references") in the
    texts the library lists: {lemma, definition, n, texts: [{slug, title, short, cite, books, n}], within,
    hits: [{slug, div, label, seq, ref, words: [word index, ...], content}], offset, more}, a
    verse (segment) a hit, in the library's order, from offset (limit verses). in: only
    those of part of the library, as a reading plan names it (plan_target: a category, a
    collection, a text, or some of its books; text=<slug> as in=<slug>), and within: {title,
    n, texts: {slug: n}}, the lemma's words there; else within is null."""
    if not LIBRARY.exists():
        return None
    q = urllib.parse.parse_qs(query)
    offset = max(0, int(q.get("offset", ["0"])[0] or 0))
    limit = min(500, max(1, int(q.get("limit", ["100"])[0] or 100)))
    target = q.get("in", q.get("text", [None]))[0]
    con = sqlite3.connect(LIBRARY)
    try:
        row = con.execute("SELECT id, definition FROM lemmas WHERE lemma = ?", (unicodedata.normalize("NFC", lemma),)).fetchone()
        if not row:
            return None
        lid, definition = row
        shown = listed(con)  # (texts' columns: lemma_words has none of those names)
        texts = con.execute(
            f"SELECT w.text, t.title, {collection_of(con, 'short')}, t.cite, "
            "(SELECT count(*) FROM divisions d WHERE d.text = w.text), count(*) FROM lemma_words w "
            f"JOIN texts t ON t.slug = w.text WHERE w.lemma = ? AND {shown} GROUP BY w.text ORDER BY t.sort",
            (lid,)).fetchall()
        where, params, within = "1", [], None
        if target:
            part = plan_target(con, target)
            if not part:
                return None
            title, members, _ = part
            places = [(slug, books) for slug, _, _, books in members]
            where = " OR ".join("(w.text = ?" + (" AND w.div BETWEEN ? AND ?)" if books else ")") for _, books in places)
            params = [x for slug, books in places for x in (slug, *(books or ()))]
            counts = dict(con.execute(f"SELECT w.text, count(*) FROM lemma_words w JOIN texts t ON t.slug = w.text "
                                      f"WHERE w.lemma = ? AND {shown} AND ({where}) GROUP BY w.text", (lid, *params)))
            within = {"title": title, "n": sum(counts.values()), "texts": counts}
        verses = con.execute(
            f"SELECT w.text, w.div, w.seq, group_concat(DISTINCT w.word_index) FROM lemma_words w "
            f"JOIN texts t ON t.slug = w.text WHERE w.lemma = ? AND {shown} AND ({where}) "
            "GROUP BY w.text, w.div, w.seq ORDER BY t.sort, w.div, w.seq LIMIT ? OFFSET ?",
            (lid, *params, limit + 1, offset)).fetchall()
        hits = []
        for slug, div, seq, indexes in verses[:limit]:
            ref, content = con.execute("SELECT ref, content FROM segments WHERE text = ? AND div = ? AND seq = ?",
                                       (slug, div, seq)).fetchone()
            label = con.execute("SELECT label FROM divisions WHERE text = ? AND n = ?", (slug, div)).fetchone()
            hits.append({"slug": slug, "div": div, "label": label[0] if label else None, "seq": seq, "ref": ref,
                         "words": sorted(map(int, str(indexes).split(","))), "content": content})
    except sqlite3.OperationalError:  # a library built before the lemma index
        return None
    finally:
        con.close()
    return {"lemma": unicodedata.normalize("NFC", lemma), "definition": definition, "n": sum(t[5] for t in texts),
            "texts": [{"slug": t[0], "title": t[1], "short": t[2], "cite": t[3], "books": t[4], "n": t[5]}
                      for t in texts],
            "within": within, "hits": hits, "offset": offset, "more": len(verses) > limit}


def search_spelling(word):
    """A word as the search has it: in small letters, without accents, breathings or other
    marks, σ for ς (library.py's search_key)."""
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if not unicodedata.combining(c)).replace("ς", "σ")


def index_lemma(con, lemma):
    """(lemma, n, definition) of the lemma index's lemma for one Morpheus names: spelled so,
    or so in a parsing table (γίνομαι: γίγνομαι), or the one lemma of its letters; or None."""
    lemma = unicodedata.normalize("NFC", lemma)
    rows = (con.execute("SELECT lemma, n, definition FROM lemmas WHERE lemma = ?", (lemma,)).fetchall()
            or con.execute("SELECT l.lemma, l.n, l.definition FROM lemma_forms f JOIN lemmas l ON l.id = f.lemma "
                           "WHERE f.form = ?", (lemma,)).fetchall()
            or con.execute("SELECT lemma, n, definition FROM lemmas WHERE bare = ?", (search_spelling(lemma),)).fetchall())
    return rows[0] if len(rows) == 1 else None


def lemmas_json(query):
    """The lemmas the library's search suggests for what's typed (q): first, where it's a
    word, every lemma Morpheus finds it may be of (λόγου: λόγος, λογόω), the commonest in
    the library first, each with its analyses (parses: "masc gen sg"); then those beginning
    so, whatever the accents, breathings and capitals (λογ, ΛΟΓ: λόγος, λογίζομαι, ...), or
    with words in the library printed so (λογου: λόγος; ειπ: λέγω): the one spelled so
    first, then those with a word printed so, then those with the most words beginning so.
    [{lemma, n, definition, form, parses}], at most limit (Morpheus's all, though), form
    the word printed so where the lemma isn't spelled so; n 0 for a lemma of Morpheus's
    the library hasn't (absent: true)."""
    if not LIBRARY.exists():
        return None
    q = urllib.parse.parse_qs(query)
    word = q.get("q", [""])[0].strip()
    typed = search_spelling(word)
    limit = min(50, max(1, int(q.get("limit", ["12"])[0] or 12)))
    if not typed:
        return []
    span = (typed, typed + "\U0010ffff")
    con = sqlite3.connect(LIBRARY)
    try:
        parsed = {}
        for a in morpheus.analyse(word) if not re.search(r"\s", word) else []:
            if row := index_lemma(con, a["lemma"]):
                lemma, n, definition = row
                x = parsed.setdefault(lemma, {"lemma": lemma, "n": n, "definition": definition,
                                              "form": None, "parses": []})
            else:
                definition = con.execute("SELECT definition FROM lemma_defs WHERE lemma = ?", (a["lemma"],)).fetchone()
                x = parsed.setdefault(a["lemma"], {"lemma": a["lemma"], "n": 0, "definition": definition and definition[0],
                                                   "form": None, "parses": [], "absent": True})
            if search_spelling(a["form"]) != search_spelling(x["lemma"]):
                x["form"] = a["form"]
            x["parses"] += [p for p in a["parses"] if p not in x["parses"]]
        try:
            rows = con.execute(
                "WITH hits (lemma, named, exact, n) AS ("
                "  SELECT id, 1, 2 * (bare = ?), n FROM lemmas WHERE bare >= ? AND bare < ?"
                "  UNION ALL SELECT lemma, 0, bare = ?, n FROM lemma_inflections WHERE bare >= ? AND bare < ?), "
                "best AS (SELECT lemma, max(named) AS named, max(exact) AS exact, "
                "  max(max(CASE WHEN named THEN n ELSE 0 END), total(CASE WHEN named THEN 0 ELSE n END)) AS score "
                "  FROM hits GROUP BY lemma ORDER BY exact DESC, score DESC LIMIT ?) "
                "SELECT l.lemma, l.n, l.definition, CASE WHEN b.named THEN NULL ELSE "
                "  (SELECT form FROM lemma_inflections i WHERE i.lemma = b.lemma AND i.bare >= ? AND i.bare < ? "
                "   ORDER BY i.bare = ? DESC, i.n DESC LIMIT 1) END "
                "FROM best b JOIN lemmas l ON l.id = b.lemma ORDER BY b.exact DESC, b.score DESC, l.lemma",
                (typed, *span, typed, *span, limit + len(parsed), *span, typed)).fetchall()
        except sqlite3.OperationalError:  # a library built before the words as printed: the lemmas only
            rows = con.execute("SELECT lemma, n, definition, NULL FROM lemmas "
                               "WHERE bare >= ? AND bare < ? ORDER BY bare = ? DESC, n DESC, lemma LIMIT ?",
                               (*span, typed, limit + len(parsed))).fetchall()
    except sqlite3.OperationalError:  # a library built before the lemma index (or its search)
        return None
    finally:
        con.close()
    rest = [{"lemma": lemma, "n": n, "definition": definition, "form": form}
            for lemma, n, definition, form in rows if lemma not in parsed]
    return sorted(parsed.values(), key=lambda x: -x["n"]) + rest[:max(0, limit - len(parsed))]


def parsing_json(slug, n, seq, index):
    """A word's card as lexword_json gives it, but each token's lemma as its parsing table
    spells it ("_lemma"), not its references."""
    if not LIBRARY.exists():
        return None
    con = sqlite3.connect(LIBRARY)
    try:
        seg = con.execute("SELECT content FROM segments WHERE text = ? AND div = ? AND seq = ?", (slug, n, seq)).fetchone()
        if not seg or not 0 <= index < len(seg[0].split()):
            return None
        word = seg[0].split()[index]
        row = con.execute("SELECT w.strongs, w.morph, l.lemma, l.gloss, l.definition, w.lemma, w.gloss, d.definition "
                          "FROM words w LEFT JOIN lexicon l ON l.strongs = w.strongs "
                          "LEFT JOIN lemma_defs d ON d.lemma = w.lemma "
                          "WHERE w.text = ? AND w.div = ? AND w.seq = ? AND w.word_index = ?",
                          (slug, n, seq, index)).fetchone()
        parts = [] if row else con.execute(
            f"SELECT t.form, t.lemma, t.postag, t.relation, t.head, d.definition, {treebank_match(con)} FROM treebank t "
            "LEFT JOIN lemma_defs d ON d.lemma = t.lemma "
            "WHERE t.text = ? AND t.div = ? AND t.seq = ? AND t.word_index = ? ORDER BY t.part",
            (slug, n, seq, index)).fetchall()
        gloss = paraphrase_word(con, slug, n, seq, index)
    except sqlite3.OperationalError:  # a library built before texts had parsed words
        return None
    finally:
        con.close()
    # The word in the paraphrase (Gaza's), where the text has one.
    extra = {"gloss": " ".join(EDGE_PUNCT.sub("", w) for w in gloss) or None} if gloss is not None else {}
    if parts:  # a hand-made treebank's analysis
        # (as the treebank spells it where it's several, or matched to its text: the Iliad's)
        return {"word": word, **extra, "tokens": [{
            "form": form if len(parts) > 1 or match else word, "lemma": lemma, "definition": definition,
            **({"match": match} if match else {}),
            "morph": postag_morph(postag), "relation": relation, "role": relation_label(relation),
            "head": head or None, "root": head == "", "_lemma": lemma}
            for form, lemma, postag, relation, head, definition, match in parts]}
    if extra:  # (the Iliad: a word the treebank leaves out)
        return {"word": word, **extra, "tokens": []}
    if not row:
        auto = automatic_parsing(slug, n, seq, index)
        if auto:
            return {"word": word, "tokens": [auto]}
        return {"word": word, "tokens": [], "note": "Not parsed (the parsing doesn't have this word here)."}
    strongs, morph, lemma, gloss, definition, own_lemma, own_gloss, short = row
    indexed = own_lemma or lemma  # (as the lemma index has it)
    if own_lemma:  # the Septuagint: its own lemma; Strong's (and Dodson's entry) only where it's the same word
        if not lemma or letters(lemma) != letters(own_lemma):
            strongs = definition = None
        lemma, gloss, definition = own_lemma, own_gloss, definition or short or own_gloss
    catss = own_lemma is not None
    return {"word": word, "tokens": [{
        "form": word, "lemma": lemma, "strongs": strongs, "gloss": gloss, "definition": definition or gloss,
        "morph": (catss_morph if catss else robinson_morph)(morph) if morph else [], "code": morph,
        "_lemma": indexed}]}


def word_groups(sharer_sets):
    """Join Homeric words (line, word_index) that share any paraphrase word into
    groups (union-find). Returns {line: {word_index: group_id}} for groups of 2+."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for words in sharer_sets:
        first, *rest = words
        for w in rest:
            parent[find(w)] = find(first)
    members = {}
    for w in parent:
        members.setdefault(find(w), []).append(w)
    out = {}
    for gid, ws in enumerate(m for m in members.values() if len(m) > 1):
        for line, wi in ws:
            out.setdefault(line, {})[wi] = gid
    return out


# Reading plans: a text (or a collection: a Testament) read in so many days. It's split
# into that many readings of about the same length (in words), each ending where a chapter
# or book ends if one is near, else at a verse, line or section; a reading a day from the
# day it starts. Worked out here (/api/plans/preview); each device keeps its own plans,
# and its bookmarks, in its browser (shared.js).
VERSE_REF = re.compile(r"\d+(?::\d+)?[a-z]?")  # a ref a selection can name (read.html ?v=)


def plan_target(con, target):
    """(title, [(slug, title, cite, books)], text) of a plan's texts, in order: a category
    ("category:<name>": all its texts), a collection ("collection:<name>"), a text (its slug)
    or some of its books ("<slug>:3-5"; books, (from, to), else None for all); text: the
    title of the one text a plan is of, else None. None if there's no such."""
    shown = listed(con)
    for kind, column in (("category:", "category"), ("collection:", collection_of(con))):
        if target.startswith(kind):
            name = target[len(kind):]
            rows = con.execute(f"SELECT slug, title, cite FROM texts WHERE {column} = ? AND {shown} "
                               "ORDER BY sort", (name,)).fetchall()
            if not rows:
                return None
            # (a category's name is in Greek and English, "Ὅμηρος · Homer": the Greek)
            return name.split(" · ")[0], [(*r, None) for r in rows], rows[0][1] if len(rows) == 1 else None
    m = re.fullmatch(r"([\w-]+)(?::(\d+)(?:-(\d+))?)?", target)
    row = m and con.execute(f"SELECT slug, title, cite FROM texts WHERE slug = ? AND {shown}", (m[1],)).fetchone()
    if not row:
        return None
    if not m[2]:
        return row[1], [(*row, None)], row[1]
    books = (int(m[2]), int(m[3] or m[2]))
    labels = dict(con.execute("SELECT n, label FROM divisions WHERE text = ?", (row[0],)).fetchall())
    if books[0] > books[1] or books[0] not in labels or books[1] not in labels:
        return None
    if len(labels) == 1:
        return row[1], [(*row, None)], row[1]
    name = lambda n: shortLabel(labels[n], n)  # noqa: E731  (Α, or 5)
    span = name(books[0]) if books[0] == books[1] else f"{name(books[0])}–{name(books[1])}"
    return f"{row[1]} {span}", [(*row, books)], row[1]


def plan_units(con, slug, cite):
    """A text's units, in order, that a plan's readings are made of: a verse of the Bible
    (with its additions, 12a, 12b), a line of Homer, a Stephanus section. Each
    {book, first, last (refs), words, open, close}: whether a reading may begin with it (its
    first ref can be named, or it begins its chapter) and end with it."""
    rows = con.execute("SELECT div, ref, content FROM segments WHERE text = ? AND seq > 0 ORDER BY div, seq",
                       (slug,)).fetchall()
    units, section = [], None
    for book, ref, content in rows:
        ref = ref or ""
        if cite == "section":
            ref = section = ref or section or ""
        named = cite != "verse" or bool(VERSE_REF.fullmatch(ref))
        key = re.sub(r"[a-z]$", "", ref) if cite == "verse" else ref
        last = units[-1] if units else None
        if last and last["book"] == book and (last["key"] == key or not named):  # (one that can't be named goes with the one before)
            last.update(last=ref, words=last["words"] + len(content.split()), close=named)
        else:
            units.append({"book": book, "key": key, "first": ref, "last": ref, "words": len(content.split()),
                          "open": named or not last or last["book"] != book, "close": named})
    return units


def split_plan(con, target, days):
    """A plan's readings: [{label, words, parts: [{text, book, v, label}]}], one a day."""
    found = plan_target(con, target)
    if not found:
        return None
    title, texts, one = found
    units = []
    for slug, text_title, cite, books in texts:
        labels = dict(con.execute("SELECT n, label FROM divisions WHERE text = ?", (slug,)).fetchall())
        for u in plan_units(con, slug, cite):
            if not books or books[0] <= u["book"] <= books[1]:
                units.append({**u, "text": slug, "title": text_title, "cite": cite, "label": labels.get(u["book"]),
                              "multi": len(labels) > 1})
    if not units:
        return None
    days = max(1, min(days, len(units)))
    total = sum(u["words"] for u in units)
    before = [0]  # words before each unit
    for u in units:
        before.append(before[-1] + u["words"])
    # Each reading's end: near where its share of the words ends, at the end of a book
    # (or a text) if one is within half a day's reading, else of a chapter within a third.
    share = total / days
    cuts = [0]
    for k in range(1, days):
        ideal, best = k * share, None
        lo = max(cuts[-1] + 1, 1)
        hi = len(units) - (days - k)  # (room for the readings after)
        for i in range(lo, hi + 1):
            if before[i] < ideal - share / 2:
                continue
            if before[i] > ideal + share / 2:
                break
            a, b = units[i - 1], units[i]
            if not (b["open"] and (a["close"] or a["book"] != b["book"] or a["text"] != b["text"])):
                continue
            bonus = 0.5 if a["text"] != b["text"] else 0.3 if a["book"] != b["book"] else 0
            score = abs(before[i] - ideal) / share - bonus
            if best is None or score < best[0]:
                best = (score, i)
        cuts.append(best[1] if best else min(max(lo, round(ideal / share)), hi))
    cuts.append(len(units))
    first_of, last_of = {}, {}  # (text, book) -> index of its first and last unit
    for i, u in enumerate(units):
        first_of.setdefault((u["text"], u["book"]), i)
        last_of[u["text"], u["book"]] = i
    portions = []
    for a, b in zip(cuts, cuts[1:]):
        parts, i = [], a
        names, i = [], a
        while i < b:  # a part: a page of the reader (a chapter of the Bible; a book of Homer; a dialogue)
            u = units[i]
            j = i
            while j + 1 < b and units[j + 1]["text"] == u["text"] and units[j + 1]["book"] == u["book"]:
                j += 1
            parts.append(plan_part(units, i, j, first_of, last_of))
            # The day's name: a text's chapters as one span ("Γένεσις 2:10–4:7"); a text's
            # name once ("Ἰλιάς Α 448–611 · Β 1–299").
            if u["cite"] == "verse" and names and names[-1][0] == u["text"]:
                names[-1][2] = j
            else:
                names.append([u["text"], i, j])
            i = j + 1
        labels = []
        for k, (text, i, j) in enumerate(names):
            if units[i]["cite"] == "verse":
                name = plan_part(units, i, j, first_of, last_of)["label"]
            else:
                name = next(p["label"] for p in parts if p["text"] == text and p["book"] == units[i]["book"])
            if k and names[k - 1][0] == text:
                name = name[len(units[i]["title"]) + 1:]
            labels.append(name)
        label = " · ".join(labels)
        portions.append({"label": label, "words": before[b] - before[a], "parts": parts})
    return {"title": title, "portions": portions, **({"text": one} if one else {})}


def plan_part(units, i, j, first_of, last_of):
    """A reading's part, units[i..j] of one text: where the reader shows it (?v=) and its name."""
    u, w = units[i], units[j]
    whole_from = first_of[u["text"], u["book"]] == i
    whole_to = last_of[w["text"], w["book"]] == j
    if u["cite"] == "verse":  # a selection, across chapters: 3.start-5.12 (start, end: of the chapter)
        v = f"{u['book']}.{'start' if whole_from else u['first']}-{w['book']}.{'end' if whole_to else w['last']}"
        if whole_from and whole_to:
            span = str(u["book"]) if u["book"] == w["book"] else f"{u['book']}–{w['book']}"
        else:
            p = f"{u['book']}:{u['key']}" if VERSE_REF.fullmatch(u["first"]) else str(u["book"])
            q = w["key"] if u["book"] == w["book"] else f"{w['book']}:{w['key']}"
            span = f"{p}–{q}" if (u["book"], u["key"]) != (w["book"], w["key"]) else p
        label = f"{u['title']} {span}"
    else:  # a range in a book: 40-60, 17a-18c
        v = f"{u['first']}-{w['last']}"
        book = shortLabel(u["label"], u["book"]) if u["multi"] else None
        name = " ".join(x for x in (u["title"], book) if x)
        label = name if whole_from and whole_to else f"{name} {u['first']}–{w['last']}"
    return {"text": u["text"], "book": u["book"], "v": v, "label": label}


def shortLabel(label, n):
    """A book's short name, as shared.js has it: the numeral letter of "Ῥαψῳδία Α", else its number."""
    m = re.search(r"\s(\w{1,2})$", label or "")
    return m[1] if m and not m[1].isdigit() else str(n)


def plan_preview(query):
    q = urllib.parse.parse_qs(query)
    try:
        target, days = q["target"][0], int(q["days"][0])
    except (KeyError, ValueError):
        return None
    if not LIBRARY.exists() or not 1 <= days <= 3660:
        return None
    con = sqlite3.connect(LIBRARY)
    split = split_plan(con, target, days)
    con.close()
    return split and {**split, "days": len(split["portions"])}


# Flashcards: the lemmas of part of the library as an Anki deck (scripts/anki.py), a note
# per lemma, in the order first met, whose ids are the lemma's own, so that decks made from
# one text after another add only the lemmas not had before.
VERSE_PIECE = re.compile(r"(?:(\d+)[:.])?(\d+[a-z]*)(?:\s*[-–]\s*(?:(\d+)[:.])?(\d+[a-z]*))?")
SENTENCE_STOP = ".;;··!?"
SENTENCE_REACH = 30  # (words either side of a word, at most, of its sentence on a card)


def ref_key(ref):
    """(number, letters) of a ref ("12a": (12, "a")), or None for one that isn't numbered."""
    m = re.fullmatch(r"(\d+)([a-z]*)", ref or "")
    return (int(m[1]), m[2]) if m else None


def verse_ranges(verses, first_div):
    """[(low, high)] of (div, number, letters), for the verses asked for: "1-50, 60" (in the
    first book chosen), "3:5-4:12" (book:verse), each piece's book the one before's unless
    named. None if they don't read."""
    ranges, div = [], first_div
    for piece in re.split(r"\s*[,;]\s*", verses.strip()):
        m = VERSE_PIECE.fullmatch(piece)
        if not m:
            return None
        div = int(m[1]) if m[1] else div
        a = ref_key(m[2])
        if m[4]:
            end_div = int(m[3]) if m[3] else div
            b = ref_key(m[4])
        else:
            end_div, b = div, a
        ranges.append(((div, *a), (end_div, b[0], b[1] or "~")))  # ("~": 12 takes in 12a, 12b)
        div = end_div
    return ranges


def anki_selection(con, target, verses):
    """(title, [(slug, title, cite, form, labels, [(div, seq, ref)])]): the segments of part of
    the library (a reading plan's target), or of a text's verses there (verses, as
    verse_ranges reads them; a single text's only). None if there's no such, or no verses."""
    found = plan_target(con, target)
    if not found:
        return None
    title, texts, _ = found
    if verses and len(texts) != 1:
        return None
    out = []
    for slug, text_title, cite in ((t[0], t[1], t[2]) for t in texts):
        books = next(t[3] for t in texts if t[0] == slug)
        form = con.execute("SELECT form FROM texts WHERE slug = ?", (slug,)).fetchone()[0]
        labels = dict(con.execute("SELECT n, label FROM divisions WHERE text = ?", (slug,)))
        rows, section = [], None
        for div, seq, ref in con.execute("SELECT div, seq, ref FROM segments WHERE text = ? AND seq > 0 ORDER BY div, seq",
                                         (slug,)):
            if books and not books[0] <= div <= books[1]:
                continue
            if cite == "section":  # (a section's later segments: its ref; before the first, a prologue: 0, as the reader has it)
                if ref_key(ref) or not ref:
                    ref = section = ref or section or "0"
            rows.append((div, seq, ref))
        if verses:
            ranges = cite in ("line", "verse", "section") and verse_ranges(verses, books[0] if books else min(labels))
            if not ranges:
                return None
            rows = [r for r in rows if (k := ref_key(r[2])) and any(lo <= (r[0], *k) <= hi for lo, hi in ranges)]
            if not rows:
                return None
            one_book = books and books[0] == books[1] and ":" not in verses
            title = f"{title}{':' if one_book and cite == 'verse' else ' '}{verses.strip()}"  # (Ἰωάννης 3:1-16, Ἰλιάς Α 1-50)
        out.append((slug, text_title, cite, form, labels, rows))
    return title, out


def place_label(title, cite, labels, div, ref):
    """Where a segment is, as a card names it: Ἰλιάς Α 1, Γένεσις 1:1, Εὐθύφρων 2a."""
    if cite == "verse":
        return f"{title} {ref}" if ":" in (ref or "") else f"{title} {div}:{ref}"
    if cite == "label":
        return f"{title}, {ref}" if ref else title
    if cite == "section" and ref == "0":
        return f"{title}, πρόλογος"
    book = shortLabel(labels.get(div), div) if len(labels) > 1 else None
    return " ".join(x for x in (title, book, ref) if x)


def sentence_of(words, at, prose_breaks):
    """The sentence word at of words ([(seq, index, word)], a division's) is in, as HTML,
    the word in bold: from the stop before it (. ; · ? !, or a prose paragraph's start) to
    the one after, SENTENCE_REACH words either side at most (… where cut)."""
    def ends(k):  # (a sentence ends after word k)
        return words[k][2].rstrip("»”’\"')]⟩᾽").endswith(tuple(SENTENCE_STOP)) \
            or words[k + 1][0] != words[k][0] and words[k + 1][0] in prose_breaks
    a = at
    while a > 0 and at - a < SENTENCE_REACH and not ends(a - 1):
        a -= 1
    b = at
    while b < len(words) - 1 and b - at < SENTENCE_REACH and not ends(b):
        b += 1
    cut_before = a > 0 and not ends(a - 1)
    cut_after = b < len(words) - 1 and not ends(b)
    parts = [f"<b>{html.escape(w, False)}</b>" if k == at else html.escape(w, False)
             for k, (_, _, w) in enumerate(words[a:b + 1], a)]
    return ("… " if cut_before else "") + " ".join(parts) + (" …" if cut_after else "")


def anki_notes(con, selection, pictures, sentences, forms=True):
    """The notes for a selection (anki_selection's): a lemma each, first met first, with its
    definition, where asked its picture (the best deck's; else, for the Iliad, its Gaza
    paraphrase's word's, as the reader shows them) and the sentence and place it's first met
    in, and (forms) a noun's or an adjective's dictionary form for the front (headwords.py: ὁ
    λόγος, -ου; ἀγαθός, -ή, -όν). ([{lemma, fields, tags}], {media name: path}, words in all)."""
    notes, media, seen, total = [], {}, {}, 0
    slugs = [t[0] for t in selection[1]]
    lemma_rows = {}  # (one pass over the lemma index for them all: it has no index by text)
    for row in con.execute(f"SELECT w.text, w.div, w.seq, w.word_index, l.lemma, l.definition FROM lemma_words w "
                           f"JOIN lemmas l ON l.id = w.lemma WHERE w.text IN ({','.join('?' * len(slugs))}) "
                           "ORDER BY w.text, w.div, w.seq, w.word_index, w.part", slugs):
        lemma_rows.setdefault(row[0], []).append(row[1:])
    for slug, title, cite, form, labels, rows in selection[1]:
        first = len(notes)  # (this text's notes: from here)
        chosen = {(div, seq): ref for div, seq, ref in rows}
        divs = sorted({div for div, _, _ in rows})
        segments = {}
        for div, seq, content, para in con.execute("SELECT div, seq, content, para FROM segments WHERE text = ? AND seq > 0 "
                                                   "ORDER BY div, seq", (slug,)):
            if div in divs:
                segments.setdefault(div, []).append((seq, content, para))
        by_word = {}
        for div, seq, index, lemma, definition in lemma_rows.get(slug, []):
            if (div, seq) not in chosen:
                continue
            total += 1
            by_word.setdefault((div, seq, index), []).append(lemma)
            if lemma in seen:
                continue
            seen[lemma] = len(notes)
            fields = {"Lemma": html.escape(lemma), "Definition": html.escape(definition or "")}
            if sentences:
                words = [(s, i, w) for s, content, _ in segments[div] for i, w in enumerate(content.split())]
                at = next((k for k, (s, i, _) in enumerate(words) if s == seq and i == index), None)
                if at is not None:
                    breaks = {s for s, _, para in segments[div] if para} if form == "prose" else set()
                    fields["Sentence"] = sentence_of(words, at, breaks)
                fields["Reference"] = html.escape(place_label(title, cite, labels, div, chosen[div, seq]))
            notes.append({"lemma": lemma, "fields": fields, "tags": [f"acroaterium::{slug}"]})
        if pictures:
            # The Gaza paraphrase's word's pictures, for the Iliad's words without their own.
            borrowed = {}
            if con.execute("SELECT 1 FROM paraphrase_links WHERE text = ? LIMIT 1", (slug,)).fetchone():
                for div in divs:
                    shown = images_json(slug, div)
                    for seq, line in shown["lines"].items():
                        for index, value in enumerate(line):
                            if isinstance(value, list):
                                for lemma in by_word.get((div, seq, index), []):
                                    borrowed.setdefault(lemma, shown["lemmas"][value[0]]["lemma"])
            for note in notes[first:]:
                lemma = note["lemma"]
                file = picture_of(con, lemma) or (lemma in borrowed and picture_of(con, borrowed[lemma]))
                if file and (LEMMA_IMAGES / file).is_file():
                    name = "acroaterium-" + file.replace("/", "-")
                    media[name] = LEMMA_IMAGES / file
                    note["fields"]["Picture"] = f'<img src="{html.escape(name)}">'
    if forms:
        add_forms(con, notes)
    return notes, media, total


def add_forms(con, notes):
    """A noun's or an adjective's dictionary form (ὁ λόγος, -ου; ἀγαθός, -ή, -όν) as notes' Form."""
    kinds = lemma_kinds(con)
    made = headwords.headwords([(n["lemma"], *kinds.get(n["lemma"], ("other", {}))) for n in notes])
    for note in notes:
        if note["lemma"] in made:
            note["fields"]["Form"] = html.escape(made[note["lemma"]])


_kinds = {}  # LIBRARY's mtime -> {lemma: (noun or adj, {gender: words})}


def lemma_kinds(con):
    """{lemma: ("noun" or "adj", {"masc": n, "fem": n, "neut": n})}: the library's nouns and
    adjectives, as most of their words are parsed (the treebanks, the New Testament's
    Robinson, the lemmatiser's), a noun's genders counted, for its article. Made once a run
    (of the library as it is)."""
    stamp = LIBRARY.stat().st_mtime
    if stamp in _kinds:
        return _kinds[stamp]
    pos = {}  # lemma -> Counter of n, a, other
    genders = {}
    names = {"m": "masc", "f": "fem", "n": "neut"}

    def count(lemma, kind, gender=None):
        c = pos.setdefault(lemma, {})
        c[kind] = c.get(kind, 0) + 1
        if kind == "n" and gender:
            g = genders.setdefault(lemma, {})
            g[gender] = g.get(gender, 0) + 1
    try:
        for lemma, postag in con.execute("SELECT lemma, postag FROM treebank WHERE lemma IS NOT NULL AND postag IS NOT NULL"):
            count(re.sub(r"\d+$", "", lemma), postag[0] if postag[0] in "na" else "x", names.get(postag[6:7]))
        for lemma, upos, morph in con.execute("SELECT lemma, pos, morph FROM auto_words WHERE lemma IS NOT NULL"):
            kind = {"NOUN": "n", "ADJ": "a"}.get(upos, "x")
            count(lemma, kind, next((g for g in ("masc", "fem", "neut") if morph and g in morph), None))
        for lemma, morph in con.execute("SELECT coalesce(l.lemma, w.lemma), w.morph FROM words w "
                                        "LEFT JOIN lexicon l ON l.strongs = w.strongs WHERE w.morph IS NOT NULL"):
            if not lemma:
                continue
            m = re.match(r"([NA])[-.]\w\w(\w)?", morph)  # (Robinson's N-NSF, CATSS's N.NSF)
            count(lemma, m[1].lower() if m else "x", names.get((m[2] or "").lower()) if m else None)
    except sqlite3.OperationalError:  # (a library without some of them)
        pass
    out = {}
    for lemma, c in pos.items():
        kind = max(c, key=c.get)
        if kind in "na" and c[kind] * 2 >= sum(c.values()):
            out[lemma] = ("noun" if kind == "n" else "adj", genders.get(lemma, {}))
    _kinds.clear()
    _kinds[stamp] = out
    return out


def picture_of(con, lemma):
    """A lemma's best picture (its file under data/lemma_images/), as the reader shows it first."""
    try:
        row = con.execute("SELECT i.file FROM lemma_images i JOIN image_sources s USING (source) "
                          "WHERE i.lemma = ? AND NOT s.hidden ORDER BY s.rank, i.method != 'lemma', i.note, i.ord LIMIT 1",
                          (lemma,)).fetchone()
    except sqlite3.OperationalError:  # (a library without pictures)
        return None
    return row and row[0]


def anki_parts(selection):
    """What a single text's verses are, for the dialog to name them and say which there are:
    {kind: lines, verses, stephanus or chapters, first, last (book:ref where it's several
    books), multi}; None for a text whose parts aren't numbered (a service's)."""
    if len(selection[1]) != 1:
        return None
    _, _, cite, _, _, rows = selection[1][0]
    keyed = [r for r in rows if ref_key(r[2])]
    if cite not in ("line", "verse", "section") or not keyed:
        return None
    kind = {"line": "lines", "verse": "verses"}.get(cite) \
        or ("stephanus" if any(ref_key(r[2])[1] for r in keyed) else "chapters")  # (Plato's 2a; a Father's 1, 2)
    multi = keyed[0][0] != keyed[-1][0]
    name = lambda r: f"{r[0]}:{r[2]}" if multi else r[2]  # noqa: E731
    return {"kind": kind, "first": name(keyed[0]), "last": name(keyed[-1]), "multi": multi}


def anki_query(query):
    q = urllib.parse.parse_qs(query)
    target = q.get("target", [""])[0]
    verses = q.get("v", [""])[0].strip()
    yes = lambda k: q.get(k, ["0"])[0] == "1"  # noqa: E731
    start = q.get("start", ["0"])[0]
    return target, verses, yes("pictures"), yes("sentences"), int(start) if start.isdigit() else 0


def anki_preview(query):
    """JSON for the export dialog: {title, lemmas, words, pictures, sample: [{lemma,
    definition}], parts (anki_parts)}, what a deck of that part of the library would hold
    ("none": true, verses asked for that aren't there)."""
    target, verses, pictures, *_ = anki_query(query)
    if not LIBRARY.exists() or not target:
        return None
    con = sqlite3.connect(LIBRARY)
    try:
        whole = anki_selection(con, target, "")
        if not whole:
            return None
        parts = anki_parts(whole)
        selection = anki_selection(con, target, verses) if verses else whole
        if not selection:  # (verses that aren't there: what there are, to say so)
            return {"title": whole[0], "parts": parts, "none": True, "lemmas": 0, "words": 0, "pictures": None, "sample": []}
        notes, media, total = anki_notes(con, selection, pictures, False, forms=False)
        add_forms(con, notes[:6])  # (the sample's only: a whole category's take a while the first time)
    finally:
        con.close()
    return {"title": selection[0], "parts": parts, "lemmas": len(notes), "words": total,
            "pictures": sum("Picture" in n["fields"] for n in notes) if pictures else None,
            "sample": [{"lemma": html.unescape(n["fields"].get("Form") or n["fields"]["Lemma"]),
                        "definition": html.unescape(n["fields"]["Definition"])} for n in notes[:6]]}


def anki_package(query):
    """(file name, the .apkg's bytes) of a selection's deck, or None."""
    target, verses, pictures, sentences, start = anki_query(query)
    if not LIBRARY.exists() or not target:
        return None
    con = sqlite3.connect(LIBRARY)
    try:
        selection = anki_selection(con, target, verses)
        if not selection:
            return None
        notes, media, _ = anki_notes(con, selection, pictures, sentences)
    finally:
        con.close()
    if not notes:
        return None
    return f"Acroaterium - {selection[0]}.apkg", anki.package(notes, media, start)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_HEAD(self):
        if self.path == "/api/compact":
            return self.send_compact(head=True)
        super().do_HEAD()

    def send_compact(self, head=False):
        if not LIBRARY_COMPACT.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        version = compact_version()
        extra = {"X-Version": version, "ETag": f'"{version}"', "Cache-Control": "no-cache"}
        if head:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/vnd.sqlite3")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(LIBRARY_COMPACT.stat().st_size))
            for k, v in extra.items():
                self.send_header(k, v)
            return self.end_headers()
        self.send_audio(LIBRARY_COMPACT, extra)

    def do_GET(self):
        if self.path == "/api/compact":
            self.send_compact()
        elif self.path == "/api/compact/sizes":
            self.send_json(recording_sizes())
        elif self.path == "/api/library":
            self.send_json(library_json())
        elif m := re.fullmatch(r"/api/text/([\w-]+)/(\d+)", self.path):
            self.send_json(text_json(m[1], int(m[2])))
        elif m := re.fullmatch(r"/api/notes/([\w-]+)/(\d+)/(\d+)-(\d+)", self.path):
            self.send_json(notes_json(m[1], *map(int, m.groups()[1:])))
        elif m := re.fullmatch(r"/api/book/(\d+)", self.path):
            self.send_json(book_json(int(m[1])))
        elif m := re.fullmatch(r"/api/lexword/([\w-]+)/(\d+)/(\d+)/(\d+)", self.path):
            self.send_json(lexword_json(m[1], *map(int, m.groups()[1:])))
        elif m := re.fullmatch(r"/api/word/(\d+)/(\d+)/(\d+)", self.path):
            self.send_json(word_json(*map(int, m.groups())))
        elif m := re.fullmatch(r"/api/scholia/(\d+)/(\d+)", self.path):
            self.send_json(scholia_json(*map(int, m.groups())))
        elif m := re.fullmatch(r"/api/words/([\w-]+)/(\d+)", self.path):
            self.send_json(words_json(m[1], int(m[2])))
        elif m := re.fullmatch(r"/api/images/([\w-]+)/(\d+)", self.path):
            self.send_json(images_json(m[1], int(m[2])), compress=True)
        elif self.path.startswith("/lemma-images/"):
            # A lemma's picture (images_json), under data/lemma_images/, never above it.
            path = (LEMMA_IMAGES / urllib.parse.unquote(self.path[len("/lemma-images/"):].split("?")[0])).resolve()
            if not path.is_relative_to(LEMMA_IMAGES.resolve()):
                return self.send_error(HTTPStatus.NOT_FOUND)
            self.send_audio(path, {"Cache-Control": "public, max-age=604800"})
        elif m := re.fullmatch(r"/api/offline/([\w-]+)/(\d+)", self.path):
            self.send_json(offline_json(m[1], int(m[2])))
        elif m := re.fullmatch(r"/api/lectionary/(\d{4}-\d\d-\d\d)", self.path):
            self.send_json(lectionary_json(m[1]))
        elif m := re.fullmatch(r"/api/lemmas(?:\?(.*))?", self.path):
            self.send_json(lemmas_json(m[1] or ""))
        elif m := re.fullmatch(r"/api/references/([^/?]+)(?:\?(.*))?", self.path):
            self.send_json(references_json(urllib.parse.unquote(m[1]), m[2] or ""))
        elif self.path.startswith("/api/anki/preview?"):
            self.send_json(anki_preview(self.path.split("?", 1)[1]))
        elif self.path.startswith("/api/anki?"):
            self.send_package(anki_package(self.path.split("?", 1)[1]))
        elif self.path.startswith("/api/plans/preview?"):
            self.send_json(plan_preview(self.path.split("?", 1)[1]))
        elif self.path.startswith("/audio/"):
            # Any file under recordings/ (a library text's recordings stay in their folders),
            # never above it.
            path = (RECORDINGS / urllib.parse.unquote(self.path[len("/audio/"):].split("?")[0])).resolve()
            if not path.is_relative_to(RECORDINGS.resolve()):
                return self.send_error(HTTPStatus.NOT_FOUND)
            self.send_audio(path)
        else:
            super().do_GET()

    def send_json(self, data, compress=False):
        if data is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":") if compress else None).encode()
        compress = compress and "gzip" in self.headers.get("Accept-Encoding", "")
        if compress:
            body = gzip.compress(body, 6)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if compress:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_package(self, found):
        """A file to save (an Anki deck): (name, bytes), or None (404)."""
        if found is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        name, body = found
        plain = name if name.isascii() and '"' not in name else "Acroaterium" + Path(name).suffix  # (for a browser without filename*)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{plain}"; filename*=UTF-8\'\'{urllib.parse.quote(name)}')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_audio(self, path, headers=None):
        """Serve a recording, honouring a single byte Range (browsers seek with these)."""
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        size = path.stat().st_size
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m[1] or m[2]):
            if m[1]:
                start, end = int(m[1]), int(m[2]) if m[2] else size - 1
            else:  # suffix range: last N bytes
                start = max(0, size - int(m[2]))
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                return self.end_headers()
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            try:
                while remaining and (chunk := f.read(min(1 << 16, remaining))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser cancelled the request (normal when seeking)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (0.0.0.0: reachable from other devices, e.g. a phone)")
    ap.add_argument("--show-hidden", action="store_true",
                    help="list the hidden texts too, marked (for working on them locally; never on the live site)")
    args = ap.parse_args()
    global SHOW_HIDDEN
    SHOW_HIDDEN = args.show_hidden
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Reader on http://localhost:{args.port}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
