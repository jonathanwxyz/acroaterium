#!/usr/bin/env python3
"""Load three English school and scholarly commentaries on the Iliad into
data/iliad.sqlite (`commentaries`), from Perseus's XML source files (TEI P4; texts
CC BY-SA 3.0):

  Leaf     Walter Leaf, The Iliad (1900-02), notes on all 24 books
  Seymour  Thomas D. Seymour, Homer's Iliad, Books I-III and IV-VI (1891)
  Benner   Allen Rogers Benner, Selections from Homer's Iliad (1903): Books 1-3, 5, 6,
           9, 15, 16, 18, 19, 22, 24

Each note sits in a <div2 type="commline" n="LINE"> inside its book; one row per
paragraph. The Greek is in Beta Code (mh=nin) and is converted to Unicode (μῆνιν).
Inline styling is kept as `parts`, a JSON list of [kind, text] with kind "l" (lemma:
the words commented on, shown bold), "i" (italic: glosses, titles, emphasis) or "t".
A paragraph opening "Vs. 1-7." covers those lines (line_to). Book introductions outside
any commline, footnotes, and Benner's grammar appendix are left out.

The files come from Perseus's bulk download (hopper-texts-GreekRoman.tar.gz, 125 MB);
the four needed are kept in data/raw/perseus/ and the archive is deleted.

    python3 scripts/import_commentaries.py [-v]   (-v: notes per book and commentary)
"""
import html.entities
import json
import re
import sqlite3
import sys
import tarfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "perseus"
DB = ROOT / "data" / "iliad.sqlite"
ARCHIVE_URL = "https://www.perseus.tufts.edu/hopper/opensource/downloads/texts/hopper-texts-GreekRoman.tar.gz"
MEMBER = "Classics/Homer/opensource/{}"
COMMENTARIES = {  # source -> files
    "Leaf": ["leaf.hom.il_eng.xml"],
    "Seymour": ["seymour.hom.il1-3.xml", "seymour.hom.il4-6.xml"],
    "Benner": ["benner.hom.il_eng.xml"],
}
ITALIC = {"gloss", "title", "emph", "hi"}
SKIP = {"note", "head", "milestone", "pb", "lb", "table", "list"}


