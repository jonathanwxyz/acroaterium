#!/usr/bin/env python3
"""Build the library (data/library.sqlite): the texts the reader offers, grouped by
category, from a catalogue file.

    python3 scripts/library.py [--catalogue library/catalogue.json] [-v]

The catalogue is local (library/ is git-ignored); library/catalogue.example.json in
the repository shows the format. It is JSON:

    {"categories": ["Epic", "Philosophy"],          -- shown in this order
     "texts": [
       {"slug": "iliad", "title": "Ἰλιάς", "author": "Ὅμηρος", "category": "Epic",
        "reader": "iliad"},                         -- the Iliad reader (data/iliad.sqlite)
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
              after Swete's ("guide": the book's swete "books")
  swete       a book of the Septuagint ("books": [Swete's abbreviations, Gen, Dat, ...],
              see swete()): Swete's text as transcribed in LXX-Swete-1930; likewise
A text's "fixes" correct slips in its source: [{"ref": "17a", "find": "<as printed>",
"replace": "<as it should read>"}] (the text at that section; it must match once).
A text's "heading" is a title read before the text ("Πλάτωνος Ἀπολογία Σωκράτους"), or a list of lines
(the title, then a dialogue's characters): segments ..., -1, 0 of its first division,
shown (and played) as a title.
A text's "translations" (see translation()) add a translated verse under each verse:
the World English Bible, Vamvas's modern Greek.
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

Tables: categories (name, sort), texts (slug, title, author, category, sort, form:
verse|prose, cite: line|section, reader: text|iliad, credit, notes_credit, collection), divisions (text, n, label:
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
CACHE = ROOT / "data" / "raw" / "library"
UA = {"User-Agent": "Mozilla/5.0 (scrolling-reader library builder; personal study tool)"}
TEI = "{http://www.tei-c.org/ns/1.0}"
GREEK_NUMERALS = " ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
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
    return out


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
#   vamvas  Neophytos Vamvas's modern Greek (1850, public domain), CrossWire's GreVamvas as
#           scrollmapper/bible_databases has it in CSV, in monotonic spelling ("book":
#           Matthew, I Corinthians, ...)
# The reader shows English under the "translation" switch, modern Greek under "μετάφραση".
WEB_ZIP = "https://ebible.org/Scriptures/engwebp_vpl.zip"
VAMVAS = "https://raw.githubusercontent.com/scrollmapper/bible_databases/master/formats/csv/GreVamvas.csv"
_web = _vamvas = None


def web_verses():
    """{(book, chapter, verse): English} of the World English Bible."""
    global _web
    if _web is None:
        path = CACHE / "web" / "engwebp_vpl.zip"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(urllib.request.Request(WEB_ZIP, headers=UA)) as r:
                path.write_bytes(r.read())
        with zipfile.ZipFile(path) as z:
            text = z.read("engwebp_vpl.txt").decode("utf-8")
        _web = {(m[1], int(m[2]), int(m[3])): m[4].strip()
                for m in re.finditer(r"^(\w+) (\d+):(\d+) (.*)$", text, re.M)}
    return _web


def vamvas_verses():
    """{(book, chapter, verse): modern Greek} of Vamvas's translation."""
    global _vamvas
    if _vamvas is None:
        _vamvas = {(r["Book"], int(r["Chapter"]), int(r["Verse"])): r["Text"].strip()
                   for r in csv.DictReader(io.StringIO(get(VAMVAS, "vamvas/GreVamvas.csv")))}
    return _vamvas


def translation(spec, segments):
    """(div, seq, language, text) for the segments a translation has (by chapter and verse)."""
    verses = {"web": web_verses, "vamvas": vamvas_verses}.get(spec["type"])
    if not verses:
        sys.exit(f"unknown translation type {spec['type']!r}")
    verses = verses()
    # "moved": {"14:24": "16:25"}: verses the translation numbers otherwise (Vamvas puts the
    # end of Romans at 16:25-27, the Patriarchal text after chapter 14).
    moved = {tuple(map(int, a.split(":"))): tuple(map(int, b.split(":"))) for a, b in spec.get("moved", {}).items()}
    out = []
    for div, seq, ref, *_ in segments:
        if ref.isdigit():
            c, v = moved.get((div, int(ref)), (div, int(ref)))
            if text := verses.get((spec["book"], c, v)):  # (WEB lists the verses it omits, empty)
                out.append((div, seq, spec["lang"], text))
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


