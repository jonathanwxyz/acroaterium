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
  /api/notes/<slug>/<n>/<a>-<b>  JSON: a library text's notes on its segments a..b (seq),
                       shaped like /api/scholia
  /api/book/<n>        JSON: an Iliad book: title, audio URL and verses (text, paraphrase, timings,
                       and per-word paraphrase equivalents where aligned; Homeric words
                       sharing a paraphrase word, e.g. a verb in tmesis, share a group id;
                       Murray's English per line where split, else as ~5-line passages;
                       the number of scholia and commentary notes per line)
  /api/word/<b>/<l>/<i>  JSON: treebank parse of word i (whitespace chunk) of verse b.l
  /api/lexword/<slug>/<n>/<seq>/<i>  JSON: the same for a library text with parsed words
                       (the New Testament: Robinson's parsing, Dodson's lexicon)
  /api/scholia/<b>/<l>   JSON: the ancient scholia on verse b.l, grouped by manuscript, and
                         the English commentaries (Leaf, Seymour, Benner)
  /audio/<path>        a file under recordings/ (subfolders too), with HTTP Range support for seeking
  /api/bookmarks       JSON: the bookmarked verses (GET); POST one ({text, book, ref, …}) to add
                       it, DELETE /api/bookmarks/<id> to remove it. Kept in data/bookmarks.json
"""
import argparse
import json
import mimetypes
import re
import urllib.parse
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
RECORDINGS = ROOT / "recordings"
DB = ROOT / "data" / "iliad.sqlite"
LIBRARY = ROOT / "data" / "library.sqlite"
LIBRARY_AUDIO = ROOT / "data" / "library_audio.sqlite"
BOOKMARKS = ROOT / "data" / "bookmarks.json"
GREEK_NUMERALS = " ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
MODEL = "mms"

mimetypes.add_type("audio/ogg", ".opus")
mimetypes.add_type("audio/webm", ".webm")
mimetypes.add_type("application/manifest+json", ".webmanifest")

# Punctuation and editorial brackets around a paraphrase word, dropped in glosses.
EDGE_PUNCT = re.compile(r"^[^\w’']+|[^\w’']+$")


def book_json(book):
    con = sqlite3.connect(DB)
    title = con.execute("SELECT title FROM books WHERE book = ?", (book,)).fetchone()
    # One recording per book; prefer the AAC (.m4a) files over older formats.
    rec = con.execute("SELECT recording FROM verse_timings WHERE book = ? AND model = ? "
                      "GROUP BY recording ORDER BY recording LIKE '%.m4a' DESC LIMIT 1",
                      (book, MODEL)).fetchone()
    timing = {line: (start, speech_end, end) for line, start, speech_end, end in con.execute(
        "SELECT line, start, speech_end, end FROM verse_timings WHERE book = ? AND model = ? AND recording = ?",
        (book, MODEL, rec[0] if rec else None))}
    rows = ([(0, title[0], None)] if title else []) + con.execute(
        "SELECT line, homer, paraphrase FROM verses WHERE book = ? ORDER BY line", (book,)).fetchall()
    # Homeric word -> its paraphrase words, keyed by the word's whitespace-chunk index.
    glosses, sharers = {}, {}
    for line, wi, para_line, para_index, word in con.execute(
            "SELECT line, word_index, para_line, para_index, para_word FROM paraphrase_links "
            "WHERE book = ? ORDER BY line, word_index, para_line, para_index", (book,)):
        glosses.setdefault(line, {}).setdefault(wi, []).append(EDGE_PUNCT.sub("", word))
        sharers.setdefault((para_line, para_index), set()).add((line, wi))
    english = dict(con.execute(
        "SELECT line, text FROM translation_lines WHERE translation = 'murray' AND book = ?", (book,)))
    modern = dict(con.execute(
        "SELECT line, text FROM translation_lines WHERE translation = 'polylas' AND book = ?", (book,)))
    # How many scholia and commentary notes each line has (one covering several lines
    # counts for each).
    scholia = {}
    for first, last in con.execute("SELECT line, line_to FROM scholia WHERE book = ? UNION ALL "
                                   "SELECT line, line_to FROM commentaries WHERE book = ?", (book, book)):
        for line in range(first, last + 1):
            scholia[line] = scholia.get(line, 0) + 1
    # Passages only where the lines aren't split yet.
    translation = [{"from": a, "to": b, "text": t} for a, b, t in con.execute(
        "SELECT line_from, line_to, text FROM translation_passages "
        "WHERE translation = 'murray' AND book = ? ORDER BY line_from", (book,))
        if not all(line in english for line in range(a, b + 1))]
    con.close()
    groups = word_groups(sharers.values())
    if not rows:
        return None
    verses = [{"line": line, "text": text, "paraphrase": paraphrase, "english": english.get(line),
               "modern": modern.get(line),
               **({"scholia": scholia[line]} if line in scholia else {}),
               **(dict(zip(("start", "speechEnd", "end"), timing[line])) if line in timing else {}),
               **({"glosses": {i: " ".join(w) for i, w in glosses[line].items()}} if line in glosses else {}),
               **({"groups": groups[line]} if line in groups else {})}
              for line, text, paraphrase in rows]
    return {"book": book, "audio": f"/audio/{urllib.parse.quote(rec[0])}" if rec else None, "verses": verses,
            "translation": translation, "text": "iliad", "title": "Ἰλιάς", "author": "Ὅμηρος",
            "eyebrow": "Ὁμήρου Ἰλιάς", "form": "verse", "cite": "line", "features": ILIAD_FEATURES,
            "books": [{"n": b, "label": f"Ῥαψῳδία {GREEK_NUMERALS[b]}"} for b in range(1, 25)],
            "credits": ILIAD_CREDITS, "notesCredit": ILIAD_NOTES_CREDIT}


# What the Iliad reader has on top of a plain text, and its credits (the scholia panel
# carries its own).
ILIAD_FEATURES = ["audio", "paraphrase", "modern", "translation", "scholia", "words"]
ILIAD_NOTES_CREDIT = ("Scholia: Dindorf & Maass, Scholia Graeca in Homeri Iliadem (1875–88), via "
                      "First1KGreek, CC BY-SA 4.0 · Leaf, Seymour, Benner: via Perseus, CC BY-SA 3.0")
ILIAD_CREDITS = [
    {"id": "translation-credit", "text": "English: A. T. Murray (Loeb, 1924), via Perseus"},
    {"id": "modern-credit", "text": "Μετάφραση: Ἰάκωβος Πολυλάς (1923), via Βικιθήκη"},
]


def collection_of(con, column="collection"):
    """SQL for a column of texts (collection, short), or NULL in a library built before it existed."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(texts)")}
    return column if column in cols else "NULL"


