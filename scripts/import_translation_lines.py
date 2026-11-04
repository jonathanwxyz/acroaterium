#!/usr/bin/env python3
"""Split Murray's passages (translation_passages) into single Greek lines, using the
hand-made cut points in data/translation_lines/murray_book<NN>.txt, and store them in
data/iliad.sqlite as `translation_lines`.

Run after import_translation.py. Each cut-point entry is `book.line first words`: the
line's English starts at those words, searched for after the previous cut within the
passage. Checks that every line of a split passage has an entry and that the pieces
rejoin to the passage text exactly.

A passage's first line needs no entry. Where Murray's English for it lies across the
passage break, it may have one (across()): words found at the end of the line before
(the previous passage's last line) bring that English forward into this line; words found
further into this line's own English give what comes before them to the line before
(a line marked "-" there takes it).
"""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "translation_lines"
DB = ROOT / "data" / "iliad.sqlite"
TRANSLATION = "murray"
OPENERS = "“‘(["  # a cut moves back over these so they stay with the words they open


def read_cuts(path):
    cuts = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = re.fullmatch(r"(\d+)\.(\d+)\s+(.+)", s)
        if not m:
            sys.exit(f"{path.name}:{n}: can't read {raw!r}")
        cuts[(int(m[1]), int(m[2]))] = None if m[3] == "-" else m[3]
    return cuts


def split(text, first, last, book, cuts, errors):
    """Pieces of one passage, one per line first..last."""
    starts, pos = [0], 0
    for line in range(first + 1, last + 1):
        if (book, line) not in cuts:
            errors.append(f"{book}.{line}: no cut point")
            return None
        words = cuts[(book, line)]
        if words is None:  # no English of its own: it starts where the next line does (below)
            starts.append(None)
            continue
        at = text.find(words, pos)
        if at < 0:
            errors.append(f"{book}.{line}: {words!r} not found after {text[pos:pos + 40]!r}")
            return None
        while at > 0 and (text[at - 1] in OPENERS or text[at - 1] == " " and text[at - 2:at - 1] in OPENERS):
            at -= 1
        starts.append(at)
        pos = at + len(words)
    bounds = starts + [len(text)]
    for k in range(len(bounds) - 2, -1, -1):  # an empty line's piece: none, the line before keeps its own
        if bounds[k] is None:
            bounds[k] = bounds[k + 1]
    return [text[a:b].strip() for a, b in zip(bounds, bounds[1:])]


def across(book, pieces, firsts, cuts, errors):
    """Move English across passage breaks, by the entries for passages' first lines
    (pieces: {line: English}, in order, changed in place)."""
    for line in firsts:
        words = cuts.get((book, line))
        if not words or line - 1 not in pieces or line not in pieces:
            continue
        prev, own = pieces[line - 1] or "", pieces[line] or ""
        at = prev.find(words)
        if at > 0:
            while at > 0 and (prev[at - 1] in OPENERS or prev[at - 1] == " " and prev[at - 2:at - 1] in OPENERS):
                at -= 1
            pieces[line - 1], pieces[line] = prev[:at].strip(), (prev[at:].strip() + " " + own).strip()
            continue
        at = own.find(words)
        if at > 0:
            while at > 0 and (own[at - 1] in OPENERS or own[at - 1] == " " and own[at - 2:at - 1] in OPENERS):
                at -= 1
            pieces[line - 1], pieces[line] = (prev + " " + own[:at].strip()).strip(), own[at:].strip()
            continue
        if at < 0:
            errors.append(f"{book}.{line}: {words!r} not found at the end of {book}.{line - 1} or in {book}.{line}")


def main():
    con = sqlite3.connect(DB)
    rows, errors = [], []
    for path in sorted(SRC.glob(f"{TRANSLATION}_book*.txt")):
        cuts = read_cuts(path)
        books = {b for b, _ in cuts}
        for book in sorted(books):
            done = {l for b, l in cuts if b == book}
            pieces_of, firsts = {}, []
            for first, last, text in con.execute(
                    "SELECT line_from, line_to, text FROM translation_passages "
                    "WHERE translation = ? AND book = ? ORDER BY line_from", (TRANSLATION, book)):
                if last > first and not any(first < l <= last for l in done):
                    continue  # passage not split yet
                pieces = split(text, first, last, book, cuts, errors)
                if pieces is None:
                    continue
                if "".join("".join(pieces).split()) != "".join(text.split()):
                    errors.append(f"{book}.{first}-{last}: pieces don't rejoin to the passage")
                    continue
                pieces_of.update(zip(range(first, last + 1), pieces))
                firsts.append(first)
            across(book, pieces_of, firsts, cuts, errors)
            rows += [(TRANSLATION, book, line, piece or None) for line, piece in pieces_of.items()]

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
    print(f"{len(rows)} lines -> {DB.relative_to(ROOT)} (translation_lines)")
    if errors:
        print("Problems:", *errors, sep="\n  ")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
