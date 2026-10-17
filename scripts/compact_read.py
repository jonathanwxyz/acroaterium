#!/usr/bin/env python3
"""Reading data/library_compact.sqlite (written by scripts/compact_library.py): the
reading side of its format, which an app's own decoder follows.

    from compact_read import Compact
    lib = Compact("data/library_compact.sqlite")
    lib.rows("segments", "iliad", 1)        # [{"seq": 0, "ref": ..., "content": ...}, ...]
    lib.full_rows("words", "<slug>", 1)    # rows as the full database's table has them

    python3 scripts/compact_read.py data/library_compact.sqlite OUT_DIR
        writes OUT_DIR/library.sqlite and OUT_DIR/library_audio.sqlite back from it, as
        the full databases (the check in compact_library.py compares them row for row).

The format, in short (compact_library.py's docstring has it in full):
  tables      the small tables of the full databases, under their own names and as they are
              (texts, divisions, categories, settings, lexicon, recordings, ...)
  chunks      (kind, text, div) -> rows: zlib (RFC 1950) of UTF-8 JSON, an array of arrays,
              each a row of that kind's columns (CHUNK_COLUMNS), in order
  dicts       (kind, id) -> value: JSON; a chunk's column named "<kind>_id" holds the id of a
              value in dicts of that kind (a lemma, a morphology code, ...)
  schema      the full databases' CREATE statements, to rebuild them (restore)
"""
import json
import sqlite3
import sys
import zlib
from pathlib import Path

# Each chunk kind's columns, as stored. "x_id": an id in dicts kind "x" (DICTS says what its
# value is); "content?": null where it's the segment's own content (units); "body?": null
# where it's the notes' parts' text joined (notes).
CHUNK_COLUMNS = {
    "segments": ["seq", "ref", "content", "speaker", "para"],
    "translations": ["seq", "lang", "content"],
    "translation_passages": ["seq_from", "seq_to", "lang", "content"],
    "notes": ["pos_from", "pos_to", "source", "seq", "body?", "parts"],
    "words": ["seq", "word_index", "strongs", "words.morph_id", "words.lemma_id"],
    "treebank": ["seq", "word_index", "part", "treebank.lemma_id", "treebank.postag_id", "treebank.relation_id",
                 "treebank.head_id", "form", "match"],
    "auto_words": ["seq", "word_index", "auto.lemma_id", "auto.morph_id", "confidence", "alternative"],
    "paraphrase_links": ["seq", "word_index", "para_seq", "para_index", "para_word"],
    "units": ["seq", "piece", "content?", "start", "speech_end", "end", "score", "cut"],
}
# What a dict's value is, by the full table's columns it stands for (a list: those columns).
DICTS = {
    "words.morph": ["morph"],
    "words.lemma": ["lemma", "gloss"],
    "treebank.lemma": ["lemma"],
    "treebank.postag": ["postag"],
    "treebank.relation": ["relation"],
    "treebank.head": ["head"],
    "auto.lemma": ["lemma", "headword", "definition"],
    "auto.morph": ["pos", "morph"],
}
# The full table each kind restores, and its columns in the full table's order.
FULL = {
    "segments": ("library", "segments", ["text", "div", "seq", "ref", "content", "speaker", "para"]),
    "translations": ("library", "translations", ["text", "div", "seq", "lang", "content"]),
    "translation_passages": ("library", "translation_passages", ["text", "div", "seq_from", "seq_to", "lang", "content"]),
    "notes": ("library", "notes", ["text", "div", "pos_from", "pos_to", "source", "seq", "body", "parts"]),
    "words": ("library", "words", ["text", "div", "seq", "word_index", "strongs", "morph", "lemma", "gloss"]),
    "treebank": ("library", "treebank", ["text", "div", "seq", "word_index", "part", "lemma", "postag", "relation",
                                         "head", "form", "match"]),
    "auto_words": ("library", "auto_words", ["text", "div", "seq", "word_index", "lemma", "headword", "definition",
                                             "pos", "morph", "confidence", "alternative"]),
    "paraphrase_links": ("library", "paraphrase_links", ["text", "div", "seq", "word_index", "para_seq", "para_index",
                                                         "para_word"]),
    "units": ("audio", "units", ["text", "div", "seq", "piece", "content", "start", "speech_end", "end", "score", "cut"]),
}