def library_json():
    if not LIBRARY.exists():
        return {"categories": [], "missing": True}
    con = sqlite3.connect(LIBRARY)
    books = {}
    for text, n, label in con.execute("SELECT text, n, label FROM divisions ORDER BY text, n"):
        books.setdefault(text, []).append({"n": n, "label": label})
    texts, collections = {}, {}
    for slug, title, author, category, reader, collection in con.execute(
            f"SELECT slug, title, author, category, reader, {collection_of(con)} FROM texts ORDER BY sort"):
        t = {"slug": slug, "title": title, "author": author, "reader": reader, "books": books.get(slug, [])}
        if collection:  # the texts of a collection (a Testament) go together, where its first one is
            if (category, collection) not in collections:
                collections[category, collection] = {"collection": collection, "title": collection, "texts": []}
                texts.setdefault(category, []).append(collections[category, collection])
            collections[category, collection]["texts"].append(t)
        else:
            texts.setdefault(category, []).append(t)
    cats = [{"name": name, "texts": texts.get(name, [])}
            for (name,) in con.execute("SELECT name FROM categories ORDER BY sort")]
    con.close()
    return {"categories": cats}


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
        span = {"from": ref[first], "to": ref[last]} if ref[first] != ref[last] else {}
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
                    f"{collection_of(con, 'short')} FROM texts WHERE slug = ? AND reader = 'text'", (slug,)).fetchone()
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
    try:  # parsed words (a word card for each): the New Testament
        has_words = con.execute("SELECT 1 FROM words WHERE text = ? LIMIT 1", (slug,)).fetchone()
    except sqlite3.OperationalError:  # a library built before there were any
        has_words = None
    translated, translation_credits = {"en": {}, "el": {}}, {}
    try:  # a translated verse under each verse: English, modern Greek
        for seq, lang, content in con.execute("SELECT seq, lang, content FROM translations WHERE text = ? AND div = ?",
                                              (slug, n)):
            translated.setdefault(lang, {})[seq] = content
        translation_credits = dict(con.execute("SELECT lang, credit FROM translation_credits WHERE text = ?", (slug,)))
    except sqlite3.OperationalError:  # a library built before there were translations
        pass
    english, modern = translated["en"], translated["el"]
    con.close()
    if not rows:
        return None
    title, author, form, cite, credit, notes_credit, collection, short = t
    recording, timed = library_audio(slug, n)
    verses = []
    for seq, ref, content, speaker, para in rows:
        v = {"line": seq, "ref": ref, "text": content,
             **({"speaker": speaker} if speaker else {}), **({"para": True} if para else {}),
             **({"scholia": notes[seq]} if seq in notes else {})}
        if seq in english:
            v["english"] = english[seq]
        if seq in modern:
            v["modern"] = modern[seq]
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
    if english and translation_credits.get("en"):
        credits.append({"text": translation_credits["en"], "id": "translation-credit"})
    if recording and recording[1]:
        credits.append({"text": recording[1]})
    # The chapters of a book of the Bible share its recording: the reader goes on into the
    # next as it plays.
    # The other books of its collection (a Testament): the reader's title is a menu of them.
    con = sqlite3.connect(LIBRARY)
    members = [{"slug": s, "title": tt} for s, tt in con.execute(
        "SELECT slug, title FROM texts WHERE collection = ? ORDER BY sort", (collection,))] if collection else []
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
            "eyebrow": f"{author} · {title}" if author else title, **({"short": short} if short else {}),
            "features": (["scholia"] if has_notes else []) + (["audio"] if recording else [])
                        + (["words"] if has_words else []) + (["translation"] if english else [])
                        + (["modern"] if modern else []),
            "notesCredit": notes_credit, "books": books if len(books) > 1 else [],
            "audio": f"/audio/{urllib.parse.quote(recording[0])}" if recording else None,
            "verses": verses, "translation": [], "credits": credits}


