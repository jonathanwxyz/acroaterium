#!/usr/bin/env python3
"""Local server for the reader front end (web/).

    python3 scripts/serve.py [--port 8000]   then open http://localhost:8000/
    python3 scripts/serve.py --host 0.0.0.0  also reachable from other devices on the network

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
  /api/bookmarks       JSON: the bookmarked verses (GET); POST one ({text, book, ref, …, plan?}) to add
                       it, DELETE /api/bookmarks/<id> to remove it. Kept in data/bookmarks.json
  /api/plans           JSON: the reading plans (GET), each a text (or a Testament) in so many
                       days, a reading a day; POST {target, days, start} to start one (target: a
                       slug, "<slug>:<from book>-<to book>", "collection:<name>" or
                       "category:<name>"), POST /api/plans/<id> {day, done} to mark a
                       day read (or not), DELETE /api/plans/<id> to end it. Kept in data/plans.json
  /api/plans/preview?target=…&days=…  JSON: the days such a plan would have
  /api/compact         data/library_compact.sqlite (scripts/compact_library.py), the whole
                       library in one file, for an app to keep (with Range support; its
                       version, the start of its SHA-256, in X-Version and ETag; HEAD to ask
                       only that)
  /api/compact/sizes   JSON: {file: bytes} of the recordings there are, as the compact
                       library can't know them (the books' audio and downloads depend on them)
"""
import argparse
import datetime
import hashlib
import xml.etree.ElementTree as ElementTree
import json
import mimetypes
import re
import urllib.parse
import urllib.request
import sqlite3
import threading
import time
import unicodedata
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from import_treebank import POSTAG

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
RECORDINGS = ROOT / "recordings"
LIBRARY = ROOT / "data" / "library.sqlite"
LIBRARY_AUDIO = ROOT / "data" / "library_audio.sqlite"
LIBRARY_COMPACT = ROOT / "data" / "library_compact.sqlite"
BOOKMARKS = ROOT / "data" / "bookmarks.json"
PLANS = ROOT / "data" / "plans.json"

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
    """The compact library's version: the start of its SHA-256 (the same file, built anywhere
    or bundled in the app, has the same version: mobile/android/app/build.gradle)."""
    st = LIBRARY_COMPACT.stat()
    key = (st.st_size, st.st_mtime_ns)
    if key not in _compact_version:
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
    for slug, title, author, category, reader, collection in con.execute(
            f"SELECT slug, title, author, category, reader, {collection_of(con)} FROM texts"
            f" WHERE {collection_of(con, 'hidden')} IS NOT 1 ORDER BY sort"):
        t = {"slug": slug, "title": title, "author": author, "reader": reader, "books": books.get(slug, [])}
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
            req = urllib.request.Request(LECTIONARY.format(d=date), headers={"User-Agent": "Mozilla/5.0 (scrolling reader)"})
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
    translated, translation_credits = {"en": {}, "el": {}, "paraphrase": {}}, {}
    try:  # a translated verse under each verse: English, modern Greek
        for seq, lang, content in con.execute("SELECT seq, lang, content FROM translations WHERE text = ? AND div = ?",
                                              (slug, n)):
            translated.setdefault(lang, {})[seq] = content
        translation_credits = dict(con.execute("SELECT lang, credit FROM translation_credits WHERE text = ?", (slug,)))
    except sqlite3.OperationalError:  # a library built before there were translations
        pass
    try:  # or English in passages of several lines, after the last line of each (Murray's Homer)
        passages = [{"from": a, "to": b, "text": content} for a, b, content in con.execute(
            "SELECT seq_from, seq_to, content FROM translation_passages WHERE text = ? AND div = ? AND lang = 'en' "
            "ORDER BY seq_from", (slug, n))]
    except sqlite3.OperationalError:
        passages = []
    english, modern, paraphrase = translated["en"], translated["el"], translated["paraphrase"]
    try:  # a part's label in English, beside the Greek (the prayers: "cite": label)
        ref_labels = dict(con.execute("SELECT ref, label FROM ref_labels WHERE text = ? AND lang = 'en'", (slug,)))
    except sqlite3.OperationalError:  # a library built before there were any
        ref_labels = {}
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
             **({"scholia": notes[seq]} if seq in notes else {})}
        if seq in english:
            v["english"] = english[seq]
        if seq in modern:
            v["modern"] = modern[seq]
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
                        + (["modern"] if modern else []),
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
        row = con.execute("SELECT lemma, headword, definition, pos, morph, confidence, alternative FROM auto_words "
                          "WHERE text = ? AND div = ? AND seq = ? AND word_index = ?", (slug, n, seq, index)).fetchone()
    except sqlite3.OperationalError:  # a library built before automatic parsing
        return None
    finally:
        con.close()
    if not row:
        return None
    lemma, headword, definition, pos, morph, confidence, alternative = row
    note = "Automatic parsing (may be wrong)."
    other = (alternative or "").rstrip("0123456789")
    if confidence is not None and confidence < 0.6 and other and other != lemma.rstrip("0123456789"):
        note = f"Automatic parsing, uncertain: perhaps {other}."  # (not where it's only which λέγω)
    return {"form": None, "lemma": headword or lemma.rstrip("0123456789"), "definition": definition,
            "morph": morpheus_morph(pos, json.loads(morph or "[]")), "note": note}


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
    own lemmas and glosses) for the reader's card."""
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
            "head": head or None, "root": head == ""}
            for form, lemma, postag, relation, head, definition, match in parts]}
    if extra:  # (the Iliad: a word the treebank leaves out)
        return {"word": word, **extra, "tokens": []}
    if not row:
        auto = automatic_parsing(slug, n, seq, index)
        if auto:
            return {"word": word, "tokens": [auto]}
        return {"word": word, "tokens": [], "note": "Not parsed (the parsing doesn't have this word here)."}
    strongs, morph, lemma, gloss, definition, own_lemma, own_gloss, short = row
    if own_lemma:  # the Septuagint: its own lemma; Strong's (and Dodson's entry) only where it's the same word
        if not lemma or letters(lemma) != letters(own_lemma):
            strongs = definition = None
        lemma, gloss, definition = own_lemma, own_gloss, definition or short or own_gloss
    catss = own_lemma is not None
    return {"word": word, "tokens": [{
        "form": word, "lemma": lemma, "strongs": strongs, "gloss": gloss, "definition": definition or gloss,
        "morph": (catss_morph if catss else robinson_morph)(morph) if morph else [], "code": morph}]}


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


