#!/usr/bin/env python3
"""Load Iakovos Polylas's modern Greek verse translation of the Iliad (published 1923,
public domain) from Greek Wikisource into data/iliad.sqlite (`translation_lines`,
translation = 'polylas').

Polylas renders the Iliad line for line, and Wikisource marks every fifth line with
{{r|n}}. A book is loaded only if it has as many lines as the Greek (verses table)
and every marker n sits on its n-th line; other books are reported and skipped.
Within a 5-line block he sometimes drifts by a line or swaps two; hand-made
corrections in data/translation_lines/polylas_book<NN>.txt (`book.line = polylas
lines` or `-`) fix those, and every Polylas line must then be used exactly once.
Raw wikitext is cached in data/raw/polylas/.
"""
import html
import re
import sqlite3
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "polylas"
CORRECTIONS = ROOT / "data" / "translation_lines"
DB = ROOT / "data" / "iliad.sqlite"
TRANSLATION = "polylas"
PAGE = "Ιλιάδα (Πολυλάς)/{}"
BOOK_LETTERS = "αβγδεζηθικλμνξοπρστυφχψω"


def fetch(book):
    path = RAW / f"{book:02d}.txt"
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        title = urllib.parse.quote(PAGE.format(BOOK_LETTERS[book - 1]))
        url = f"https://el.wikisource.org/w/index.php?title={title}&action=raw"
        req = urllib.request.Request(url, headers={"User-Agent": "acroaterium/1.0 (personal study tool)"})
        with urllib.request.urlopen(req) as r:
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8")


def verse_lines(wikitext):
    """[(text, marker or None)] for each verse line of the page."""
    body = re.search(r"<poem>(.*?)</poem>", wikitext, re.S)
    body = body[1] if body else wikitext.split("}}", 1)[-1]
    out = []
    for raw in body.splitlines():
        marker = re.search(r"\{\{\s*r\s*\|\s*(\d+)\s*\}\}", raw)
        text = re.sub(r"\{\{[^}]*\}\}", "", raw)              # templates ({{r|5}} etc.)
        text = re.sub(r"<[^>]+>", "", text)                   # <br>, spans
        text = re.sub(r"'{2,}", "", text)                     # wiki bold/italics
        text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)  # links
        text = unicodedata.normalize("NFC", html.unescape(text))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append((text, int(marker[1]) if marker else None))
    return out


def read_corrections(book):
    """{greek line: [polylas lines]} from polylas_book<NN>.txt, if present."""
    path = CORRECTIONS / f"polylas_book{book:02d}.txt"
    out = {}
    if path.exists():
        for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            m = re.fullmatch(r"(\d+)\.(\d+)\s*=\s*(-|[\d\s]+)", s)
            if not m or int(m[1]) != book:
                sys.exit(f"{path.name}:{n}: can't read {raw!r}")
            out[int(m[2])] = [] if m[3] == "-" else [int(x) for x in m[3].split()]
    return out


def main():
    con = sqlite3.connect(DB)
    greek_lines = dict(con.execute("SELECT book, count(*) FROM verses GROUP BY book"))
    rows, report = [], []
    for book in range(1, 25):
        lines = verse_lines(fetch(book))
        misplaced = [(i, m) for i, (_, m) in enumerate(lines, 1) if m is not None and m != i]
        if len(lines) != greek_lines[book] or misplaced:
            report.append(f"book {book:2}: skipped — {len(lines)} lines vs {greek_lines[book]} Greek"
                          + (f"; first misplaced marker {misplaced[0][1]} on line {misplaced[0][0]}" if misplaced else ""))
            continue
        fixes = read_corrections(book)
        mapping = {g: fixes.get(g, [g]) for g in range(1, len(lines) + 1)}
        used = sorted(p for ps in mapping.values() for p in ps)
        if used != list(range(1, len(lines) + 1)):
            dup = sorted({p for p in used if used.count(p) > 1})
            missing = sorted(set(range(1, len(lines) + 1)) - set(used))
            sys.exit(f"book {book}: corrections use Polylas lines {dup} twice and {missing} not at all")
        rows += [(TRANSLATION, book, g, " ".join(lines[p - 1][0] for p in ps) or None)
                 for g, ps in mapping.items()]
        report.append(f"book {book:2}: {len(lines)} lines" + (f", {len(fixes)} corrected" if fixes else ""))

    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS translation_lines (
            translation TEXT    NOT NULL,  -- murray (English), polylas (modern Greek), ...
            book        INTEGER NOT NULL,
            line        INTEGER NOT NULL,
            text        TEXT,              -- NULL: no text of its own (in the line before)
            PRIMARY KEY (translation, book, line)
        ) WITHOUT ROWID;
        """
    )
    con.execute("DELETE FROM translation_lines WHERE translation = ?", (TRANSLATION,))
    con.executemany("INSERT INTO translation_lines VALUES (?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    print(f"{len(rows)} Polylas lines -> {DB.relative_to(ROOT)} (translation_lines)")
    print(*report, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