def library_audio(slug, n):
    """A library book's aligned recording, (file under recordings/, credit) or None, and
    its timings: {seq: [(text, {start, speechEnd, end}), ...]} by sentence (one per line
    of verse)."""
    if not LIBRARY_AUDIO.exists():
        return None, {}
    con = sqlite3.connect(LIBRARY_AUDIO)
    try:
        rec = con.execute("SELECT file, credit FROM recordings WHERE text = ? AND div = ?", (slug, n)).fetchone()
        if not rec or not (RECORDINGS / rec[0]).is_file():
            return None, {}
        timed = {}
        for seq, content, start, speech_end, end in con.execute(
                "SELECT seq, content, start, speech_end, end FROM units WHERE text = ? AND div = ? "
                "ORDER BY seq, piece", (slug, n)):
            timed.setdefault(seq, []).append(
                (content, {"start": round(start, 2), "speechEnd": round(speech_end, 2), "end": round(end, 2)}))
        return rec, timed
    finally:
        con.close()


# Sources shown in the scholia panel, in order: (id, siglum, name, language). The
# manuscripts of the ancient scholia (scholia.source), then the English commentaries
# (commentaries.source).
SCHOLIA_SOURCES = [
    ("A", "A", "Venetus A", "grc"), ("A-int", "A", "Venetus A, interlinear", "grc"),
    ("B", "B", "Venetus B", "grc"), ("B-rec", "B", "Venetus B, later hand", "grc"),
    ("T", "T", "Townleianus", "grc"), ("T-rec", "T", "Townleianus, later hand", "grc"),
    ("Leaf", "Leaf", "Commentary on the Iliad (1900)", "en"),
    ("Seymour", "Seymour", "Commentary, Books I–VI (1891)", "en"),
    ("Benner", "Benner", "Selections from the Iliad (1903)", "en"),
]
# "μῆνιν] παρὰ τὸ μένω …": the lemma (the words commented on) and the note.
LEMMA = re.compile(r"([^\]]{1,90})\]\s*(.*)", re.S)


def scholia_json(book, line):
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT line, line_to, source, text FROM scholia "
                       "WHERE book = ? AND line <= ? AND line_to >= ? ORDER BY seq", (book, line, line)).fetchall()
    comm = con.execute("SELECT line, line_to, source, parts FROM commentaries "
                       "WHERE book = ? AND line <= ? AND line_to >= ? ORDER BY seq", (book, line, line)).fetchall()
    con.close()
    notes = {}
    for first, last, source, text in rows:
        m = LEMMA.fullmatch(text)
        notes.setdefault(source, []).append({
            "lemma": m[1].strip() if m else None, "text": m[2] if m else text,
            **({"from": first, "to": last} if first != last else {})})
    for first, last, source, parts in comm:  # styled runs: l = lemma, i = italic, t = text
        notes.setdefault(source, []).append({
            "parts": json.loads(parts), **({"from": first, "to": last} if first != last else {})})
    return {"book": book, "line": line,
            "sources": [{"id": sid, "siglum": siglum, "name": name, "lang": lang, "notes": notes[sid]}
                        for sid, siglum, name, lang in SCHOLIA_SOURCES if sid in notes]}


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


