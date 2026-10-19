#!/usr/bin/env python3
"""Build the library (data/library.sqlite): the texts the reader offers, grouped by
category, from a catalogue file.

    python3 scripts/library.py [--catalogue library/catalogue.json] [-v]

The catalogue is local (library/ is git-ignored); library/catalogue.example.json in
the repository shows the format. It is JSON:

    {"categories": ["Epic", "Philosophy"],          -- shown in this order
     "texts": [
       {"slug": "iliad", "title": "Ἰλιάς", "author": "Ὅμηρος", "category": "Epic",
        "reader": "iliad"},                         -- the Iliad, from data/iliad.sqlite (below)
       {"slug": "...", "title": "...", "author": "...", "category": "Epic",
        "credit": "...",                            -- shown under the text
        "label": "Ῥαψῳδία {N}",                     -- optional: name the books ({n}: number,
                                                    -- {N}: Greek numeral letter, Α–Ω)
        "source": {"type": "wikisource", "site": "el.wikisource.org", "index": "<index page>"}},
       {..., "collection": "Καινὴ Διαθήκη"},       -- optional: texts sharing a collection (the
                                                    -- books of a Testament) are listed together,
                                                    -- the collection's books, then their chapters
       {"slug": "apology", "title": "Ἀπολογία Σωκράτους", "author": "Πλάτων",
        "category": "Philosophy", "source": {"type": "perseus", "work": "tlg0059.tlg002"}}],
     "lectionary": {"category": "...", "title": "...",  -- optional: the day's readings (the
       "aliases": {"3 Kings": "3kingdoms"}}}        -- Orthodox lectionary) at the top of a
                                                    -- category, linking to the texts read; a
                                                    -- book whose name doesn't give its slug
                                                    -- (see serve.py) is named in "aliases"

Sources (downloads are cached in data/raw/library/):
  wikisource  a work whose index page lists its books ("# [[/α|Ῥαψωδία α]]"); each
              book's verse is the text of its <poem> blocks, one line per verse,
              numbered by counting (disagreements with the page's {{fr|N}} line markers are
              reported)
  perseus     a Greek text from PerseusDL/canonical-greekLit (work = tlgXXXX.tlgYYY,
              edition default perseus-grc2, else perseus-grc1) cited by Stephanus sections (Plato):
              segments run from one section or speech to the next, with the speaker
              (<label>) and paragraph breaks kept
  byztxt      a book of the New Testament ("book": MT, MR, ..., RE as in
              github.com/byztxt/greektext-antoniades): the Patriarchal edition, accented
              after the Byzantine text; chapters are the divisions, a verse per segment
  rahlfs      a book of the Septuagint ("books": [Gen, JudgB, DanTh, ...], see rahlfs()):
              Rahlfs's text in the CATSS transcription, parsed word by word, punctuated
              after Swete's ("guide": the book's swete "books"); in the Church's edition
              (the Apostoliki Diakonia's) where the recording reads it so
              (library/church_text.json, from scripts/church_text.py)
  swete       a book of the Septuagint ("books": [Swete's abbreviations, Gen, Dat, ...],
              see swete()): Swete's text as transcribed in LXX-Swete-1930; likewise
  prayers     a service or prayers in a local text file ("file"), in parts labelled as
              Stephanus sections are numbered ("cite": label), the labels in Greek and
              English (ref_labels); see prayers()
A text's "fixes" correct slips in its source: [{"ref": "17a", "find": "<as printed>",
"replace": "<as it should read>"}] (the text at that section; it must match once).
A text's "heading" is a title read before the text ("Πλάτωνος Ἀπολογία Σωκράτους"), or a list of lines
(the title, then a dialogue's characters): segments ..., -1, 0 of its first division,
shown (and played) as a title. "chapter_heading" titles each division ("Κεφάλαιον {N}
({nth})": Κεφάλαιον Δʹ (Τέταρτον)), segment 0, after the heading in the first.
A text's "translations" (see translation()) add a translated verse under each verse:
the World English Bible, Vamvas's modern Greek; or passages of several lines (Murray's Homer).
Bible books are verse texts cited by verse ("cite": verse): every verse is numbered.

Notes (shown in the reader's σχόλια panel) come from a text's "notes" list, each
{"type": ..., "id": "Monro", "siglum": "M", "name": "Commentary (1886)", "lang": "en"}
plus where to find them, and "notes_credit" (a line under the panel):
  perseus     "file": a commentary in Perseus's bulk download (as in import_commentaries.py:
              TEI P4, Greek in Beta Code), e.g. "Classics/Homer/opensource/<file>.xml";
              notes keyed by line (<div2 type="commline">, in <div1 type="book">) or by
              Stephanus section (<div1 type="section" n="172A">); a paragraph per note
  scholia     "work": First1KGreek scholia (tlg5026.tlgNNN) set out a book at a time,
              each scholion a paragraph opening with its line number ("5. lemma] …") or
              continuing the line before; the books' hypotheses go with line 1

The Iliad ("reader": "iliad") is built separately, in data/iliad.sqlite (see
data/README.md), and copied in here as a text like the others (iliad_into_library): its
verses, Gaza's paraphrase (translations, lang "paraphrase", and paraphrase_links: which
words of the paraphrase render each Homeric word), Murray's and Polylas's translations,
the scholia and commentaries (notes), the treebank (treebank, with each word's match to
the treebank's text) and short definitions; its timings go into data/library_audio.sqlite
(recordings, units) beside the other texts'.

Tables: categories (name, sort), texts (slug, title, author, category, sort, form:
verse|prose, cite: line|section|verse|label, reader: text, credit, notes_credit, collection, short,
hidden, eyebrow), divisions (text, n, label:
a book, or the whole work), segments (text, div, seq, ref: line number or section,
content, speaker, para: 1 where a paragraph or speech begins), note_sources (text, id,
siglum, name, lang, sort), notes (text, div, pos_from, pos_to: the segments (seq) a note
covers, source, seq, body, parts: JSON styled runs as in import_commentaries.py).
"""
import argparse
import csv
import difflib
import io
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # import_commentaries
DB = ROOT / "data" / "library.sqlite"
ILIAD_DB = ROOT / "data" / "iliad.sqlite"
AUDIO_DB = ROOT / "data" / "library_audio.sqlite"
CACHE = ROOT / "data" / "raw" / "library"
UA = {"User-Agent": "Mozilla/5.0 (scrolling-reader library builder; personal study tool)"}
TEI = "{http://www.tei-c.org/ns/1.0}"
GREEK_NUMERALS = " ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"  # (a work's books lettered Α–Ω, as Homer's)