def fetch():
    files = [f for fs in COMMENTARIES.values() for f in fs]
    if all((RAW / f).exists() for f in files):
        return
    RAW.mkdir(parents=True, exist_ok=True)
    archive = RAW / "hopper-texts-GreekRoman.tar.gz"
    if not archive.exists():
        print("downloading Perseus's Greek and Roman texts (125 MB) ...")
        req = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "Mozilla/5.0 (acroaterium; personal study tool)"})
        with urllib.request.urlopen(req) as r, archive.open("wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
    with tarfile.open(archive) as tar:
        for f in files:
            (RAW / f).write_bytes(tar.extractfile(MEMBER.format(f)).read())
    archive.unlink()


# ---------------------------------------------------------------- Beta Code

BETA_LETTERS = dict(zip("abgdezhqiklmncoprstufxyw", "αβγδεζηθικλμνξοπρστυφχψω"))
BETA_MARKS = {")": "̓", "(": "̔", "+": "̈", "/": "́", "\\": "̀",
              "=": "͂", "|": "ͅ"}
MARK_ORDER = ")(+/\\=|"  # breathing, diaeresis, accent, iota subscript (Unicode's order)
BETA_PUNCT = {":": "·", "'": "’", "#": "ʹ"}


def beta_to_unicode(text):
    """Perseus Beta Code (mh=nin, *)axilleu/s) -> NFC polytonic Greek."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "*":  # capital: marks come between * and the letter
            j = i + 1
            while j < n and text[j] in BETA_MARKS:
                j += 1
            if j < n and text[j].lower() in BETA_LETTERS:
                marks = sorted(text[i + 1:j], key=MARK_ORDER.index)
                out.append(BETA_LETTERS[text[j].lower()].upper() + "".join(BETA_MARKS[m] for m in marks))
                i = j + 1
                continue
            i += 1
            continue
        low = c.lower()
        if low in BETA_LETTERS:
            j = i + 1
            while j < n and text[j] in BETA_MARKS:
                j += 1
            marks = sorted(text[i + 1:j], key=MARK_ORDER.index)
            letter = BETA_LETTERS[low]
            if low == "s":  # final sigma at a word's end; s1/s2 force medial/final
                if j < n and text[j] in "123":
                    letter = "ς" if text[j] == "2" else "σ"
                    j += 1
                elif j >= n or not text[j].isalpha():
                    letter = "ς"
            out.append(letter + "".join(BETA_MARKS[m] for m in marks))
            i = j
            continue
        out.append(BETA_PUNCT.get(c, c))
        i += 1
    return unicodedata.normalize("NFC", "".join(out))


# ---------------------------------------------------------------- XML

def load_xml(path):
    """Parse a TEI P4 file without its DTD: named entities resolved by hand."""
    s = path.read_text(encoding="utf-8", errors="replace")
    s = re.sub(r"<!DOCTYPE.*?\]>", "", s, count=1, flags=re.S)

    def entity(m):
        name = m[1]
        if name in ("lt", "gt", "amp", "quot", "apos"):
            return m[0]
        ch = html.entities.html5.get(name + ";")
        return ch if ch else ""  # Perseus's own (&Perseus.DE;) only appear in headers
    s = re.sub(r"&([A-Za-z][\w.]*);", entity, s)
    return ET.fromstring(s)


def parts_of(el, lang=None, kind="t", out=None):
    """[(kind, text)] for an element's content, Greek converted from Beta Code."""
    out = [] if out is None else out
    lang = el.get("lang", lang)
    tag = el.tag
    if tag == "lemma":
        kind = "l"
    elif tag in ITALIC or (tag == "foreign" and lang != "greek"):
        kind = "i"
    conv = beta_to_unicode if lang == "greek" else (lambda t: t)
    quoted = tag == "quote"
    if quoted:
        out.append(("t", "“"))
    if el.text:
        out.append((kind, conv(el.text)))
    prev_verse = False
    for child in el:
        if child.tag == "l" and prev_verse:  # verses quoted in a note run on, split by /
            out.append(("t", " / "))
        prev_verse = child.tag == "l"
        if child.tag not in SKIP:
            parts_of(child, lang, kind, out)
        # Seymour's book letters (α 1 = Od. 1.1, Π 842 = Il. 16.842) are encoded inside
        # the quotation: “… μοῦσα α” 1 -> “… μοῦσα” α 1.
        if child.tag == "quote" and re.match(r"\s*\d", child.tail or "") and len(out) > 1:
            m = re.search(r"\s([Α-Ωα-ω])$", out[-2][1])
            if m:
                out[-2] = (out[-2][0], out[-2][1][:m.start()])
                out.append(("t", " " + m[1]))
        if child.tail:
            out.append((kind, conv(child.tail)))
    if quoted:
        out.append(("t", "”"))
    return out


def tidy(parts):
    """Merge runs of one kind, collapse whitespace, drop empty pieces."""
    merged = []
    for kind, text in parts:
        text = re.sub(r"\s+", " ", text)
        if merged and merged[-1][0] == kind:
            merged[-1][1] += text
        else:
            merged.append([kind, text])
    for m in merged:
        m[1] = re.sub(r" {2,}", " ", m[1])
    if merged:
        merged[0][1] = merged[0][1].lstrip()
        merged[-1][1] = merged[-1][1].rstrip()
    merged = [p for p in merged if p[1]]
    for i in range(1, len(merged)):  # one space where pieces meet
        if merged[i][1].startswith(" ") and merged[i - 1][1].endswith(" "):
            merged[i][1] = merged[i][1].lstrip()
    for i in range(len(merged)):  # none before punctuation: “…” , and -> “…”, and
        merged[i][1] = re.sub(r"(?<=\S) +(?=[,;.:)](?:\s|$))", "", merged[i][1])
        if i and re.match(r" +[,;.:)](?:\s|$)", merged[i][1]):
            merged[i][1] = merged[i][1].lstrip(" ")
    for i in range(1, len(merged)):  # no space inside quotes: “ μῆνιν ” -> “μῆνιν”
        if merged[i - 1][1].endswith("“"):
            merged[i][1] = merged[i][1].lstrip()
        if merged[i][1].startswith(" ”") or merged[i][1] == "”":
            merged[i - 1][1] = merged[i - 1][1].rstrip()
    return [p for p in merged if p[1]]


VS_RANGE = re.compile(r"^(?:Vs|Vv|vs|vv)\.\s*(\d+)\s*[-–—]\s*(\d+)")


def notes(path):
    """(book, line, line_to, parts) for each paragraph of each commline."""
    root = load_xml(path)
    for book in root.iter("div1"):
        if book.get("type") != "book" or not (book.get("n") or "").isdigit():
            continue
        b = int(book.get("n"))
        for cl in book.iter():
            if cl.get("type") != "commline" or not (cl.get("n") or "").isdigit():
                continue
            line = int(cl.get("n"))
            for p in cl.iter("p"):
                parts = tidy(parts_of(p, cl.get("lang")))
                if not parts:
                    continue
                text = "".join(t for _, t in parts)
                m = VS_RANGE.match(text)
                last = int(m[2]) if m and int(m[1]) == line and int(m[2]) > line else line
                yield b, line, last, parts


def main():
    verbose = "-v" in sys.argv
    fetch()
    rows = []
    for source, files in COMMENTARIES.items():
        for f in files:
            for b, line, last, parts in notes(RAW / f):
                rows.append((b, line, last, source, len(rows), "".join(t for _, t in parts),
                             json.dumps(parts, ensure_ascii=False)))
    con = sqlite3.connect(DB)
    con.executescript("""
        DROP TABLE IF EXISTS commentaries;
        CREATE TABLE commentaries (
            book INTEGER, line INTEGER, line_to INTEGER,
            source TEXT,        -- Leaf, Seymour, Benner
            seq INTEGER PRIMARY KEY,  -- order within the commentaries
            text TEXT,          -- the paragraph as plain text
            parts TEXT);        -- JSON [[kind, text], ...]: l = lemma, i = italic, t = text
        CREATE INDEX commentaries_line ON commentaries (book, line);
    """)
    con.executemany("INSERT INTO commentaries VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    lines = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT book, line FROM commentaries)").fetchone()[0]
    print(f"{len(rows)} commentary notes on {lines} lines -> {DB.relative_to(ROOT)} (commentaries)")
    if verbose:
        for source in COMMENTARIES:
            per = dict(con.execute("SELECT book, COUNT(*) FROM commentaries WHERE source = ? GROUP BY book", (source,)))
            print(f"{source:8}", " ".join(f"{per.get(b, 0):4}" for b in range(1, 25)))
    con.close()


if __name__ == "__main__":
    main()