def word_json(book, line, index):
    con = sqlite3.connect(DB)
    verse = con.execute("SELECT homer FROM verses WHERE book = ? AND line = ?", (book, line)).fetchone()
    if not verse or not 0 <= index < len(verse[0].split(" ")):
        return None
    gloss = [w for (w,) in con.execute(
        "SELECT para_word FROM paraphrase_links WHERE book = ? AND line = ? AND word_index = ? "
        "ORDER BY para_line, para_index", (book, line, index))]
    tokens = []
    for (form, lemma, definition, pos, person, number, tense, mood, voice, gender, case, degree,
         relation, head, sentence_id, match) in con.execute(
            "SELECT t.form, t.lemma, d.definition, t.pos, t.person, t.number, t.tense, t.mood, t.voice, "
            "t.gender, t.gram_case, t.degree, t.relation, t.head, t.sentence_id, w.match "
            "FROM word_links w JOIN tokens t ON t.seq = w.token_seq "
            "LEFT JOIN lemma_defs d ON d.lemma = t.lemma "
            "WHERE w.book = ? AND w.line = ? AND w.word_index = ? ORDER BY t.seq", (book, line, index)):
        head_word = con.execute("SELECT form, artificial FROM tokens WHERE sentence_id = ? AND word_id = ?",
                                (sentence_id, head)).fetchone() if head else None
        tokens.append({
            "form": form, "lemma": lemma, "definition": definition, "match": match,
            # Verbs read "3rd singular aorist indicative active", nominals "feminine singular accusative".
            "morph": [m for m in ((pos, person, number, tense, mood, voice, gender, case, degree) if person
                                  else (pos, tense, mood, voice, gender, number, case, degree)) if m],
            "relation": relation, "role": relation_label(relation),
            "head": None if not head else "(implied word)" if head_word and head_word[1] else
                    head_word[0] if head_word else None,
            "root": head == 0,
        })
    con.close()
    return {"word": verse[0].split(" ")[index], "gloss": " ".join(EDGE_PUNCT.sub("", w) for w in gloss) or None,
            "tokens": tokens}


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


def lexword_json(slug, n, seq, index):
    """A library text's word, as /api/word gives an Iliad word: its parsing (the New
    Testament: Robinson's, with Dodson's lexicon) for the reader's card."""
    if not LIBRARY.exists():
        return None
    con = sqlite3.connect(LIBRARY)
    try:
        seg = con.execute("SELECT content FROM segments WHERE text = ? AND div = ? AND seq = ?", (slug, n, seq)).fetchone()
        if not seg or not 0 <= index < len(seg[0].split()):
            return None
        word = seg[0].split()[index]
        row = con.execute("SELECT w.strongs, w.morph, l.lemma, l.gloss, l.definition FROM words w "
                          "LEFT JOIN lexicon l ON l.strongs = w.strongs "
                          "WHERE w.text = ? AND w.div = ? AND w.seq = ? AND w.word_index = ?",
                          (slug, n, seq, index)).fetchone()
    except sqlite3.OperationalError:  # a library built before texts had parsed words
        return None
    finally:
        con.close()
    if not row:
        return {"word": word, "tokens": [], "note": "Not parsed (the parsing doesn't have this word here)."}
    strongs, morph, lemma, gloss, definition = row
    return {"word": word, "tokens": [{
        "form": word, "lemma": lemma, "strongs": strongs, "gloss": gloss,
        "definition": definition or gloss, "morph": robinson_morph(morph) if morph else [], "code": morph}]}


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


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self):
        if self.path == "/api/library":
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
        elif self.path == "/api/bookmarks":
            self.send_json({"bookmarks": load_bookmarks()})
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
        if self.path != "/api/bookmarks":
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(min(length, 1 << 16)))
        except ValueError:
            data = None
        mark = add_bookmark(data)
        if mark is None:
            return self.send_error(HTTPStatus.BAD_REQUEST)
        self.send_json(mark)

    def do_DELETE(self):
        m = re.fullmatch(r"/api/bookmarks/([0-9a-f]+)", self.path)
        if not m or not delete_bookmark(m[1]):
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

    def send_audio(self, path):
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