def unpack(blob):
    """A chunk's rows: zlib, then JSON."""
    return json.loads(zlib.decompress(blob).decode("utf-8"))


class Compact:
    def __init__(self, path):
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._dicts = {}
        self._segments = {}

    def dict(self, kind):
        """{id: value} of a dict kind (values as lists where they stand for several columns)."""
        if kind not in self._dicts:
            self._dicts[kind] = {i: json.loads(v) for i, v in
                                 self.con.execute("SELECT id, value FROM dicts WHERE kind = ?", (kind,))}
        return self._dicts[kind]

    def chunk(self, kind, text, div):
        """A chunk's rows as stored (ids not resolved), or []."""
        row = self.con.execute("SELECT rows FROM chunks WHERE kind = ? AND text = ? AND div = ?",
                               (kind, text, div)).fetchone()
        return unpack(row[0]) if row else []

    def places(self, kind):
        """The (text, div) of every chunk of a kind."""
        return self.con.execute("SELECT text, div FROM chunks WHERE kind = ? ORDER BY text, div", (kind,)).fetchall()

    def rows(self, kind, text, div):
        """A chunk's rows as dicts of the full table's columns, ids resolved and what was
        left out (a unit's content, a note's body) filled in."""
        out = []
        for r in self.chunk(kind, text, div):
            d = {}
            for name, value in zip(CHUNK_COLUMNS[kind], r):
                if name.endswith("_id"):
                    d.update(zip(DICTS[name[:-3]], self._value(name[:-3], value)))
                else:
                    d[name.rstrip("?")] = value
            if kind == "units" and d["content"] is None:
                d["content"] = self._segment(text, div)[d["seq"]]
            if kind == "notes" and d["body"] is None:
                d["body"] = "".join(run for _, run in json.loads(d["parts"]))
            out.append(d)
        return out

    def full_rows(self, kind, text, div):
        """A chunk's rows as tuples in the full table's column order."""
        _, _, cols = FULL[kind]
        return [tuple({"text": text, "div": div, **d}[c] for c in cols) for d in self.rows(kind, text, div)]

    def _value(self, kind, i):
        v = self.dict(kind)[i]
        return v if len(DICTS[kind]) > 1 else [v]

    def _segment(self, text, div):
        if (text, div) not in self._segments:
            self._segments[text, div] = {seq: content for seq, _, content, *_ in self.chunk("segments", text, div)}
        return self._segments[text, div]


def restore(compact_path, out_dir):
    """Write the full databases back from the compact one: OUT_DIR/library.sqlite and
    OUT_DIR/library_audio.sqlite."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lib = Compact(compact_path)
    targets = {"library": out_dir / "library.sqlite", "audio": out_dir / "library_audio.sqlite"}
    cons = {}
    for db, path in targets.items():
        path.unlink(missing_ok=True)
        cons[db] = sqlite3.connect(path)
    for db, name, sql in lib.con.execute("SELECT db, name, sql FROM schema ORDER BY rowid"):
        cons[db].execute(sql)
    for db, name in lib.con.execute("SELECT db, name FROM schema WHERE type = 'table' ORDER BY rowid").fetchall():
        if name in {t for _, t, _ in FULL.values()}:
            continue
        cols = [r[1] for r in cons[db].execute(f'PRAGMA table_info("{name}")')]
        quoted = ", ".join(f'"{c}"' for c in cols)
        rows = lib.con.execute(f'SELECT {quoted} FROM "{name}"').fetchall()
        cons[db].executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(cols))})', rows)
    for kind, (db, table, cols) in FULL.items():
        for text, div in lib.places(kind):
            cons[db].executemany(f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
                                 lib.full_rows(kind, text, div))
    for con in cons.values():
        con.commit()
        con.close()
    return targets


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    for path in restore(sys.argv[1], sys.argv[2]).values():
        print(path)