# Bookmarks: verses marked in any text, newest first, in a JSON file (shared by every
# device that uses this server).
BOOKMARK_FIELDS = {"text": str, "book": int, "ref": str, "title": str, "label": str, "snippet": str}
bookmarks_lock = threading.Lock()


def load_bookmarks():
    try:
        return json.loads(BOOKMARKS.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def save_bookmarks(items):
    tmp = BOOKMARKS.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(BOOKMARKS)


def add_bookmark(data):
    """Add a bookmark (or return the one already on that verse); None if data is malformed."""
    if not isinstance(data, dict):
        return None
    mark = {}
    for key, kind in BOOKMARK_FIELDS.items():
        value = data.get(key)
        if not isinstance(value, kind) or (kind is str and len(value) > 300):
            if key in ("text", "book", "ref"):
                return None
            value = None
        mark[key] = value
    # Made in a reading plan's day: which ({id, day, part}), so the library opens it there.
    plan = data.get("plan")
    if (isinstance(plan, dict) and isinstance(plan.get("id"), str) and re.fullmatch(r"[0-9a-f]{1,32}", plan["id"])
            and all(isinstance(plan.get(k), int) for k in ("day", "part"))):
        mark["plan"] = {k: plan[k] for k in ("id", "day", "part")}
    with bookmarks_lock:
        items = load_bookmarks()
        for b in items:
            if (b["text"], b["book"], b["ref"]) == (mark["text"], mark["book"], mark["ref"]):
                return b
        mark["id"] = f"{int(time.time() * 1000):x}"
        mark["created"] = int(time.time())
        items.insert(0, mark)
        save_bookmarks(items)
    return mark


def delete_bookmark(mark_id):
    with bookmarks_lock:
        items = load_bookmarks()
        kept = [b for b in items if b["id"] != mark_id]
        if len(kept) == len(items):
            return False
        save_bookmarks(kept)
    return True


# Reading plans: a text (or a collection: a Testament) read in so many days. It's split
# into that many readings of about the same length (in words), each ending where a chapter
# or book ends if one is near, else at a verse, line or section; a reading a day from the
# day it starts. Kept with the days read, in a JSON file like the bookmarks.
plans_lock = threading.Lock()
VERSE_REF = re.compile(r"\d+(?::\d+)?[a-z]?")  # a ref a selection can name (read.html ?v=)


def plan_target(con, target):
    """(title, [(slug, title, cite, books)], text) of a plan's texts, in order: a category
    ("category:<name>": all its texts), a collection ("collection:<name>"), a text (its slug)
    or some of its books ("<slug>:3-5"; books, (from, to), else None for all); text: the
    title of the one text a plan is of, else None. None if there's no such."""
    hidden = collection_of(con, "hidden")
    for kind, column in (("category:", "category"), ("collection:", collection_of(con))):
        if target.startswith(kind):
            name = target[len(kind):]
            rows = con.execute(f"SELECT slug, title, cite FROM texts WHERE {column} = ? AND {hidden} IS NOT 1 "
                               "ORDER BY sort", (name,)).fetchall()
            if not rows:
                return None
            # (a category's name is in Greek and English, "Ὅμηρος · Homer": the Greek)
            return name.split(" · ")[0], [(*r, None) for r in rows], rows[0][1] if len(rows) == 1 else None
    m = re.fullmatch(r"([\w-]+)(?::(\d+)(?:-(\d+))?)?", target)
    row = m and con.execute(f"SELECT slug, title, cite FROM texts WHERE slug = ? AND {hidden} IS NOT 1", (m[1],)).fetchone()
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


def load_plans():
    try:
        return json.loads(PLANS.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def save_plans(items):
    tmp = PLANS.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PLANS)


def plan_request(data):
    """(target, days, start) from a request, or None."""
    if not isinstance(data, dict):
        return None
    target, days, start = data.get("target"), data.get("days"), data.get("start")
    if not isinstance(target, str) or not isinstance(days, int) or not 1 <= days <= 3660:
        return None
    try:
        datetime.date.fromisoformat(start)
    except (TypeError, ValueError):
        return None
    return target, days, start


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


def add_plan(data):
    request = plan_request(data)
    if not request or not LIBRARY.exists():
        return None
    target, days, start = request
    con = sqlite3.connect(LIBRARY)
    split = split_plan(con, target, days)
    con.close()
    if not split:
        return None
    plan = {"id": f"{int(time.time() * 1000):x}", "target": target, "title": split["title"],
            "start": start, "days": len(split["portions"]), "created": int(time.time()),
            "portions": split["portions"], "done": [], **({"text": split["text"]} if "text" in split else {})}
    with plans_lock:
        items = load_plans()
        items.append(plan)
        save_plans(items)
    return plan


def mark_plan(plan_id, data):
    """Mark a day of a plan read ({day: 1…, done: true}) or not; the plan, or None."""
    if not isinstance(data, dict) or not isinstance(data.get("day"), int):
        return None
    with plans_lock:
        items = load_plans()
        plan = next((p for p in items if p["id"] == plan_id), None)
        if not plan or not 1 <= data["day"] <= plan["days"]:
            return None
        done = set(plan["done"])
        (done.add if data.get("done", True) else done.discard)(data["day"])
        plan["done"] = sorted(done)
        save_plans(items)
    return plan


def delete_plan(plan_id):
    with plans_lock:
        items = load_plans()
        kept = [p for p in items if p["id"] != plan_id]
        if len(kept) == len(items):
            return False
        save_plans(kept)
    return True


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
        elif m := re.fullmatch(r"/api/offline/([\w-]+)/(\d+)", self.path):
            self.send_json(offline_json(m[1], int(m[2])))
        elif m := re.fullmatch(r"/api/lectionary/(\d{4}-\d\d-\d\d)", self.path):
            self.send_json(lectionary_json(m[1]))
        elif self.path == "/api/bookmarks":
            self.send_json({"bookmarks": load_bookmarks()})
        elif self.path == "/api/plans":
            self.send_json({"plans": load_plans()})
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

    def do_POST(self):
        m = re.fullmatch(r"/api/plans/([0-9a-f]+)", self.path)
        if self.path == "/api/bookmarks":
            add = add_bookmark
        elif self.path == "/api/plans":
            add = add_plan
        elif m:
            add = lambda data: mark_plan(m[1], data)  # noqa: E731
        else:
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(min(length, 1 << 16)))
        except ValueError:
            data = None
        item = add(data)
        if item is None:
            return self.send_error(HTTPStatus.BAD_REQUEST)
        self.send_json(item)

    def do_DELETE(self):
        m = re.fullmatch(r"/api/(bookmarks|plans)/([0-9a-f]+)", self.path)
        if not m or not (delete_bookmark if m[1] == "bookmarks" else delete_plan)(m[2]):
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def send_json(self, data):
        if data is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Reader on http://localhost:{args.port}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