def greek_number(n):
    """n in Greek numerals, with the keraia: 4 -> Δʹ, 16 -> ΙΣΤʹ (six as ΣΤ, as it's said:
    σίγμα ταυ), 151 -> ΡΝΑʹ."""
    hundreds, tens, units = " ΡΣΤΥΦΧΨΩϠ", " ΙΚΛΜΝΞΟΠϞ", [""] + list("ΑΒΓΔΕ") + ["ΣΤ"] + list("ΖΗΘ")
    return (hundreds[n // 100 % 10] + tens[n // 10 % 10] + units[n % 10]).replace(" ", "") + "ʹ"


# Ordinals, read out in a chapter's title ("Κεφάλαιον Δέκατον Ἕκτον"; a psalm's, masculine):
# (stem, oxytone) for 1-9, 10-90, 100; 11 and 12 are words of their own.
ORDINAL_UNITS = [None, ("πρῶτ", 0), ("δεύτερ", 0), ("τρίτ", 0), ("τέταρτ", 0), ("πέμπτ", 0), ("ἕκτ", 0),
                 ("ἕβδομ", 0), ("ὄγδο", 0), ("ἔνατ", 0)]
ORDINAL_TENS = [None, ("δέκατ", 0), ("εἰκοστ", 1), ("τριακοστ", 1), ("τεσσαρακοστ", 1), ("πεντηκοστ", 1),
                ("ἑξηκοστ", 1), ("ἑβδομηκοστ", 1), ("ὀγδοηκοστ", 1), ("ἐνενηκοστ", 1)]


def ordinal(n, ending="ον"):
    """The ordinal of n, its words capitalised: 21 -> Εἰκοστὸν Πρῶτον (ending "ος": masculine)."""
    parts = [("ἑκατοστ", 1)] if n // 100 == 1 else []
    rest = n % 100
    if rest in (11, 12):
        parts.append(("ἑνδέκατ" if rest == 11 else "δωδέκατ", 0))
    else:
        if rest // 10:
            parts.append(ORDINAL_TENS[rest // 10])
        if rest % 10:
            parts.append(ORDINAL_UNITS[rest % 10])
    words = []
    for k, (stem, oxytone) in enumerate(parts):
        end = ending
        if oxytone:  # its accent on the ending: grave before another word, acute last
            end = ending[0] + ("\u0300" if k + 1 < len(parts) else "\u0301") + ending[1:]
        w = unicodedata.normalize("NFC", stem + end)
        words.append(w[0].upper() + w[1:])
    return " ".join(words)
PERSEUS_RAW = ROOT / "data" / "raw" / "perseus"
PERSEUS_ARCHIVE_URL = "https://www.perseus.tufts.edu/hopper/opensource/downloads/texts/hopper-texts-GreekRoman.tar.gz"
FIRST1K = "https://raw.githubusercontent.com/OpenGreekAndLatin/First1KGreek/master/data/{}/{}/{}.1st1K-grc1.xml"


def get(url, name):
    """url's body, cached as data/raw/library/<name>."""
    path = CACHE / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8")


def safe(name):
    return re.sub(r'[/\\:*?"<>|\s]+', "_", name)


# ---------------------------------------------------------------- Wikisource

def wikitext(site, page):
    url = f"https://{site}/w/index.php?title={urllib.parse.quote(page)}&action=raw"
    return get(url, f"wikisource_{safe(site)}_{safe(page)}.txt")


def clean_wiki(line):
    line = re.sub(r"\{\{[^{}]*\}\}", "", line)                  # templates
    line = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", line)  # [[link|text]] -> text
    line = re.sub(r"<[^>]+>", "", line)                         # tags
    line = line.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", line).strip()


def wikisource(src, verbose):
    site, index = src.get("site", "el.wikisource.org"), src["index"]
    books = re.findall(r"^#\s*\[\[(/[^|\]]+)\|([^\]]+)\]\]", wikitext(site, index), re.M)
    if not books:
        sys.exit(f"wikisource: no '# [[/…|…]]' book list on {index}")
    divisions, segments = [], []
    for n, (sub, label) in enumerate(books, 1):
        text = wikitext(site, index + sub)
        while m := re.fullmatch(r"\s*#(?:REDIRECT|ΑΝΑΚΑΤΕΥΘΥΝΣΗ)\s*\[\[([^\]|]+)[^\]]*\]\].*", text, re.S | re.I):
            text = wikitext(site, m[1])
        divisions.append((n, label.strip()))
        line, mismatches = 0, []
        for poem in re.findall(r"<poem>(.*?)</poem>", text, re.S):
            for raw in poem.splitlines():
                marker = re.search(r"\{\{fr\|(\d+)\}\}", raw)
                content = clean_wiki(raw)
                if not content:
                    continue
                line += 1
                if marker and int(marker[1]) != line:
                    mismatches.append((line, int(marker[1])))  # reported; a stray marker
                    # (some pages have a few) shouldn't renumber the lines after it
                segments.append((n, line, str(line), content, None, 1))
        if verbose or mismatches:
            print(f"  {label.strip()}: {line} lines" +
                  (f"; line markers disagree at (counted, marked) {mismatches}" if mismatches else ""))
    return "verse", "line", divisions, segments


# ---------------------------------------------------------------- the New Testament (byztxt)

# Robinson's text of the Patriarchal edition (Antoniades 1904/1912) has no accents or
# capitals; his Byzantine text (with Pierpont), which it mostly agrees with, has both.
# So each verse's words take the accents and capitals of the same words in the same verse
# of the Byzantine text; a word the Byzantine text doesn't have there, those of the same
# word in eBible's accented Patriarchal text (below); failing that, of the commonest form
# of the word in the Byzantine text; a word in none stays as it is. All public domain
# (github.com/byztxt, ebible.org).
BYZTXT = "https://raw.githubusercontent.com/byztxt/{}/master/{}"
NT_BOOKS = "MT MR LU JOH AC RO 1CO 2CO GA EPH PHP COL 1TH 2TH 1TI 2TI TIT PHM HEB JAS 1PE 2PE 1JO 2JO 3JO JUDE RE".split()
BYZ_NAME = {"MT": "MAT", "MR": "MAR", "LU": "LUK", "AC": "ACT", "RO": "ROM", "GA": "GAL", "JAS": "JAM",
            "JUDE": "JUD", "RE": "REV"}
_byz_forms = None


def letters_key(word):
    """A word without accents, breathings, case or punctuation (ς as σ), for matching."""
    w = unicodedata.normalize("NFD", word.lower())
    return "".join(c for c in w if unicodedata.category(c)[0] == "L").replace("ς", "σ")


def split_word(word):
    """(leading punctuation, the word, trailing punctuation)."""
    m = re.fullmatch(r"([^\w]*)(.*?)([^\w\u0300-\u036f\u1fbd\u1fbf’']*)", word)
    return m[1], m[2], m[3]


def byz_verses(book):
    """{(chapter, verse): [text, ...]} of the Byzantine text. John 7:53-8:11 has two: the
    pericope of the adulteress as in John's file, and as in its own (PA), which is the form
    closer to the Patriarchal edition's."""
    out = {}
    for name in [BYZ_NAME.get(book, book)] + (["PA"] if book == "JOH" else []):
        text = get(BYZTXT.format("byzantine-majority-text", f"csv-unicode/ccat/no-variants/{name}.csv"),
                   f"byztxt/byz-{name}.csv")
        for r in csv.DictReader(io.StringIO(text)):
            out.setdefault((int(r["chapter"]), int(r["verse"])), []).append(r["text"].replace("¶", " ").strip())
    return out


def closest(words, versions):
    """The version (a list of words) sharing most words with words, in order."""
    keys = [letters_key(w) for w in words]
    return max((v.split() for v in versions), default=[], key=lambda v: difflib.SequenceMatcher(
        None, keys, [letters_key(w) for w in v], autojunk=False).ratio())


def byz_forms():
    """{letters_key: the commonest accented form} over the whole Byzantine text."""
    global _byz_forms
    if _byz_forms is None:
        counts = {}
        for book in NT_BOOKS:
            for versions in byz_verses(book).values():
                for w in " ".join(versions).split():
                    core = split_word(w)[1]
                    counts.setdefault(letters_key(core), Counter())[core] += 1
        _byz_forms = {k: c.most_common(1)[0][0] for k, c in counts.items()}
    return _byz_forms


def movable(key):
    """A word's letters without a final movable ν or ς (ἐστιν/ἐστι, οὕτως/οὕτω), to match
    editions that write them differently."""
    return re.sub(r"[νσ]$", "", key)


def as_spelt(form, key):
    """form (accented, from another edition) spelt with this edition's letters key, where the
    two differ only in a final movable ν or ς."""
    have = letters_key(form)
    if have == key:
        return form
    if key.startswith(have):  # ἐστι -> ἐστιν, οὕτω -> οὕτως
        return form + ("ς" if key.endswith("σ") else key[len(have):])
    if have.startswith(key):  # ἐστιν -> ἐστι
        return form[:len(form) - (len(have) - len(key))]
    return form


def accented(words, guide, edition=()):
    """words (unaccented) with the accents and capitals of the guide's words where they are
    the same word; else with the accents of the same word in another edition of the verse
    (the Patriarchal text as eBible has it, allowing for a movable ν or ς); else of the
    word's commonest form. Returns (text, words not found)."""
    keys, gkeys = [letters_key(w) for w in words], [letters_key(w) for w in guide]
    out, missing = list(words), 0
    matched = set()
    for blk in difflib.SequenceMatcher(None, keys, gkeys, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            lead, _, trail = split_word(words[blk.a + k])
            out[blk.a + k] = lead + split_word(guide[blk.b + k])[1] + trail
            matched.add(blk.a + k)
    rest = [i for i in range(len(words)) if i not in matched and keys[i]]
    if rest and edition:
        ekeys = [movable(letters_key(w)) for w in edition]
        for blk in difflib.SequenceMatcher(None, [movable(k) for k in keys], ekeys, autojunk=False).get_matching_blocks():
            for k in range(blk.size):
                i = blk.a + k
                if i in matched or not keys[i]:
                    continue
                lead, _, trail = split_word(words[i])
                form = as_spelt(split_word(edition[blk.b + k])[1], keys[i])
                if trail.strip():  # before a stop: acute, not grave
                    form = acute(form)
                out[i] = lead + form + trail
                matched.add(i)
    forms = {**byz_forms(), **{letters_key(k): v for k, v in ONE_WORD.items()}}  # (keys as looked up: ς as σ)
    for i, w in enumerate(words):
        if i in matched or not keys[i]:
            continue
        lead, _, trail = split_word(w)
        form = forms.get(keys[i])
        if form is None:
            missing += 1
            continue
        if trail.strip():  # before a stop: acute, not grave
            form = acute(form)
        out[i] = lead + form + trail
    return " ".join(out), missing


# Accented by hand: written as one word in the Patriarchal edition, as two in the
# Byzantine text; and a few words of its own.
ONE_WORD = {"διατι": "διατί", "ινατι": "ἱνατί", "αραγε": "ἄραγε", "υπερλιαν": "ὑπερλίαν",
            "υπερεκπερισσου": "ὑπερεκπερισσοῦ", "υπερεκπερισσως": "ὑπερεκπερισσῶς", "τουτεστι": "τουτέστι",
            "τουτεστιν": "τουτέστιν", "καθοτι": "καθότι", "διοτι": "διότι",
            # Readings of the pericope of the adulteress (John 8:4-7) in neither Byzantine form.
            "κατειληπται": "κατείληπται", "αυτοφωρω": "αὐτοφώρῳ", "εκπειραζοντες": "ἐκπειράζοντες",
            "σχωσιν": "σχῶσιν", "ανεκυψεν": "ἀνέκυψεν",
            # Words in neither, and in eBible's text in a neighbouring verse or spelt otherwise.
            "εκλειποντος": "ἐκλείποντος", "μεσσιαν": "Μεσσίαν", "βερενικη": "Βερενίκη",
            "ειδωλολατριαις": "εἰδωλολατρίαις"}

# Slips in Robinson's transcription, corrected after eBible's Patriarchal text before
# the words are accented: (book, chapter, verse) -> {as written: as it should read}.
CORRECTIONS = {
    ("MT", 27, 9): {"τετιμημημενου": "τετιμημενου"},
    ("MR", 3, 2): {"παρετητηρουν": "παρετηρουν"},
    ("LU", 1, 59): {"ζαχαχαριαν": "ζαχαριαν"},
    ("LU", 4, 17): {"επεδοδη": "επεδοθη"},
    ("LU", 22, 31): {"συνιασαι": "σινιασαι"},
    ("1CO", 14, 30): {"αποκακαλυφθη": "αποκαλυφθη"},
    ("1TH", 2, 11): {"εαυτουπαρακαλουντες": "εαυτου παρακαλουντες"},
}


def acute(word):
    """A word's grave accent (only ever on its last syllable) as an acute: before a stop."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", word).replace("\u0300", "\u0301"))


def byztxt(src, verbose, words_out=None):
    """A book of the New Testament; words_out, if given, gets (chapter, seq, word index,
    strong's, morphology code) for each parsed word."""
    book = src["book"]
    text = get(BYZTXT.format("greektext-antoniades", f"textonly/unicode/{book}.txt"), f"byztxt/ant-{book}.txt")
    guide = byz_verses(book)
    # (A verse begun mid-line can carry the dash that preceded its number: "4:2— καιτοιγε".)
    verses = [(c, v, words.lstrip("— ")) for c, v, words in verse_marks(text) if words.strip("— ")]
    chapters = sorted({c for c, _, _ in verses})
    parsed = robinson_parsed(book) if words_out is not None else {}
    segments, missing, seq = [], 0, {}
    for c, v, words in verses:
        words = words.replace("<<", "").replace(">>", "")  # (Robinson's marks round the pericope)
        for wrong, right in CORRECTIONS.get((book, c, v), {}).items():
            words = words.replace(wrong, right)
        words = words.split()
        edition = pt_verses().get((PT_CODE.get(book, book), c, v), "").split()
        text, miss = accented(words, closest(words, guide.get((c, v), [])), edition)
        if words_out is not None:
            words_out += [(c, seq.get(c, 0) + 1, *row) for row in word_rows(text.split(), parsed.get((c, v), []))]
        missing += miss
        seq[c] = seq.get(c, 0) + 1
        segments.append((c, seq[c], str(v), text, None, 1))
    if verbose or missing:
        print(f"  {len(chapters)} chapters, {len(verses)} verses; {missing} words left unaccented")
    return "verse", "verse", [(c, str(c)) for c in chapters], segments


# Robinson's parsing of the Patriarchal text (parsed/<book>.UAN): each word with its
# Strong's number and his morphology code ({V-AAI-3S}), in his ASCII Greek ("biblov").
# The words are those of the text above, in the same order; where a verse's count differs,
# they are matched by their letters. Dodson's lexicon (CC0, keyed by Strong's number)
# gives each number's lemma and English definitions.
ROBINSON_LETTERS = dict(zip("abgdezhqiklmnxoprstufcywv", "αβγδεζηθικλμνξοπρστυφχψωσ"))
DODSON = "https://raw.githubusercontent.com/biblicalhumanities/Dodson-Greek-Lexicon/master/dodson.csv"


def verse_marks(text):
    """[(chapter, verse, text)] of a book in Robinson's files: the text after each "c:v"
    up to the next, wherever it falls (a verse can start mid-line, John 4:2; or be empty,
    Mark 7:16, which the Patriarchal edition leaves out)."""
    marks = list(re.finditer(r"(?<![\d:])(\d+):(\d+)(?![\d:])", text))
    return [(int(m[1]), int(m[2]), text[m.end():marks[k + 1].start() if k + 1 < len(marks) else len(text)].strip())
            for k, m in enumerate(marks)]


def robinson_parsed(book):
    """{(chapter, verse): [(word key, strong's, morphology code)]}."""
    text = get(BYZTXT.format("greektext-antoniades", f"parsed/{book}.UAN"), f"byztxt/parsed-{book}.UAN")
    # (Strong's numbers are words too here: markers are only those followed by a word, so
    # split on the verse marks of the line starts, "     7:17 kai".)
    out = {}
    for c, v, body in verse_marks(re.sub(r"\{[^}]*\}", lambda m: m[0].replace(":", ";"), text)):
        out[(c, v)] = [("".join(ROBINSON_LETTERS.get(ch, "") for ch in w.lower()), int(n), code.replace(";", ":"))
                       for w, n, code in re.findall(r"(\S+)\s+(\d+)(?:\s+\d+)?\s+\{([^}]+)\}", body)]
    return out


def word_rows(words, parsed):
    """(word index, strong's, code) for the words of a verse that the parsing covers."""
    if len(words) == len(parsed):
        return [(i, n, code) for i, (_, n, code) in enumerate(parsed)]
    keys = [letters_key(w) for w in words]
    rows = []
    for blk in difflib.SequenceMatcher(None, keys, [k for k, _, _ in parsed], autojunk=False).get_matching_blocks():
        rows += [(blk.a + k, parsed[blk.b + k][1], parsed[blk.b + k][2]) for k in range(blk.size)]
    return rows


def dodson():
    """[(strong's, lemma, brief definition, longer definition)]."""
    from import_commentaries import beta_to_unicode
    rows = []
    for r in csv.reader(io.StringIO(get(DODSON, "dodson.csv")), delimiter="\t"):
        if r and r[0].strip('"').isdigit():
            lemma = beta_to_unicode(r[2]).split(",")[0].strip()  # "Ἀαρών, ὁ" -> the word itself
            rows.append((int(r[0]), lemma, r[3].strip(), r[4].strip()))
    return rows


# The Patriarchal text accented, as eBible.org has it ("1904 Patriarchal Greek New
# Testament with 20 corrections from later editions", public domain): a second source of
# accents for words the Byzantine text doesn't share.
PT_ZIP = "https://ebible.org/Scriptures/grcbyz_vpl.zip"
PT_CODE = {"MT": "MAT", "MR": "MAR", "LU": "LUK", "AC": "ACT", "RO": "ROM", "GA": "GAL", "PHP": "PHI",
           "JAS": "JAM", "JUDE": "JUD", "RE": "REV"}
_pt = None


def pt_verses():
    """{(book, chapter, verse): text} of eBible's Patriarchal text."""
    global _pt
    if _pt is None:
        path = CACHE / "grcbyz" / "grcbyz_vpl.zip"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(urllib.request.Request(PT_ZIP, headers=UA)) as r:
                path.write_bytes(r.read())
        with zipfile.ZipFile(path) as z:
            text = z.read("grcbyz_vpl.txt").decode("utf-8")
        _pt = {(m[1], int(m[2]), int(m[3])): m[4].strip() for m in re.finditer(r"^(\w+) (\d+):(\d+) (.*)$", text, re.M)}
    return _pt


# ---------------------------------------------------------------- hand-made treebanks

# A text's "treebank": a hand-annotated dependency treebank of it, each word's lemma,
# morphology (the AGDT's 9-letter postag) and syntax (relation, head), for the reader's
# word card:
#   agdt    the Perseus Ancient Greek Dependency Treebank 2.1 ("file": e.g.
#           tlg0012.tlg001.perseus-grc1.tb.xml; CC BY-SA 3.0)
#   gorman  Vanessa Gorman's treebanks ("file": a file in the repository's public/xml; CC0)
# Its words are matched to the text's in order, by their letters (the treebank's edition
# may differ a little); a word it doesn't share gets no card. Books are told apart by
# the sentences' citations (subdoc "1.1-1.2": book 1), for a work in several.
TREEBANKS = {"agdt": "https://raw.githubusercontent.com/PerseusDL/treebank_data/master/v2.1/Greek/texts/{}",
             "gorman": "https://raw.githubusercontent.com/perseids-publications/gorman-trees/master/public/xml/{}"}


def treebank_words(spec):
    """[(book or None, letters key, lemma, postag, relation, head word, form)] in order."""
    if spec["type"] not in TREEBANKS:
        sys.exit(f"unknown treebank type {spec['type']!r}")
    root = ElementTree.fromstring(get(TREEBANKS[spec["type"]].format(spec["file"]),
                                      f"treebank/{spec['type']}-{spec['file']}").encode("utf-8"))
    out = []
    for sent in root.iter("sentence"):
        m = re.match(r"(\d+)\.", sent.get("subdoc") or "")
        book = int(m[1]) if m else None
        words = sent.findall("word")
        # A sentence whose annotation has slipped against its words (a word tagged as
        # punctuation, the lemmas a word out from there on: Euthyphro 3d, "οὐδʼ αὐτὸς πάνυ τι
        # γιγνώσκω…"): left out, its words parsed automatically instead (auto_words).
        if any((w.get("postag") or "").startswith("u") and _GREEK_LETTER.search(w.get("form") or "")
               and not w.get("artificial") for w in words):
            continue
        form_of = {w.get("id"): ("(implied word)" if w.get("artificial") or w.get("insertion_id") else w.get("form"))
                   for w in words}
        for w in words:
            postag = w.get("postag") or ""
            if w.get("artificial") or w.get("insertion_id") or postag.startswith("u"):
                continue  # an implied word, or punctuation
            head = w.get("head")
            out.append((book, tb_key(w.get("form") or ""), unicodedata.normalize("NFC", w.get("lemma") or ""),
                        postag, w.get("relation"), "" if head in (None, "0") else form_of.get(head),
                        unicodedata.normalize("NFC", w.get("form") or "")))
    # Two words each annotated as the other (the Odyssey's "οὐ δέ": οὐ as δέ, δέ as οὐ): put back.
    for k in range(len(out) - 1):
        a, b = out[k], out[k + 1]
        if (_fits(a[6], b[2]) and _fits(b[6], a[2]) and not _fits(a[6], a[2]) and not _fits(b[6], b[2])):
            out[k], out[k + 1] = (*a[:2], *b[2:6], a[6]), (*b[:2], *a[2:6], b[6])
    return out


_GREEK_LETTER = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")


def _fits(form, lemma):
    """A form plausibly of a lemma: their first letters alike (accents aside)."""
    a, b = letters_key(form)[:4], letters_key(lemma.rstrip("0123456789"))[:4]
    return bool(a) and bool(b) and difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def tb_key(word):
    """letters_key without the elision mark (ʼ, a modifier letter), as the treebanks drop it."""
    return letters_key(word).replace("ʼ", "")


def treebank_rows(spec, segments):
    """(div, seq, word index, lemma, postag, relation, head) for the text's words the treebank
    has, matched division by division (a work in several books: by the sentences' book)."""
    tb = treebank_words(spec)
    rows, divs = [], sorted({s[0] for s in segments})
    for div in divs:
        mine = [(seq, i, tb_key(w)) for d, seq, ref, content, *_ in segments if d == div and seq > 0
                for i, w in enumerate(content.split()) if tb_key(w)]
        theirs = [t for t in tb if t[0] in (div, None)] if len(divs) > 1 else tb
        sm = difflib.SequenceMatcher(None, [k for _, _, k in mine], [t[1] for t in theirs], autojunk=False)
        a = b = 0
        for blk in sm.get_matching_blocks():
            rows += gap_rows(div, mine[a:blk.a], theirs[b:blk.b])
            for k in range(blk.size):
                seq, i, _ = mine[blk.a + k]
                rows.append((div, seq, i, 0, *theirs[blk.b + k][2:]))
            a, b = blk.a + blk.size, blk.b + blk.size
    return rows


def gap_rows(div, mine, theirs):
    """Rows for a stretch the plain match left over: mostly words the treebank splits in
    two (οὐδέ as οὐ + δέ, crasis such as τἀληθῆ as τὰ + ἀληθῆ), each part a row."""
    rows, i, j = [], 0, 0
    while i < len(mine) and j < len(theirs):
        seq, w, key = mine[i]
        pair = theirs[j][1] + theirs[j + 1][1] if j + 1 < len(theirs) else None
        if key == theirs[j][1]:
            rows.append((div, seq, w, 0, *theirs[j][2:]))
            i, j = i + 1, j + 1
        elif pair and (key == pair or difflib.SequenceMatcher(None, key, pair).ratio() >= 0.8):
            rows += [(div, seq, w, 0, *theirs[j][2:]), (div, seq, w, 1, *theirs[j + 1][2:])]
            i, j = i + 1, j + 2
        elif j + 1 < len(theirs) and key == theirs[j + 1][1]:
            j += 1
        else:
            i += 1
    return rows


# ---------------------------------------------------------------- translations

# A text's "translations": a list of {"type": ..., "book": ..., "lang": "en"|"el",
# "credit": ...}, each a verse for each verse of the text, by chapter and verse:
#   web     the World English Bible (public domain), eBible.org's verse-per-line edition
#           ("book": MAT, MAR, ...)
#   brenton  L. C. L. Brenton's English Septuagint (1851, public domain), eBible.org's
#           verse-per-line edition ("book": GEN, 1SA (1 Kingdoms), 1KI (3 Kingdoms), DNG,
#           ...), numbered as the Septuagint (the Psalms, Jeremiah's order), see translation()
#   vamvas  Neophytos Vamvas's modern Greek (1850, public domain), CrossWire's GreVamvas as
#           scrollmapper/bible_databases has it in CSV, in monotonic spelling ("book":
#           Matthew, I Corinthians, ...)
#   murray  A. T. Murray's English Homer (Loeb, public domain; Perseus's encoding CC BY-SA
#           4.0) in passages between line numbers ("work": tlg0012.tlg001), see
#           translation_passages()
#   gutenberg  Polylas's modern Greek Homer (public domain) from Project Gutenberg, a line
#           under each line ("ebooks": [the volumes' numbers]), see translation_lines()
# The reader shows English under the "translation" switch, modern Greek under "μετάφραση".
WEB_ZIP = "https://ebible.org/Scriptures/engwebp_vpl.zip"
BRENTON_ZIP = "https://ebible.org/Scriptures/eng-Brenton_vpl.zip"
VAMVAS = "https://raw.githubusercontent.com/scrollmapper/bible_databases/master/formats/csv/GreVamvas.csv"
_vpl = {}
_vamvas = None


def vpl_verses(url, name):
    """{(book, chapter, verse): text} of an eBible.org verse-per-line edition (zipped)."""
    if name not in _vpl:
        path = CACHE / "web" / f"{name}.zip"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
                path.write_bytes(r.read())
        with zipfile.ZipFile(path) as z:
            text = z.read(f"{name}.txt").decode("utf-8")
        _vpl[name] = {(m[1], int(m[2]), int(m[3])): m[4].strip()
                      for m in re.finditer(r"^(\w+) (\d+):(\d+) (.*)$", text, re.M)}
    return _vpl[name]


def web_verses():
    """{(book, chapter, verse): English} of the World English Bible."""
    return vpl_verses(WEB_ZIP, "engwebp_vpl")


def brenton_verses():
    """{(book, chapter, verse): English} of Brenton's Septuagint."""
    return vpl_verses(BRENTON_ZIP, "eng-Brenton_vpl")


def vamvas_verses():
    """{(book, chapter, verse): modern Greek} of Vamvas's translation."""
    global _vamvas
    if _vamvas is None:
        _vamvas = {(r["Book"], int(r["Chapter"]), int(r["Verse"])): r["Text"].strip()
                   for r in csv.DictReader(io.StringIO(get(VAMVAS, "vamvas/GreVamvas.csv")))}
    return _vamvas


def translation(spec, segments):
    """(div, seq, language, text) for the segments a translation has, by chapter and verse:
    a verse "12" of chapter 3 is the translation's 3:12; one numbered for another chapter
    ("30:15", Rahlfs's Proverbs), that chapter's; an addition ("1a") only where its chapter
    has no plain verse of that number (Esther's first: its translation's 1:1 holds it).
    The spec may say otherwise:
      "moved": {"14:24": "16:25", "30:15-33": "24:35"}: verses the translation numbers
        otherwise, one or a run (Vamvas puts the end of Romans at 16:25-27; Brenton's
        Proverbs runs on in chapter 24 through what Rahlfs numbers 30:15-33 and 31:1-9);
      "chapters": {"13": ["SUS", 1]}: a chapter the translation has as a book of its own
        (Brenton's Susanna and Bel, Daniel's 13 and 14 here);
      "refs": {"1:πρ. 1": "1:1", "1:1": null}: a verse of ours ("div:ref") read at another
        place of the translation's, or at none (Brenton's Sirach: the prologue as 1:1)."""
    verses = {"web": web_verses, "vamvas": vamvas_verses, "brenton": brenton_verses}.get(spec["type"])
    if not verses:
        sys.exit(f"unknown translation type {spec['type']!r}")
    verses = verses()
    moved = {}
    for a, b in spec.get("moved", {}).items():
        (ca, va), (cb, vb) = a.split(":"), map(int, b.split(":"))
        first, _, last = va.partition("-")
        for k in range(int(last or first) - int(first) + 1):
            moved[(int(ca), int(first) + k)] = (cb, vb + k)
    chapters = {int(c): tuple(b) for c, b in spec.get("chapters", {}).items()}
    refs = spec.get("refs", {})
    plain = {(div, ref) for div, _, ref, *_ in segments}
    out = []
    for div, seq, ref, *_ in segments:
        if f"{div}:{ref}" in refs:
            target = refs[f"{div}:{ref}"]
            if target is None:
                continue
            c, v = map(int, target.split(":"))
        elif m := re.fullmatch(r"(\d+):(\d+)", ref):
            c, v = int(m[1]), int(m[2])
        elif ref.isdigit():
            c, v = div, int(ref)
        elif (m := re.fullmatch(r"(\d+)a", ref)) and (div, m[1]) not in plain:
            c, v = div, int(m[1])
        else:
            continue
        c, v = moved.get((c, v), (c, v))
        book, c = chapters.get(c, (spec["book"], c))
        if text := verses.get((book, c, v)):  # (WEB lists the verses it omits, empty)
            out.append((div, seq, spec["lang"], text))
    return out


def translation_passages(spec, segments):
    """([(div, first seq, last seq, language, text)], [(div, seq, language, text)]): a
    translation in passages between the Greek line numbers it marks: Murray's Homer in
    Perseus ("type": "murray", "work": tlg0012.tlg001, "edition": perseus-eng3), parsed as
    for the Iliad (import_translation.py, which explains where its markers stand). With
    "cuts" (a file per book, "library/murray_book{:02d}.txt"), the passages of a book that
    has one are split into its lines, as import_translation_lines.py does the Iliad's: the
    second list; the rest stay passages. "markers" corrects a misnumbered line marker
    ({"16.580": 280}: book 16's marker 580 is line 280)."""
    from import_translation import marker_at_line_end, passages
    from import_translation_lines import read_cuts, split
    tlg, wk = spec["work"].split(".")
    name = f"{spec['work']}.{spec.get('edition', 'perseus-eng3')}.xml"
    root = ElementTree.fromstring(get(f"https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/{tlg}/{wk}/{name}",
                                      f"perseus_{name}").encode())
    seq_of = {(div, int(ref)): seq for div, seq, ref, *_ in segments if seq > 0 and ref.isdigit()}
    greek = {(div, int(ref)): content for div, seq, ref, content, *_ in segments if seq > 0 and ref.isdigit()}
    last = {}
    for div, line in seq_of:
        last[div] = max(last.get(div, 0), line)
    out, lines, errors = [], [], []
    for book in root.iter(f"{TEI}div"):
        if book.get("subtype") != "book" or int(book.get("n")) not in last:
            continue
        b = int(book.get("n"))
        for m in book.iter(f"{TEI}milestone"):  # Perseus's slips in its line markers: "markers": {"16.580": 280}
            if m.get("unit") == "line" and f"{b}.{m.get('n')}" in spec.get("markers", {}):
                m.set("n", str(spec["markers"][f"{b}.{m.get('n')}"]))
        found = [(marker, text) for marker, text in passages(book) if text]
        shift = marker_at_line_end(b, found, greek)
        starts = [1 if marker == 1 else marker + shift for marker, _ in found]
        cut_file = ROOT / spec["cuts"].format(b) if spec.get("cuts") else None
        cuts = read_cuts(cut_file) if cut_file and cut_file.exists() else None
        for k, (_, text) in enumerate(found):
            first, end = starts[k], min(starts[k + 1] - 1 if k + 1 < len(found) else last[b], last[b])
            if (b, first) not in seq_of or (b, end) not in seq_of:
                continue
            pieces = split(text, first, end, b, cuts, errors) if cuts is not None else None
            if pieces and "".join("".join(pieces).split()) != "".join(text.split()):
                errors.append(f"{b}.{first}-{end}: pieces don't rejoin to the passage")
                pieces = None
            if pieces:
                lines += [(b, seq_of[(b, n)], spec["lang"], piece) for n, piece in zip(range(first, end + 1), pieces)
                          if piece and (b, n) in seq_of]
            else:
                out.append((b, seq_of[(b, first)], seq_of[(b, end)], spec["lang"], text))
    if errors:
        print("  translation: cut points that don't work:", *errors[:20], sep="\n    ")
    return out, lines


# Polylas's modern Greek Homer (public domain) as Project Gutenberg transcribed
# it (monotonic): a book per "Ραψωδία Χ" heading, every fifth line numbered at its end, a
# line too long for the page carried over onto the next, indented after "[".
GUTENBERG = "https://www.gutenberg.org/cache/epub/{0}/pg{0}.txt"
GREEK_LETTERS = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"


def gutenberg_books(ebooks):
    """{book: [(line, its number or None)]} from the volumes' plain text."""
    books = {}
    for n in ebooks:
        text = get(GUTENBERG.format(n), f"gutenberg/pg{n}.txt")
        text = text.split("*** START", 1)[1].split("*** END", 1)[0].replace("•", "·")  # (its raised stop)
        text = re.split(r"\n\s*(?:ΤΕΛΟΣ|Τ Ε Λ Ο Σ)", text)[0]  # (then the publisher's pages)
        parts = re.split(r"\n(Ραψωδία \S+)\n", text)
        for head, body in zip(parts[1::2], parts[2::2]):
            book = GREEK_LETTERS.index(head.split()[1].replace("I", "Ι")) + 1  # (a Latin I)
            lines = []
            for raw in body.split("\n"):
                if not raw.strip() or re.fullmatch(r"[\sΑ-Ω]+", raw):  # (a running head left in: ΟΜΗΡΟΥ)
                    continue
                if re.match(r"\s+\[", raw) and lines:  # the end of the line before
                    text_, num = lines[-1]
                    lines[-1] = (f"{text_} {raw.strip()[1:]}", num)
                    continue
                m = re.match(r"(.*?)\s+(\d+)$", raw.rstrip())
                lines.append((m[1], int(m[2])) if m else (raw.strip(), None))
            books[book] = lines
    return books


def translation_lines(spec, segments):
    """(div, seq, language, text): a line-for-line translation, a line under each line
    ("type": "gutenberg", "ebooks": [the volumes' numbers]). A book with as many lines as
    the Greek goes line for line; one with more or fewer is matched between its numbered
    lines, the lines of a stretch spread over the Greek's (joined where there are more)."""
    seq_of = {(div, int(ref)): seq for div, seq, ref, *_ in segments if seq > 0 and ref.isdigit()}
    count = {}
    for div, line in seq_of:
        count[div] = max(count.get(div, 0), line)
    out = []
    for book, lines in gutenberg_books(spec["ebooks"]).items():
        if book not in count:
            continue
        n = count[book]
        if len(lines) == n:
            anchors = [(0, 0), (n, n)]
        else:
            anchors = [(0, 0)] + [(i + 1, num) for i, (_, num) in enumerate(lines) if num and 0 < num < n] + [(len(lines), n)]
            # A number out of step with those on either side is a misprint, not a drift.
            off = [i - g for i, g in anchors]
            anchors = [a for k, a in enumerate(anchors) if k in (0, len(anchors) - 1) or off[k] in (off[k - 1], off[k + 1])]
            anchors = [a for k, a in enumerate(anchors) if k == 0 or a[1] > anchors[k - 1][1] and a[0] > anchors[k - 1][0]]
            print(f"  translation: book {book}: {len(lines)} lines for {n}, matched between its line numbers")
        for (i0, g0), (i1, g1) in zip(anchors, anchors[1:]):
            mine = [t for t, _ in lines[i0:i1]]
            for k in range(g1 - g0):  # Greek line g0+k+1 gets its share of the stretch
                share = mine[k * len(mine) // (g1 - g0):(k + 1) * len(mine) // (g1 - g0)]
                if share and (book, g0 + k + 1) in seq_of:
                    out.append((book, seq_of[(book, g0 + k + 1)], spec["lang"], " ".join(share)))
    return out


# ---------------------------------------------------------------- the Old Testament (Swete)

# Swete's Septuagint (1909-1930, public domain) as transcribed in Eliran Wong's
# LXX-Swete-1930 (GPL-3.0): a word list and the first word of each verse. Swete prints
# a book's opening words in capitals (ΕΝ ΑΡΧΗ); they take the commonest accented form of
# the word elsewhere, the first capitalised. His editorial signs go: the substitution
# and interpolation marks (⸂ ⸃ ⸆), the brackets round letters he supplied (the letters
# stay), and alternative verse numbers in brackets ([11]).
SWETE_OPENING = {"αρχη": "ἀρχῇ"}  # Gen 1:1, where the commonest form (ἀρχὴ) is the wrong case
SWETE = "https://raw.githubusercontent.com/eliranwong/LXX-Swete-1930/master/{}"
_swete = None


def swete_verses():
    """{book: [(chapter, verse, text)]} in Swete's order."""
    global _swete
    if _swete is None:
        words = [re.sub(r"[⸂⸃⸆\[\]]", "", re.sub(r"\[\d+\]", "", line.split("\t", 1)[1]))
                 for line in get(SWETE.format("01-Swete_word_with_punctuations.csv"),
                                 "swete/01-Swete_word_with_punctuations.csv").splitlines() if "\t" in line]
        starts = []
        for line in get(SWETE.format("00-Swete_versification.csv"), "swete/00-Swete_versification.csv").splitlines():
            if m := re.fullmatch(r"(\d+)\t(\w+)\.(\d+):(\d+)", line.strip()):
                starts.append((int(m[1]) - 1, m[2], int(m[3]), int(m[4])))
        forms = {}
        for w in words:
            core = split_word(w)[1]
            if core and core != core.upper():
                forms.setdefault(letters_key(core), Counter())[core] += 1
        forms = {k: c.most_common(1)[0][0] for k, c in forms.items()}

        def cased(w, initial):
            lead, core, trail = split_word(w)
            if len(core) > 1 and core == core.upper():
                key = letters_key(core)
                if form := SWETE_OPENING.get(key) or forms.get(key):
                    if trail.strip():  # before a stop: acute
                        form = acute(form)
                    core = form[0].upper() + form[1:] if initial else form
            return lead + core + trail

        def caps(w):
            core = split_word(w)[1]
            return len(core) > 1 and core == core.upper()

        _swete = {}
        for k, (first, book, c, v) in enumerate(starts):
            last = starts[k + 1][0] if k + 1 < len(starts) else len(words)
            span = [w for w in words[first:last] if w]
            text = " ".join(cased(w, i == 0 or not caps(span[i - 1])) for i, w in enumerate(span)).strip()
            if text:
                _swete.setdefault(book, []).append((c, v, text))
    return _swete


def swete(src, verbose):
    """A book of the Septuagint from src["books"]: Swete's book abbreviations (Gen, Dat:
    Daniel in Theodotion's version, ...), each as it is, or {"book": "Sut", "as": 13} to
    make it chapter 13 of the book, or {"book": "Sip", "as": 1, "ref": "πρ. {v}"} to put
    it at the start of chapter 1 with its own verse numbers (Sirach's prologue)."""
    verses = swete_verses()
    chapters, seq, segments = {}, {}, []
    for part in src["books"]:
        part = {"book": part} if isinstance(part, str) else part
        if part["book"] not in verses:
            sys.exit(f"swete: no book {part['book']!r}")
        for c, v, text in verses[part["book"]]:
            c = part.get("as", c)
            chapters.setdefault(c, None)
            seq[c] = seq.get(c, 0) + 1
            segments.append((c, seq[c], part.get("ref", "{v}").format(v=v), text, None, 1))
    # A part placed at a chapter's start ("ref") comes before the chapter's own verses.
    segments.sort(key=lambda s: (s[0], s[1]))
    if verbose:
        print(f"  {len(chapters)} chapters, {len(segments)} verses")
    return "verse", "verse", [(c, str(c)) for c in sorted(chapters)], segments


# Rahlfs's Septuagint (1935; public domain in the UK and EU) in the CATSS transcription
# (CCAT, University of Pennsylvania) as Eliran Wong's LXX-Rahlfs-1935 has it, a word a
# line, with each word's CATSS morphology (James Tauber's corrections), lemma (Open
# Scriptures), Strong's number and English gloss: CC BY-NC-SA 4.0, so non-commercial use
# only. The transcription has no punctuation or capitals; they are taken from Swete's
# text of the same book (the "guide", Swete's parts as for swete()) word for word.
RAHLFS = "https://raw.githubusercontent.com/eliranwong/LXX-Rahlfs-1935/master/{}"
RAHLFS_FILES = {"text": "01_wordlist_unicode/text_accented.csv",
                "verses": "01_wordlist_unicode/alignment_with_OSSP/E-verse.csv",
                "morph": "03a_morphology_with_JTauber_patches/patched_623693.csv",
                "lemma": "02_lexemes/OSSP_lexemes.csv",
                "strongs": "07_StrongNumber/final_Strongs.csv",
                "gloss": "06_English_gloss/beta.csv"}
RAHLFS_CORRECTIONS = {"κύριου": "κυρίου"}  # the transcription's slips
_rahlfs = None


def rahlfs_words():
    """({book: [(chapter or None, verse, first word, end word)]}, [(word, morph, lemma, strongs, gloss)])."""
    global _rahlfs
    if _rahlfs is None:
        cols = {}
        for key, path in RAHLFS_FILES.items():
            lines = get(RAHLFS.format(path), "rahlfs/" + path.rsplit("/", 1)[1]).splitlines()
            cols[key] = [line.split("\t")[-1].strip() for line in lines] if key != "verses" else lines
        words = []
        for w, morph, lemma, strongs, gloss in zip(cols["text"], cols["morph"], cols["lemma"], cols["strongs"], cols["gloss"]):
            w = RAHLFS_CORRECTIONS.get(w, re.sub(r"σ(?=\W*$)", "ς", w))  # and a few final sigmas
            words.append((w, morph or None, unicodedata.normalize("NFC", lemma) or None,
                          int(strongs[1:]) if re.fullmatch(r"G\d+", strongs) else None,
                          re.sub(r";?<br>", "; ", gloss) or None))
        starts = []
        for line in cols["verses"]:
            first, _, ref = line.split("\t")
            book, _, cv = ref.strip("「」").partition(" ")
            c, _, v = cv.rpartition(":")
            starts.append((int(first) - 1, book, c or None, v))
        books = {}
        for k, (first, book, c, v) in enumerate(starts):
            end = starts[k + 1][0] if k + 1 < len(starts) else len(words)
            books.setdefault(book, []).append((c, v, first, end))
        _rahlfs = books, words
    return _rahlfs


def punctuate(words, guide):
    """words (Rahlfs's, bare) with the punctuation and capitals of guide (Swete's), matched
    by their letters."""
    out = list(words)
    a, b = [letters_key(w) for w in words], [letters_key(w) for w in guide]

    def take(i, j, lead=True, trail=True):
        g_lead, g_core, g_trail = split_word(guide[j])
        core = out[i]
        if g_core[:1].isupper() and core[:1].islower():
            core = core[0].upper() + core[1:]
        if trail and g_trail.strip(" ’'"):
            core = acute(core)  # before a stop
        out[i] = (g_lead if lead else "") + core + (g_trail if trail else "")

    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                take(i1 + k, j1 + k)
        elif op == "replace":  # unequal stretches: the stretch's opening and closing marks
            take(i1, j1, trail=False)
            take(i2 - 1, j2 - 1, lead=False)
    return out


def rahlfs(src, verbose, words_out=None):
    """A book of the Septuagint from src["books"]: Rahlfs's book names as in LXX-Rahlfs-1935
    (Gen, JudgB, 2Esdr, DanTh, ...), each as it is or a dict: {"book": "2Esdr", "chapters":
    [11, 23], "shift": -10} (Nehemiah), {"book": "SusTh", "as": 13} (a part as one chapter),
    {"book": "Sir", "chapters": "Prolog", "as": 1, "ref": "πρ. {v}"} (at the start of a
    chapter, with its own verse numbers). src["regroup"]: [[ref, chapter], ...] puts the
    verses from each ref on into that chapter, numbered as "chapter:verse" (Proverbs, whose
    chapters 30-36 Rahlfs numbers by the Hebrew; Swete and the Orthodox editions keep 24-29).
    words_out gets (chapter, seq, word index, strongs, morph, lemma, gloss)."""
    books, words = rahlfs_words()
    regroup = {ref: c for ref, c in src.get("regroup", [])}
    chapters, seq, segments, parsed = {}, {}, [], {}
    for part in src["books"]:
        part = {"book": part} if isinstance(part, str) else part
        if part["book"] not in books:
            sys.exit(f"rahlfs: no book {part['book']!r}")
        want = part.get("chapters")
        moved = None
        for c, v, first, end in books[part["book"]]:
            if isinstance(want, list) and not (c and c.isdigit() and want[0] <= int(c) <= want[1]):
                continue
            if isinstance(want, str) and c != want or want is None and c and not c.isdigit():
                continue
            if f"{c}:{v}" in regroup:
                moved = regroup[f"{c}:{v}"]
            if moved is not None and c and c.isdigit() and int(c) != moved:
                ch, ref = moved, f"{c}:{v}"
            else:
                ch = part.get("as") or (int(c) + part.get("shift", 0) if c else 1)
                ref = part.get("ref", "{v}").format(v=v)
            if not v:  # a book's or psalm's title without a verse number: before what follows
                ref = ""
            chapters.setdefault(ch, None)
            seq[ch] = seq.get(ch, 0) + 1
            segments.append((ch, seq[ch], ref, [w[0] for w in words[first:end]], None, 1))
            parsed[(ch, seq[ch])] = words[first:end]
    segments.sort(key=lambda s: (s[0], s[1]))
    if src.get("guide"):
        _, _, _, guide = swete({"books": src["guide"]}, False)
        flat = [w for s in segments for w in s[3]]
        flat = punctuate(flat, [w for g in guide for w in g[3].split()])
        k, out = 0, []
        for s in segments:
            out.append((*s[:3], " ".join(flat[k:k + len(s[3])]), *s[4:]))
            k += len(s[3])
        segments = out
    else:
        segments = [(*s[:3], " ".join(s[3]), *s[4:]) for s in segments]
    if words_out is not None:
        for (ch, sq), ws in parsed.items():
            words_out += [(ch, sq, i, strongs, morph, lemma, gloss)
                          for i, (_, morph, lemma, strongs, gloss) in enumerate(ws)]
    if verbose:
        print(f"  {len(chapters)} chapters, {len(segments)} verses")
    return "verse", "verse", [(c, str(c)) for c in sorted(chapters)], segments


AUTO_PARSING = ROOT / "data" / "raw" / "lemmatised"


def auto_words(slug, segments, gold):
    """Rows of auto_words: the lemmatiser's parsing (data/raw/lemmatised/<slug>.json, from
    scripts/lemmatise_library.py) of the words without gold parsing, where the segment still
    reads as it was lemmatised."""
    path = AUTO_PARSING / f"{slug}.json"
    if not path.exists():
        return []
    done = json.loads(path.read_text())
    out = []
    for div, seq, ref, content, *_ in segments:
        entry = done.get(f"{div}:{seq}")
        if not entry or entry["text"] != content:
            continue
        for i, r in entry["words"].items():
            if (div, seq, int(i)) in gold:
                continue
            alt = max(r.get("alternatives") or {}, key=(r.get("alternatives") or {}).get, default=None)
            out.append((slug, div, seq, int(i), r["lemma"], r.get("headword"), r.get("definition"), r.get("pos"),
                        json.dumps(r.get("morph") or [], ensure_ascii=False), r.get("confidence"), alt))
    return out


CHURCH_TEXT = ROOT / "library" / "church_text.json"


def church_wording(slug, segments, words, verbose):
    """Rahlfs's verses in the Church's edition, where the recording reads them so
    (library/church_text.json, from scripts/church_text.py: {slug: {"div:ref": verse}}).
    A word changed loses its parsing; the rest keep theirs."""
    if not CHURCH_TEXT.exists():
        return segments, words
    changed = json.loads(CHURCH_TEXT.read_text()).get(slug, {})
    if not changed:
        return segments, words
    plain = lambda w: re.sub(r"[^\w]", "", "".join(c for c in unicodedata.normalize("NFD", w.lower())
                                                   if not unicodedata.combining(c)))
    out, moved = [], {}  # moved: (div, seq) -> {old word index: new}
    for s in segments:
        verse = changed.get(f"{s[0]}:{s[2]}")
        if verse is None:
            out.append(s)
            continue
        old, new = s[3].split(), verse.split()
        sm = difflib.SequenceMatcher(None, [plain(w) for w in old], [plain(w) for w in new], autojunk=False)
        moved[(s[0], s[1])] = {i: j for a, b, n in sm.get_matching_blocks() for i, j in zip(range(a, a + n), range(b, b + n))}
        out.append((*s[:3], verse, *s[4:]))
    kept = [(ch, sq, moved[(ch, sq)][i], *rest) if (ch, sq) in moved else (ch, sq, i, *rest)
            for ch, sq, i, *rest in words if (ch, sq) not in moved or i in moved[(ch, sq)]]
    if verbose:
        print(f"  {len(moved)} verses as read in church")
    return out, kept


# ---------------------------------------------------------------- Perseus (Stephanus prose)

def perseus(src, verbose, title):
    work = src["work"]
    tlg, wk = work.split(".")
    for edition in [src["edition"]] if "edition" in src else ["perseus-grc2", "perseus-grc1"]:
        url = f"https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/data/{tlg}/{wk}/{work}.{edition}.xml"
        try:
            xml = get(url, f"perseus_{work}.{edition}.xml")
            break
        except urllib.error.HTTPError as e:
            if e.code != 404 or "edition" in src:
                raise
    else:
        sys.exit(f"perseus: no Greek edition of {work} found")
    body = ElementTree.fromstring(xml.encode()).find(f".//{TEI}body")
    segments = []
    state = {"ref": None, "speaker": None, "para": 1, "buf": []}

    def flush():
        content = re.sub(r"\s+", " ", "".join(state["buf"])).strip()
        if content:
            segments.append((1, len(segments) + 1, state["ref"], content, state["speaker"], state["para"]))
            state["speaker"], state["para"] = None, 0
        state["buf"] = []

    def walk(el):
        tag = el.tag.replace(TEI, "")
        if tag == "milestone":
            unit = el.get("unit")
            if unit == "section":
                flush()
                state["ref"] = el.get("n")
            elif unit == "para":
                flush()
                state["para"] = 1
        elif tag == "label":  # speaker: starts a new speech
            if state.pop("merge", False):  # the same speech, carried over a page division
                return
            flush()
            state["speaker"] = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
            state["para"] = 1
        elif tag in ("del", "note", "bibl", "gap", "head"):
            pass
        else:
            if tag == "p":
                flush()
                said = el.find(f"{TEI}said")
                if said is not None and said.get("rend") == "merge":
                    state["merge"] = True  # continues the speech before: no new paragraph
                else:
                    state["para"] = 1
            if el.text:
                state["buf"].append(el.text)
            for child in el:
                walk(child)
                if child.tail:
                    state["buf"].append(child.tail)
            if tag == "l":
                state["buf"].append(" ")

    for child in body:
        walk(child)
    flush()
    if verbose:
        refs = [s[2] for s in segments]
        print(f"  {len(segments)} segments, sections {refs[0]}–{refs[-1]}")
    return "prose", "section", [(1, title)], segments


# ---------------------------------------------------------------- prayers (local text files)

# A heading set in capitals ("ΕΥΧΗ ΤΟΥ ΕΥΑΓΓΕΛΙΟΥ", "ΜΕΓΑΛΗ ΣΥΝΑΠΤΗ ἢ ΕΙΡΗΝΙΚΑ"): two or more
# words in capitals, any others short ones (ἢ, ἡ).
def capitals_heading(line):
    words = [w for w in line.split() if any(c.isalpha() for c in w)]
    caps = [w for w in words if not any(c.islower() for c in w) and sum(c.isalpha() for c in w) >= 2]
    return len(caps) >= 2 and all(w in caps or sum(c.isalpha() for c in w) <= 2 for w in words)


def prayers(src, verbose, title, labels_out):
    """A service or prayers from a local text file ("file", under the project): "# name"
    (its title, given by the catalogue instead), then its parts, each opened by "## id",
    a heading in capitals, or a line starting as one of "starts" says. A part's label (the
    segments' ref, as a Stephanus section) is Greek; its English goes in labels_out
    ((ref, "en", label)). "labels": {"id or heading": [Greek, English]} (or a list of those,
    one for each time it comes); "": the text before the first part; psalm_N needs none
    (Ψαλμὸς Νʹ, Psalm 50). "starts": [[the line's start, Greek, English], ...] in the
    text's order, for parts the file doesn't mark. "roles": who speaks a line ("Διάκονος·
    ...", also with "Ὁ " and a parenthesis: "Ἱερεύς (χαμηλοφώνως)·"), its speaker. A
    blank line ends a paragraph; the lines of one are kept as lines (para 2: a line break).
    The text is taken in Unicode's composed form (NFC), and so are the markers."""
    path = ROOT / src["file"]
    nfc = lambda x: unicodedata.normalize("NFC", x)  # (the ano teleia is a middle dot, oxia tonos)
    labels = {nfc(k): v for k, v in src.get("labels", {}).items()}
    starts = [[nfc(at), el, en] for at, el, en in src.get("starts", [])]
    roles = [nfc(r) for r in src.get("roles", [])]
    role = re.compile(rf"^((?:Ὁ )?(?:{'|'.join(map(re.escape, roles))})(?: \([^)]*\))?)·\s*(.*)$") if roles else None
    seen, used, missing = Counter(), Counter(), []
    segments, ref, new_para = [], None, True

    def part(marker):
        nonlocal ref, new_para
        k = seen[marker]
        seen[marker] += 1
        given = labels.get(marker)
        if given and isinstance(given[0], list):
            given = given[k] if k < len(given) else None
        if not given and (m := re.fullmatch(r"psalm_(\d+)", marker)):
            given = [f"Ψαλμὸς {greek_number(int(m[1]))}", f"Psalm {m[1]}"]
        if not given:
            missing.append(marker if k == 0 else f"{marker} (time {k + 1})")
            given = [marker, marker]
        el, en = given
        used[el] += 1
        if used[el] > 1:  # (the same again: Μικρὰ Συναπτή (Βʹ))
            el, en = f"{el} ({greek_number(used[el])})", f"{en} ({used[el]})"
        ref, new_para = el, True
        labels_out.append((ref, "en", en))

    for line in path.read_text(encoding="utf-8").splitlines():
        line = nfc(line.strip())
        if re.match(r"#(?!#)", line):
            continue
        if line.startswith("##"):
            part(line.lstrip("#").strip())
            continue
        if not line:
            new_para = True
            continue
        if capitals_heading(line):
            part(line)
            continue
        if starts and line.startswith(starts[0][0]):
            _, el, en = starts.pop(0)
            labels[f"start:{el}"] = [el, en]
            part(f"start:{el}")
        if ref is None:
            part("")
        speaker, content = None, line
        if role and (m := role.match(line)):
            speaker, content = m[1], m[2]
        if not content:
            continue
        segments.append((1, len(segments) + 1, ref, content, speaker, 1 if new_para else 2))
        new_para = False
    if missing:
        sys.exit(f"{src['file']}: no label for " + ", ".join(repr(m) for m in missing))
    if starts:
        sys.exit(f"{src['file']}: not found (in order): " + ", ".join(repr(s[0]) for s in starts))
    if verbose:
        print(f"  {len({s[2] for s in segments})} parts, {len(segments)} lines")
    return "prose", "label", [(1, title)], segments


# ---------------------------------------------------------------- the Iliad

def iliad(verbose):
    """The Iliad's books (data/iliad.sqlite); its contents are copied in after the build
    (iliad_into_library)."""
    if not ILIAD_DB.exists():
        print("  (data/iliad.sqlite not built: no books listed)")
        return "verse", "line", [], []
    con = sqlite3.connect(ILIAD_DB)
    books = [b for (b,) in con.execute("SELECT DISTINCT book FROM verses ORDER BY book")]
    con.close()
    return "verse", "line", [(b, f"Ῥαψῳδία {GREEK_NUMERALS[b]}") for b in books], []


ILIAD_NOTES_CREDIT = ("Scholia: Dindorf & Maass, Scholia Graeca in Homeri Iliadem (1875–88), via "
                      "First1KGreek, CC BY-SA 4.0 · Leaf, Seymour, Benner: via Perseus, CC BY-SA 3.0")
ILIAD_TRANSLATION_CREDITS = {"en": "English: A. T. Murray (Loeb, 1924), via Perseus",
                             "el": "Μετάφραση: Ἰάκωβος Πολυλάς (1923), via Βικιθήκη"}
# Sources of the notes, in the order shown: the manuscripts of the ancient scholia
# (scholia.source), then the English commentaries (commentaries.source).
ILIAD_NOTE_SOURCES = [
    ("A", "A", "Venetus A", "grc"), ("A-int", "A", "Venetus A, interlinear", "grc"),
    ("B", "B", "Venetus B", "grc"), ("B-rec", "B", "Venetus B, later hand", "grc"),
    ("T", "T", "Townleianus", "grc"), ("T-rec", "T", "Townleianus, later hand", "grc"),
    ("Leaf", "Leaf", "Commentary on the Iliad (1900)", "en"),
    ("Seymour", "Seymour", "Commentary, Books I–VI (1891)", "en"),
    ("Benner", "Benner", "Selections from the Iliad (1903)", "en"),
]
ILIAD_RECORDING_CREDIT = "Recording: Project Eustathios"
ILIAD_MODEL = "mms"  # the aligner whose timings the reader uses

# Tables (and columns) the Iliad brings beyond the other texts': added to a library built
# before them, so the Iliad can be copied into it without a rebuild.
ILIAD_SCHEMA = """
    CREATE TABLE IF NOT EXISTS paraphrase_links (  -- the words of a paraphrase (translations,
        text       TEXT NOT NULL,                 --   lang "paraphrase") rendering each word
        div        INTEGER NOT NULL,              --   of the text: Gaza's of the Iliad
        seq        INTEGER NOT NULL,
        word_index INTEGER NOT NULL,              -- 0-based whitespace chunk of the segment
        para_seq   INTEGER NOT NULL,              -- the paraphrase's segment (usually seq)
        para_index INTEGER NOT NULL,              -- 0-based whitespace chunk of its paraphrase
        para_word  TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS paraphrase_links_place ON paraphrase_links (text, div, seq);
"""


# data/library_audio.sqlite's tables, as scripts/align_library.py makes them.
AUDIO_SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    text   TEXT NOT NULL,
    div    INTEGER NOT NULL,
    file   TEXT NOT NULL,     -- under recordings/
    credit TEXT,
    frames INTEGER,           -- length of the aligned audio, 20 ms frames
    PRIMARY KEY (text, div));
CREATE TABLE IF NOT EXISTS units (
    text       TEXT NOT NULL,
    div        INTEGER NOT NULL,
    seq        INTEGER NOT NULL,   -- segments.seq in library.sqlite
    piece      INTEGER NOT NULL,   -- sentence within the segment (0 for verse)
    content    TEXT NOT NULL,
    start      REAL NOT NULL,
    speech_end REAL NOT NULL,
    end        REAL NOT NULL,
    score      REAL,
    cut        INTEGER,            -- 1: the gap after it is speech that isn't text (an
                                   -- announcement), to be skipped at once, not as a pause
    PRIMARY KEY (text, div, seq, piece));
"""


def upgrade_schema(con):
    """Add the Iliad's columns and tables to a library built before them."""
    con.executescript(ILIAD_SCHEMA)
    for table, column in (("texts", "eyebrow TEXT"), ("treebank", "match TEXT")):
        if column.split()[0] not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column}")


def iliad_into_library(con, slug="iliad", audio_db=AUDIO_DB, verbose=False):
    """Copy the Iliad from data/iliad.sqlite into the library (con) as the text slug, in the
    tables every text uses, replacing what it had there; and its timings (the mms aligner's,
    a recording per book, the AAC one where there are several) into audio_db."""
    upgrade_schema(con)
    il = sqlite3.connect(ILIAD_DB)
    for table in ("segments", "translations", "translation_passages", "translation_credits", "note_sources",
                  "notes", "treebank", "paraphrase_links"):
        con.execute(f"DELETE FROM {table} WHERE text = ?", (slug,))
    books = dict(il.execute("SELECT book, title FROM books"))
    con.execute("DELETE FROM divisions WHERE text = ?", (slug,))
    con.executemany("INSERT INTO divisions VALUES (?, ?, ?)",
                    [(slug, b, f"Ῥαψῳδία {GREEK_NUMERALS[b]}")
                     for (b,) in il.execute("SELECT DISTINCT book FROM verses ORDER BY book")])
    con.execute("UPDATE texts SET reader = 'text', form = 'verse', cite = 'line', notes_credit = ? "
                "WHERE slug = ?", (ILIAD_NOTES_CREDIT, slug))

    # The verses (line 0: the book's title, as the recording reads it), and Gaza's paraphrase.
    verses = il.execute("SELECT book, line, homer, paraphrase FROM verses ORDER BY book, line").fetchall()
    con.executemany("INSERT INTO segments VALUES (?, ?, 0, '', ?, NULL, 1)",
                    [(slug, b, title) for b, title in books.items() if title])
    con.executemany("INSERT INTO segments VALUES (?, ?, ?, ?, ?, NULL, 1)",
                    [(slug, b, line, str(line), homer) for b, line, homer, _ in verses])
    con.executemany("INSERT INTO translations VALUES (?, ?, ?, 'paraphrase', ?)",
                    [(slug, b, line, para) for b, line, _, para in verses if para])
    con.executemany("INSERT INTO paraphrase_links VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(slug, *row) for row in il.execute(
                        "SELECT book, line, word_index, para_line, para_index, para_word FROM paraphrase_links "
                        "ORDER BY book, line, word_index, para_line, para_index")])

    # Translations: Murray's English line by line where it's been split, else in passages
    # (only those not split); Polylas's modern Greek line by line.
    split = set(il.execute("SELECT book, line FROM translation_lines WHERE translation = 'murray'"))
    con.executemany("INSERT INTO translations VALUES (?, ?, ?, ?, ?)",
                    [(slug, b, line, {"murray": "en", "polylas": "el"}[tr], text) for tr, b, line, text in il.execute(
                        "SELECT translation, book, line, text FROM translation_lines "
                        "WHERE translation IN ('murray', 'polylas') AND text IS NOT NULL")])
    con.executemany("INSERT INTO translation_passages VALUES (?, ?, ?, ?, 'en', ?)",
                    [(slug, b, a, z, text) for b, a, z, text in il.execute(
                        "SELECT book, line_from, line_to, text FROM translation_passages WHERE translation = 'murray' "
                        "ORDER BY book, line_from")
                     if not all((b, line) in split for line in range(a, z + 1))])
    con.executemany("INSERT INTO translation_credits VALUES (?, ?, ?)",
                    [(slug, lang, credit) for lang, credit in ILIAD_TRANSLATION_CREDITS.items()])

    # Notes: the scholia, then the commentaries, each in its own order.
    con.executemany("INSERT INTO note_sources VALUES (?, ?, ?, ?, ?, ?)",
                    [(slug, *src, k) for k, src in enumerate(ILIAD_NOTE_SOURCES)])
    rows = [(b, a, z, src, text, None) for b, a, z, src, text in il.execute(
        "SELECT book, line, line_to, source, text FROM scholia ORDER BY seq")]
    rows += il.execute("SELECT book, line, line_to, source, text, parts FROM commentaries ORDER BY seq").fetchall()
    con.executemany("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(slug, b, a, z, src, k, text, parts) for k, (b, a, z, src, text, parts) in enumerate(rows)])

    # The treebank, word by word as the verses split (a word may be several of its
    # tokens: μηδέ, μη + δέ), with how each matched the treebank's text; the head of
    # each token as a word ('' for the sentence's root).
    form_of = {(sid, wid): "(implied word)" if artificial else form for sid, wid, form, artificial in il.execute(
        "SELECT sentence_id, word_id, form, artificial FROM tokens")}
    tb, part = [], {}
    for b, line, wi, form, lemma, postag, relation, head, sid, match in il.execute(
            "SELECT w.book, w.line, w.word_index, t.form, t.lemma, t.postag, t.relation, t.head, t.sentence_id, "
            "w.match FROM word_links w JOIN tokens t ON t.seq = w.token_seq ORDER BY w.book, w.line, w.word_index, t.seq"):
        k = part[b, line, wi] = part.get((b, line, wi), -1) + 1
        tb.append((slug, b, line, wi, k, lemma, postag, relation,
                   None if head is None else "" if head == 0 else form_of.get((sid, head)), form, match))
    con.executemany("INSERT INTO treebank (text, div, seq, word_index, part, lemma, postag, relation, head, form, match) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tb)
    con.executemany("INSERT OR IGNORE INTO lemma_defs VALUES (?, ?)", il.execute("SELECT lemma, definition FROM lemma_defs"))

    # The timings, a recording per book.
    timed = 0
    audio = sqlite3.connect(audio_db)
    try:
        audio.executescript(AUDIO_SCHEMA)
        audio.execute("DELETE FROM units WHERE text = ?", (slug,))
        audio.execute("DELETE FROM recordings WHERE text = ?", (slug,))
        content = {(b, line): homer for b, line, homer, _ in verses} | {(b, 0): t for b, t in books.items() if t}
        for (b,) in il.execute("SELECT DISTINCT book FROM verse_timings WHERE model = ? ORDER BY book", (ILIAD_MODEL,)).fetchall():
            (rec,) = il.execute("SELECT recording FROM verse_timings WHERE book = ? AND model = ? GROUP BY recording "
                                "ORDER BY recording LIKE '%.m4a' DESC LIMIT 1", (b, ILIAD_MODEL)).fetchone()
            audio.execute("INSERT INTO recordings (text, div, file, credit) VALUES (?, ?, ?, ?)",
                          (slug, b, rec, ILIAD_RECORDING_CREDIT))
            units = [(slug, b, line, 0, content.get((b, line), ""), start, speech_end, end, score, 0)
                     for line, start, speech_end, end, score in il.execute(
                         "SELECT line, start, speech_end, end, score FROM verse_timings "
                         "WHERE book = ? AND model = ? AND recording = ?", (b, ILIAD_MODEL, rec))]
            audio.executemany("INSERT INTO units (text, div, seq, piece, content, start, speech_end, end, score, cut) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", units)
            timed += len(units)
        audio.commit()
    finally:
        audio.close()
    il.close()
    print(f"  the Iliad: {len(verses)} lines, {len(rows)} notes, {len(tb)} treebank words, {timed} timed lines")


# ---------------------------------------------------------------- notes

def perseus_members(members):
    """Make sure each Perseus bulk-download member is in data/raw/perseus/ (fetching the
    125 MB archive once if any is missing, and deleting it afterwards)."""
    missing = [m for m in members if not (PERSEUS_RAW / Path(m).name).exists()]
    if not missing:
        return
    import tarfile
    PERSEUS_RAW.mkdir(parents=True, exist_ok=True)
    archive = PERSEUS_RAW / "hopper-texts-GreekRoman.tar.gz"
    if not archive.exists():
        print("downloading Perseus's Greek and Roman texts (125 MB) ...")
        with urllib.request.urlopen(urllib.request.Request(PERSEUS_ARCHIVE_URL, headers=UA)) as r, \
                archive.open("wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
    with tarfile.open(archive) as tar:
        for m in missing:
            (PERSEUS_RAW / Path(m).name).write_bytes(tar.extractfile(m).read())
    archive.unlink()


def perseus_notes(spec):
    """(book, ref_from, ref_to, parts) for each note paragraph of a Perseus commentary."""
    from import_commentaries import VS_RANGE, load_xml, parts_of, tidy
    root = load_xml(PERSEUS_RAW / Path(spec["file"]).name)

    def paragraphs(block):
        for p in block.iter("p"):
            parts = tidy(parts_of(p, block.get("lang")))
            if parts:
                yield parts

    for book in root.iter("div1"):  # line notes, a book at a time
        if book.get("type") != "book" or not (book.get("n") or "").isdigit():
            continue
        for cl in book.iter():
            if cl.get("type") == "commline" and (cl.get("n") or "").isdigit():
                line = int(cl.get("n"))
                for parts in paragraphs(cl):
                    m = VS_RANGE.match("".join(t for _, t in parts))
                    last = int(m[2]) if m and int(m[1]) == line and int(m[2]) > line else line
                    yield int(book.get("n")), str(line), str(last), parts
    for sec in root.iter():  # Stephanus sections (an introduction's numbered § don't match)
        if sec.get("type") == "section" and re.fullmatch(r"\d+[A-Ea-e]", sec.get("n") or ""):
            ref = sec.get("n").lower()
            for parts in paragraphs(sec):
                yield 1, ref, ref, parts


def scholia_notes(spec):
    """(book, line, line, None, text) for each scholion in a First1KGreek book-by-book edition."""
    tlg, wk = spec["work"].split(".")
    root = ElementTree.fromstring(get(FIRST1K.format(tlg, wk, spec["work"]), f"first1k_{spec['work']}.xml").encode())

    def flat(el, out):
        tag = el.tag.replace(TEI, "")
        if tag not in ("note", "head"):
            out.append(el.text or "")
            for child in el:
                flat(child, out)
        out.append(el.tail or "")

    for book in root.iter(f"{TEI}div"):
        if book.get("subtype") != "book" or not (book.get("n") or "").isdigit():
            continue
        b, line, argument = int(book.get("n")), 1, True  # the argument comes before the scholia
        for sec in book.iter(f"{TEI}div"):
            if sec.get("subtype") != "section":
                continue
            for p in sec.iter(f"{TEI}p"):
                out = []
                flat(p, out)
                text = "".join(out[:-1])  # without the paragraph's own tail
                text = re.sub(r"-\s*\n\s*", "", text)  # words broken over a printed line
                text = re.sub(r"\s+", " ", text).strip()
                if not text:
                    continue
                m = re.match(r"(\d{1,3})[ab]?\.\s*", text)
                if m:
                    argument = False
                    line, text = int(m[1]), text[m.end():]
                elif argument:  # the book's argument (ὑπόθεσις), before its first scholion
                    yield b, "1", "1", f"ὑπόθεσις] {text}"
                    continue
                yield b, str(line), str(line), text


def build_notes(slug, specs, segments, form):
    """Notes rows for a text: each source's notes placed on the segments they cover."""
    first, last = {}, {}  # (div, ref) -> seq of its first / last segment
    for div, seq, ref, *_ in segments:
        first.setdefault((div, ref), seq)
        last[(div, ref)] = seq
    sources, rows, skipped = [], [], 0
    for sort, spec in enumerate(specs):
        sources.append((slug, spec["id"], spec.get("siglum", spec["id"]), spec["name"], spec.get("lang"), sort))
        if spec["type"] == "perseus":
            items = ((b, a, z, json.dumps(parts, ensure_ascii=False), "".join(t for _, t in parts))
                     for b, a, z, parts in perseus_notes(spec))
        elif spec["type"] == "scholia":
            items = ((b, a, z, None, text) for b, a, z, text in scholia_notes(spec))
        else:
            sys.exit(f"{slug}: unknown notes type {spec['type']!r}")
        count = 0
        for b, a, z, parts, body in items:
            if (b, a) not in first:
                skipped += 1
                continue
            z = z if (b, z) in last else a
            rows.append((slug, b, first[(b, a)], last[(b, z)], spec["id"], len(rows), body, parts))
            count += 1
        print(f"  notes: {spec['id']}: {count}")
    if skipped:
        print(f"  notes: {skipped} on lines or sections not in this text, left out")
    return sources, rows


# ---------------------------------------------------------------- build

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogue", type=Path, default=ROOT / "library" / "catalogue.json")
    ap.add_argument("-v", action="store_true", help="report each book")
    args = ap.parse_args()
    if not args.catalogue.exists():
        sys.exit(f"no catalogue at {args.catalogue}: copy library/catalogue.example.json there and edit it")
    cat = json.loads(args.catalogue.read_text(encoding="utf-8"))
    categories = cat.get("categories", [])

    perseus_members([n["file"] for t in cat["texts"] for n in t.get("notes", []) if n["type"] == "perseus"])
    rows_t, rows_d, rows_s, rows_ns, rows_n, rows_w, rows_tr, rows_tc, rows_tb = [], [], [], [], [], [], [], [], []
    rows_tp, rows_aw, rows_rl = [], [], []
    for sort, t in enumerate(cat["texts"]):
        gold_from = (len(rows_w), len(rows_tb))  # (this text's gold parsing: rows added from here)
        if t.get("category") not in categories:
            sys.exit(f"{t['slug']}: category {t.get('category')!r} is not in the catalogue's categories")
        print(f"{t['slug']}: {t['title']}")
        reader = t.get("reader", "text")
        if reader == "iliad":
            form, cite, divs, segs = iliad(args.v)
        else:
            src = t["source"]
            if src["type"] == "wikisource":
                form, cite, divs, segs = wikisource(src, args.v)
            elif src["type"] == "perseus":
                form, cite, divs, segs = perseus(src, args.v, t["title"])
            elif src["type"] == "byztxt":
                found = []
                form, cite, divs, segs = byztxt(src, args.v, found)
                rows_w += [(t["slug"], *w, None, None) for w in found]
            elif src["type"] == "rahlfs":
                found = []
                form, cite, divs, segs = rahlfs(src, args.v, found)
                segs, found = church_wording(t["slug"], segs, found, args.v)
                rows_w += [(t["slug"], *w) for w in found]
            elif src["type"] == "swete":
                form, cite, divs, segs = swete(src, args.v)
            elif src["type"] == "prayers":
                found = []
                form, cite, divs, segs = prayers(src, args.v, t["title"], found)
                rows_rl += [(t["slug"], *r) for r in found]
            else:
                sys.exit(f"{t['slug']}: unknown source type {src['type']!r}")
        for fix in t.get("fixes", []):  # a slip in the source: {"ref": "17a", "find": ..., "replace": ...}
            hits = [k for k, s in enumerate(segs) if s[2] == fix["ref"] and fix["find"] in s[3]]
            if len(hits) != 1:
                sys.exit(f"{t['slug']}: fix at {fix['ref']} matches {len(hits)} segments")
            s = segs[hits[0]]
            segs[hits[0]] = (*s[:3], s[3].replace(fix["find"], fix["replace"], 1), *s[4:])
        # A chapter's title, as the recording reads it before the chapter: segment 0 of each
        # division, from "chapter_heading" ({N}: its number in Greek numerals, {nth}: as an
        # ordinal, {nth_m}: masculine), or "chapter_headings" for one ({"13": "Σωσάννα"}, null: none).
        titled = set()
        if t.get("chapter_heading") or t.get("chapter_headings"):
            own = t.get("chapter_headings", {})
            for n, _ in divs:
                title = own[str(n)] if str(n) in own else t.get("chapter_heading")
                if title:
                    segs.append((n, 0, "", title.format(N=greek_number(n), nth=ordinal(n), nth_m=ordinal(n, "ος")), None, 1))
                    titled.add(n)
            segs.sort(key=lambda s: (s[0], s[1]))
        if t.get("heading"):  # lines read before the text: segments ..., -1, 0 of its first division
            lines = [t["heading"]] if isinstance(t["heading"], str) else t["heading"]
            last = -1 if divs[0][0] in titled else 0  # (before its first chapter's title)
            segs = [(divs[0][0], last + k - len(lines) + 1, "", line, None, 1) for k, line in enumerate(lines)] + segs
        if t.get("label"):
            divs = [(n, t["label"].format(n=n, N=GREEK_NUMERALS[n] if n < len(GREEK_NUMERALS) else n))
                    for n, _ in divs]
        if t.get("notes"):
            ns, n = build_notes(t["slug"], t["notes"], segs, form)
            rows_ns += ns
            rows_n += n
        if t.get("treebank"):
            found = treebank_rows(t["treebank"], segs)
            rows_tb += [(t["slug"], *r) for r in found]
            words_total = sum(1 for s in segs if s[1] > 0 for w in s[3].split() if tb_key(w))
            print(f"  treebank: {len({r[:3] for r in found})} of {words_total} words parsed ({100 * len({r[:3] for r in found}) / max(1, words_total):.1f}%)")
        for spec in t.get("translations", []):
            if spec["type"] == "murray":
                found, split_lines = translation_passages(spec, segs)
                rows_tp += [(t["slug"], *row) for row in found]
                rows_tr += [(t["slug"], *row) for row in split_lines]
                print(f"  translation: {len(found)} passages" + (f", {len(split_lines)} lines split from them" if split_lines else ""))
            elif spec["type"] == "gutenberg":
                found = translation_lines(spec, segs)
                rows_tr += [(t["slug"], *row) for row in found]
                print(f"  translation: {len(found)} lines")
            else:
                rows_tr += [(t["slug"], *row) for row in translation(spec, segs)]
            rows_tc.append((t["slug"], spec["lang"], spec.get("credit")))
        gold = {r[1:4] for r in rows_w[gold_from[0]:]} | {r[1:4] for r in rows_tb[gold_from[1]:]}
        found = auto_words(t["slug"], segs, gold)
        rows_aw += found
        if found:
            print(f"  automatic parsing: {len(found)} words")
        rows_t.append((t["slug"], t["title"], t.get("author"), t["category"], sort, form, cite, "text",
                       t.get("credit"), t.get("notes_credit"), t.get("collection"), t.get("short"), int(bool(t.get("hidden"))),
                       t.get("eyebrow")))
        rows_d += [(t["slug"], n, label) for n, label in divs]
        rows_s += [(t["slug"], *s) for s in segs]

    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
        DROP TABLE IF EXISTS categories; DROP TABLE IF EXISTS texts; DROP TABLE IF EXISTS settings;
        DROP TABLE IF EXISTS divisions; DROP TABLE IF EXISTS segments;
        DROP TABLE IF EXISTS note_sources; DROP TABLE IF EXISTS notes;
        DROP TABLE IF EXISTS words; DROP TABLE IF EXISTS lexicon; DROP TABLE IF EXISTS translations; DROP TABLE IF EXISTS translation_passages;
        DROP TABLE IF EXISTS translation_credits; DROP TABLE IF EXISTS ref_labels;
        DROP TABLE IF EXISTS treebank; DROP TABLE IF EXISTS auto_words; DROP TABLE IF EXISTS lemma_defs;
        DROP TABLE IF EXISTS paraphrase_links;
        CREATE TABLE categories (name TEXT PRIMARY KEY, sort INTEGER NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- JSON: "lectionary", "church_text"
        CREATE TABLE texts (
            slug     TEXT PRIMARY KEY,
            title    TEXT NOT NULL,
            author   TEXT,
            category TEXT NOT NULL REFERENCES categories,
            sort     INTEGER NOT NULL,   -- order within the library
            form     TEXT NOT NULL,      -- verse (a line per segment) or prose
            cite     TEXT NOT NULL,      -- line, verse (numbered within a chapter) or section (Stephanus)
            reader   TEXT NOT NULL,      -- text (generic reader) or iliad
            credit   TEXT,
            notes_credit TEXT,           -- under the σχόλια panel
            collection TEXT,             -- listed with the other texts of this name (a Testament)
            short    TEXT,               -- a short title, for narrow screens (Ματθ.)
            hidden   INTEGER NOT NULL DEFAULT 0,   -- 1: kept, and readable by link, but not listed
            eyebrow  TEXT);              -- the reader's title over the text, if not "author · title"
        CREATE TABLE divisions (
            text  TEXT NOT NULL REFERENCES texts,
            n     INTEGER NOT NULL,      -- book number (1 for an undivided work)
            label TEXT,
            PRIMARY KEY (text, n));
        CREATE TABLE segments (
            text    TEXT NOT NULL,
            div     INTEGER NOT NULL,
            seq     INTEGER NOT NULL,    -- order within the division
            ref     TEXT,                -- line number, or Stephanus section (172a)
            content TEXT NOT NULL,
            speaker TEXT,                -- speaker label where a speech begins (ΣΩ.)
            para    INTEGER NOT NULL,    -- 1: starts a paragraph (always, for verse)
            PRIMARY KEY (text, div, seq));
        CREATE TABLE note_sources (
            text TEXT NOT NULL, id TEXT NOT NULL, siglum TEXT, name TEXT, lang TEXT,
            sort INTEGER NOT NULL, PRIMARY KEY (text, id));
        CREATE TABLE notes (
            text     TEXT NOT NULL,
            div      INTEGER NOT NULL,
            pos_from INTEGER NOT NULL,   -- first and last segment (seq) the note is on
            pos_to   INTEGER NOT NULL,
            source   TEXT NOT NULL,
            seq      INTEGER NOT NULL,   -- order within the source
            body     TEXT NOT NULL,      -- plain text ("lemma] note" for scholia)
            parts    TEXT);              -- JSON [[kind, text], ...] (l lemma, i italic, t text)
        CREATE INDEX notes_place ON notes (text, div, pos_from);
        CREATE TABLE words (             -- a parsed text's words (the New Testament: Robinson's; the Septuagint: CATSS)
            text       TEXT NOT NULL,
            div        INTEGER NOT NULL,
            seq        INTEGER NOT NULL,
            word_index INTEGER NOT NULL, -- 0-based whitespace chunk of the segment
            strongs    INTEGER,          -- Strong's number
            morph      TEXT,             -- Robinson's code (V-AAI-3S), or CATSS's (V.AAI3S)
            lemma      TEXT,             -- the word's own lemma and gloss where the lexicon
            gloss      TEXT,             --   (by Strong's number) hasn't it: the Septuagint
            PRIMARY KEY (text, div, seq, word_index));
        CREATE TABLE treebank (          -- a hand-made treebank's analysis of a text's words
            text       TEXT NOT NULL,
            div        INTEGER NOT NULL,
            seq        INTEGER NOT NULL,
            word_index INTEGER NOT NULL, -- 0-based whitespace chunk of the segment
            part       INTEGER NOT NULL, -- 0, or 1 for a word the treebank splits (οὐδέ: οὐ + δέ)
            lemma      TEXT,
            postag     TEXT,             -- the AGDT's 9 letters (n-s---fn-)
            relation   TEXT,             -- SBJ, OBJ, ATR, ... (_CO, _AP)
            head       TEXT,             -- the word it depends on ('' for the sentence's root)
            form       TEXT,             -- as the treebank spells it (a part: τὰ of τἀληθῆ)
            match      TEXT,             -- the Iliad's: how the word matched the treebank's (exact, variant, ...)
            PRIMARY KEY (text, div, seq, word_index, part));
        CREATE TABLE auto_words (        -- the lemmatiser's parsing, for words without gold parsing
            text       TEXT NOT NULL,      --   (scripts/lemmatise_library.py)
            div        INTEGER NOT NULL,
            seq        INTEGER NOT NULL,
            word_index INTEGER NOT NULL, -- 0-based whitespace chunk of the segment
            lemma      TEXT,
            headword   TEXT,             -- the short definitions' headword, and its definition
            definition TEXT,
            pos        TEXT,             -- UD's UPOS (NOUN, VERB, ...)
            morph      TEXT,             -- JSON: Morpheus's reading(s) as chosen in context ("aor ind act 3rd sg")
            confidence REAL,             -- the lemma's (0-1)
            alternative TEXT,            -- the next likeliest lemma, where it was a choice
            PRIMARY KEY (text, div, seq, word_index));
        CREATE TABLE lemma_defs (        -- short English definitions of the treebanks' lemmas
            lemma      TEXT PRIMARY KEY,
            definition TEXT NOT NULL);
        CREATE TABLE translations (      -- a translation of each verse (the New Testament: WEB, Vamvas)
            text    TEXT NOT NULL,
            div     INTEGER NOT NULL,
            seq     INTEGER NOT NULL,
            lang    TEXT NOT NULL,       -- en (English), el (modern Greek)
            content TEXT NOT NULL,
            PRIMARY KEY (text, div, seq, lang));
        CREATE TABLE translation_passages (  -- a translation in passages of several lines (Murray's Homer)
            text     TEXT NOT NULL,
            div      INTEGER NOT NULL,
            seq_from INTEGER NOT NULL,
            seq_to   INTEGER NOT NULL,
            lang     TEXT NOT NULL,
            content  TEXT NOT NULL,
            PRIMARY KEY (text, div, seq_from, lang));
        CREATE TABLE ref_labels (        -- a part's label (segments.ref) in another language
            text  TEXT NOT NULL,
            ref   TEXT NOT NULL,
            lang  TEXT NOT NULL,
            label TEXT NOT NULL,
            PRIMARY KEY (text, ref, lang));
        CREATE TABLE translation_credits (
            text   TEXT NOT NULL,
            lang   TEXT NOT NULL,
            credit TEXT,                 -- under the text, while that translation shows
            PRIMARY KEY (text, lang));
        CREATE TABLE lexicon (           -- Dodson's Greek-English lexicon, by Strong's number
            strongs    INTEGER PRIMARY KEY,
            lemma      TEXT NOT NULL,
            gloss      TEXT,             -- brief
            definition TEXT);            -- longer
    """)
    con.executemany("INSERT INTO categories VALUES (?, ?)", [(c, i) for i, c in enumerate(categories)])
    if cat.get("lectionary"):
        con.execute("INSERT INTO settings VALUES ('lectionary', ?)", (json.dumps(cat["lectionary"], ensure_ascii=False),))
    if CHURCH_TEXT.exists():  # (scripts/church_text.py compares with Rahlfs's text: not with this)
        con.execute("INSERT INTO settings VALUES ('church_text', ?)", (json.dumps(True),))
    con.executemany("INSERT INTO texts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_t)
    con.executemany("INSERT INTO translations VALUES (?, ?, ?, ?, ?)", rows_tr)
    con.executemany("INSERT INTO translation_passages VALUES (?, ?, ?, ?, ?, ?)", rows_tp)
    con.executemany("INSERT INTO translation_credits VALUES (?, ?, ?)", rows_tc)
    con.executemany("INSERT INTO ref_labels VALUES (?, ?, ?, ?)", rows_rl)
    con.executemany("INSERT OR IGNORE INTO treebank VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)", rows_tb)
    con.executemany("INSERT INTO auto_words VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_aw)
    if rows_tb or rows_w:  # definitions, as for the Iliad (import_shortdefs.py)
        from import_shortdefs import matcher
        match = matcher()
        defs = {lemma: m[0] for lemma in {r[5] for r in rows_tb if r[5]} | {r[6] for r in rows_w if r[6]}
                if (m := match(lemma))}
        con.executemany("INSERT INTO lemma_defs VALUES (?, ?)", defs.items())
    con.executemany("INSERT INTO note_sources VALUES (?, ?, ?, ?, ?, ?)", rows_ns)
    con.executemany("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows_n)
    con.executemany("INSERT INTO divisions VALUES (?, ?, ?)", rows_d)
    con.executemany("INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows_s)
    con.executemany("INSERT OR IGNORE INTO words VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows_w)
    if rows_w:
        con.executemany("INSERT OR IGNORE INTO lexicon VALUES (?, ?, ?, ?)", dodson())
    con.executescript(ILIAD_SCHEMA)
    for t in cat["texts"]:  # the Iliad: its contents, from data/iliad.sqlite
        if t.get("reader") == "iliad" and ILIAD_DB.exists():
            iliad_into_library(con, t["slug"], verbose=args.v)
    con.commit()
    con.close()
    print(f"{len(rows_t)} texts, {len(rows_d)} divisions, {len(rows_s)} segments, {len(rows_n)} notes, "
          f"{len(rows_w)} parsed words "
          f"-> {DB.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