# ---------------------------------------------------------------- Iliad (its own reader)

def iliad(verbose):
    if not ILIAD_DB.exists():
        print("  (data/iliad.sqlite not built: no books listed)")
        return "verse", "line", [], []
    con = sqlite3.connect(ILIAD_DB)
    books = [b for (b,) in con.execute("SELECT DISTINCT book FROM verses ORDER BY book")]
    con.close()
    return "verse", "line", [(b, f"Ῥαψῳδία {GREEK_NUMERALS[b]}") for b in books], []


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
    for sort, t in enumerate(cat["texts"]):
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
                rows_w += [(t["slug"], *w) for w in found]
            elif src["type"] == "swete":
                form, cite, divs, segs = swete(src, args.v)
            else:
                sys.exit(f"{t['slug']}: unknown source type {src['type']!r}")
        for fix in t.get("fixes", []):  # a slip in the source: {"ref": "17a", "find": ..., "replace": ...}
            hits = [k for k, s in enumerate(segs) if s[2] == fix["ref"] and fix["find"] in s[3]]
            if len(hits) != 1:
                sys.exit(f"{t['slug']}: fix at {fix['ref']} matches {len(hits)} segments")
            s = segs[hits[0]]
            segs[hits[0]] = (*s[:3], s[3].replace(fix["find"], fix["replace"], 1), *s[4:])
        if t.get("heading"):  # lines read before the text: segments ..., -1, 0 of its first division
            lines = [t["heading"]] if isinstance(t["heading"], str) else t["heading"]
            segs = [(divs[0][0], k - len(lines) + 1, "", line, None, 1) for k, line in enumerate(lines)] + segs
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
            rows_tr += [(t["slug"], *row) for row in translation(spec, segs)]
            rows_tc.append((t["slug"], spec["lang"], spec.get("credit")))
        rows_t.append((t["slug"], t["title"], t.get("author"), t["category"], sort, form, cite, reader,
                       t.get("credit"), t.get("notes_credit"), t.get("collection"), t.get("short")))
        rows_d += [(t["slug"], n, label) for n, label in divs]
        rows_s += [(t["slug"], *s) for s in segs]

    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
        DROP TABLE IF EXISTS categories; DROP TABLE IF EXISTS texts; DROP TABLE IF EXISTS settings;
        DROP TABLE IF EXISTS divisions; DROP TABLE IF EXISTS segments;
        DROP TABLE IF EXISTS note_sources; DROP TABLE IF EXISTS notes;
        DROP TABLE IF EXISTS words; DROP TABLE IF EXISTS lexicon; DROP TABLE IF EXISTS translations;
        DROP TABLE IF EXISTS translation_credits;
        DROP TABLE IF EXISTS treebank; DROP TABLE IF EXISTS lemma_defs;
        CREATE TABLE categories (name TEXT PRIMARY KEY, sort INTEGER NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- JSON: "lectionary"
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
            short    TEXT);              -- a short title, for narrow screens (Ματθ.)
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
            PRIMARY KEY (text, div, seq, word_index, part));
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
    con.executemany("INSERT INTO texts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_t)
    con.executemany("INSERT INTO translations VALUES (?, ?, ?, ?, ?)", rows_tr)
    con.executemany("INSERT INTO translation_credits VALUES (?, ?, ?)", rows_tc)
    con.executemany("INSERT OR IGNORE INTO treebank VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_tb)
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
    con.commit()
    con.close()
    print(f"{len(rows_t)} texts, {len(rows_d)} divisions, {len(rows_s)} segments, {len(rows_n)} notes, "
          f"{len(rows_w)} parsed words "
          f"-> {DB.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
