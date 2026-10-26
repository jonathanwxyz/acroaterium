#!/usr/bin/env python3
"""Load the ancient scholia on the Iliad into data/iliad.sqlite (`scholia`), from
First1KGreek's TEI of Dindorf & Maass, Scholia Graeca in Homeri Iliadem (Oxford,
1875-88; tlg5026.tlg001, CC BY-SA 4.0). The XML is cached in data/raw/.

Each <div subtype="section"> carries the Iliad line(s) it comments on
(corresp="urn:cts:greekLit:tlg0012.tlg001:B.L" or "B.L-B.M"). The edition's volumes
are kept apart by manuscript (SOURCES). Dropped: the editors' Latin footnotes and the
marginal sigla (A=, B+ …: how far another manuscript agrees). Lemmas that the encoding
marks as <del status="error"> lost their closing bracket and get it back. Where a
section runs on into the scholia of a later line ("5. οἰωνοῖσι] … 8. σφῶε] …"), it is
split there, so each line gets its own notes.

    python3 scripts/import_scholia.py [-v]   (-v: entries per book and source)
"""
import re
import sqlite3
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "tlg5026.tlg001.1st1K-grc1.xml"
DB = ROOT / "data" / "iliad.sqlite"
URL = ("https://raw.githubusercontent.com/OpenGreekAndLatin/First1KGreek/master/"
       "data/tlg5026/tlg001/tlg5026.tlg001.1st1K-grc1.xml")
TEI = "{http://www.tei-c.org/ns/1.0}"
PARA = "\x1e"  # paragraph boundary while flattening (source newlines are just spaces)

# Edition volume -> (source id, order shown). Dindorf 1-4, Maass 5-6.
SOURCES = {
    "1": "A", "2": "A",          # Venetus A (Marc. gr. 822), main scholia
    "2a": "A-int",               # Venetus A, interlinear glosses
    "3": "B", "4": "B",          # Venetus B (Marc. gr. 453)
    "4a": "B-rec",               # Venetus B, later hand (from the Etymologica etc.)
    "5": "T", "6": "T",          # Townleianus (Burney 86)
    "6a": "T-rec",               # Townleianus, second hand
}
SOURCE_ORDER = ["A", "A-int", "B", "B-rec", "T", "T-rec"]

# "8. σφῶε]" (or "7a. …") inside a section: a scholion on a later line run into this one.
RUN_ON = re.compile(r"(?:^|(?<=[\s\]·.,;]))(\d{1,3})[ab]?\. (?=[*∗⌈〈⟨(]*\s*[^\W\d])")


def fetch():
    if not RAW.exists():
        RAW.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(URL, headers={"User-Agent": "acroaterium/1.0 (personal study tool)"})
        with urllib.request.urlopen(req) as r:
            RAW.write_bytes(r.read())
    return ET.parse(RAW).getroot()


def flatten(el, out):
    """Append el's text to out, paragraphs separated by PARA, notes dropped."""
    tag = el.tag.removeprefix(TEI)
    if tag == "note":
        out.append(el.tail or "")
        return
    if tag in ("p", "lg"):
        out.append(PARA)
    out.append(el.text or "")
    for child in el:
        flatten(child, out)
    if tag == "del":
        out.append("]")
    elif tag == "gap":
        out.append(" … ")
    elif tag in ("p", "lg"):
        out.append(PARA)
    out.append(el.tail or "")


def clean(text):
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)   # words hyphenated across lines
    text = re.sub(r"(?<=\S)-[ \t]+(?=[^\W\d])", "", text)  # … and where the break was a tag
    paras = (re.sub(r"\s+", " ", p).strip() for p in text.split(PARA))
    return [p.replace(" ]", "]") for p in paras if p]


def split_run_ons(book, line, paras):
    """[(line, paragraph)], moving run-on scholia of later lines to their own line."""
    out, cur = [], line
    for p in paras:
        pieces, start = [], 0
        for m in RUN_ON.finditer(p):
            n = int(m[1])
            if cur < n <= cur + 30 and not re.search(r"(?:\b[vV]|Il|Od|p)\.\s*$|,\s*$", p[:m.start()]):
                pieces.append((cur, p[start:m.start()]))
                cur, start = n, m.start()
        pieces.append((cur, p[start:]))
        out += [(ln, s.strip()) for ln, s in pieces if s.strip()]
    return out


def lead_number(line, p):
    """Drop the line number(s) a scholion opens with ("1. Μῆνιν]", "4, 5. ὅτι", "12—16.", or "I." for 1)."""
    m = re.match(r"(\d+|I|l)[ab]?(?:[,—–-]\s*\d+)*(?:[.,\"]\s*|\s+)", p)  # the dot is often lost
    return p[m.end():] if m and m[1] in (str(line), *(("I", "l") if line == 1 else ())) else p


def main():
    verbose = "-v" in sys.argv
    root = fetch()
    rows = []
    for vol in root.iter(f"{TEI}div"):
        if vol.get("subtype") != "volume":
            continue
        source = SOURCES[vol.get("n")]
        for sec in vol.iter(f"{TEI}div"):
            ref = (sec.get("corresp") or "").rpartition(":")[2]
            m = re.fullmatch(r"(\d+)\.(\d+)(?:-(\d+)\.(\d+))?", ref)
            if sec.get("subtype") != "section" or not m:
                continue
            book, line = int(m[1]), int(m[2])
            line_to = int(m[4]) if m[3] and int(m[3]) == book else line
            out = []
            for child in sec:
                flatten(child, out)
            for ln, p in split_run_ons(book, line, clean((sec.text or "") + "".join(out))):
                last = line_to if ln == line else ln
                rows.append((book, ln, last, source, lead_number(ln, p)))

    con = sqlite3.connect(DB)
    con.executescript("""
        DROP TABLE IF EXISTS scholia;
        CREATE TABLE scholia (
            book INTEGER, line INTEGER, line_to INTEGER,
            source TEXT,        -- A, A-int, B, B-rec, T, T-rec (see import_scholia.py)
            seq INTEGER,        -- order within the edition
            text TEXT,          -- one scholion (paragraph) per row
            PRIMARY KEY (seq));
        CREATE INDEX scholia_line ON scholia (book, line);
    """)
    con.executemany("INSERT INTO scholia VALUES (?, ?, ?, ?, ?, ?)",
                    [(b, l, lt, s, i, t) for i, (b, l, lt, s, t) in enumerate(rows)])
    con.commit()
    lines = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT book, line FROM scholia)").fetchone()[0]
    print(f"{len(rows)} scholia on {lines} lines -> {DB.relative_to(ROOT)} (scholia)")
    if verbose:
        for source in SOURCE_ORDER:
            per = dict(con.execute("SELECT book, COUNT(*) FROM scholia WHERE source = ? GROUP BY book", (source,)))
            print(f"{source:6}", " ".join(f"{per.get(b, 0):4}" for b in range(1, 25)))
    con.close()


if __name__ == "__main__":
    main()
