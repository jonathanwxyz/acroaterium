#!/usr/bin/env python3
"""Export the library to one compact file, data/library_compact.sqlite: everything in
data/library.sqlite and data/library_audio.sqlite (the texts, translations, notes, every
word's parsing, the timings), losslessly, in a fraction of the space, for an app to keep on
the device. The server goes on using the full databases; this is the last step of a
rebuild (data/README.md). Hidden texts (texts.hidden) are left out, every row of them: an
app gets only what the library lists.

    python3 scripts/compact_library.py            # writes it, then checks it (below)
    python3 scripts/compact_library.py --check    # only checks the one there
    python3 scripts/compact_library.py --no-check

Where the space goes in the full databases is mostly repetition: every word's row repeats
its place (text, book, segment) and its lemma, gloss and morphology code as text, and each
table has an index besides. Here a book's rows of each kind are one compressed chunk, and
what repeats from word to word (a lemma, a code) is stored once, in dicts, and referred to
by number.

Format (scripts/compact_read.py reads it; an app's decoder follows that):

  schema(db, type, name, sql)
      the full databases' CREATE statements (db: "library" or "audio"), to rebuild them.

  The small tables, as they are, under their own names and with the same columns:
      texts, divisions, categories, settings, note_sources, translation_credits,
      lexicon, lemma_defs (library.sqlite) and recordings (library_audio.sqlite).

  chunks(kind TEXT, text TEXT, div INTEGER, rows BLOB), primary key (kind, text, div)
      (an ordinary rowid table with 1 KB pages: chunks are kilobytes each)
      A book's (division's) rows of one kind. rows: zlib (RFC 1950, as Python's
      zlib.compress, Dart's ZLibCodec) of UTF-8 JSON: an array of rows, each an array of
      the kind's columns (compact_read.CHUNK_COLUMNS), in the full table's order (by rowid
      within the book). JSON keeps the values exactly: integers as integers, reals as
      reals (shortest round-trip form), null as null.
        segments              seq, ref, content, speaker, para
        translations          seq, lang, content
        translation_passages  seq_from, seq_to, lang, content
        notes                 pos_from, pos_to, source, seq, body?, parts
        words                 seq, word_index, strongs, words.morph_id, words.lemma_id
        treebank              seq, word_index, part, treebank.lemma_id, treebank.postag_id,
                              treebank.relation_id, treebank.head_id, form, match
        auto_words            seq, word_index, auto.lemma_id, auto.morph_id, confidence,
                              alternative
        paraphrase_links      seq, word_index, para_seq, para_index, para_word
        units                 seq, piece, content?, start, speech_end, end, score, cut
      A column "<kind>_id" is the id of a value in dicts (below). "content?" (units): null
      where it's the same as its segment's content (every line of verse; prose's
      sentences are stored). "body?" (notes): null where it's the note's parts' texts
      joined (parts: JSON [[style, text], ...], kept as it was written).

  dicts(kind TEXT, id INTEGER, value TEXT), primary key (kind, id)
      Values shared by many words, as JSON: a string, or null, or for a kind standing for
      several columns an array of them:
        words.morph       morph (Robinson's or CATSS's code)
        words.lemma       [lemma, gloss] (the word's own, where the lexicon hasn't it)
        treebank.lemma    lemma            treebank.postag   postag (9 letters)
        treebank.relation relation         treebank.head     head (a word, "" for the root)
        auto.lemma        [lemma, headword, definition]
        auto.morph        [pos, morph]

  Its version: the start of the SHA-256 of what it holds (content_version), 16 hex digits,
  in the file's header: the first 8 as its application_id, the last 8 as its user_version
  (big-endian, bytes 68-71 and 60-63). The same library built anywhere has the same
  version, though zlib (zlib-ng or not) may compress its chunks to other bytes; serve.py
  gives it as /api/compact's X-Version, and the app's build reads it for its own copy.

The check restores the full databases from the compact one (compact_read.restore) and
compares every row of every table with the originals (less the hidden texts'), then builds the API's JSON (with
serve.py's own functions) from both for a broad sample and compares that too.
"""
import argparse
import hashlib
import json
import random
import sqlite3
import sys
import tempfile
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compact_read import CHUNK_COLUMNS, DICTS, FULL, restore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "data" / "library.sqlite"
LIBRARY_AUDIO = ROOT / "data" / "library_audio.sqlite"
OUT = ROOT / "data" / "library_compact.sqlite"
SOURCES = {"library": LIBRARY, "audio": LIBRARY_AUDIO}


def read_only(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def hidden_texts():
    """The slugs of the texts the library doesn't list (texts.hidden), which stay out."""
    con = read_only(LIBRARY)
    cols = {row[1] for row in con.execute("PRAGMA table_info(texts)")}
    hidden = {s for (s,) in con.execute("SELECT slug FROM texts WHERE hidden = 1")} if "hidden" in cols else set()
    con.close()
    return hidden


def not_hidden(name, cols, hidden):
    """SQL selecting a table's rows that aren't a hidden text's (its "text" column, the
    texts table's "slug"), with its parameters; all rows of a table not of a text."""
    col = "slug" if name == "texts" else "text" if "text" in cols else None
    if not col or not hidden:
        return "1", []
    return f'"{col}" NOT IN ({", ".join("?" * len(hidden))})', sorted(hidden)


def pack(rows):
    return zlib.compress(json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"), 9)


def content_version(con):
    """The start of the SHA-256 of every table's rows, in order, the chunks' as they are
    uncompressed: the same for the same library, whatever zlib made of it."""
    digest = hashlib.sha256()
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                               "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")')]
        digest.update(f"{name}{cols!r}".encode())
        order = ", ".join(f'"{c}"' for c in cols)
        for row in con.execute(f'SELECT {order} FROM "{name}" ORDER BY {order}'):
            if name == "chunks":
                row = (*row[:3], zlib.decompress(row[3]))
            digest.update(repr(row).encode())
    return digest.hexdigest()[:16]


def signed32(hex8):
    """8 hex digits as the signed 32-bit integer SQLite keeps in its header."""
    n = int(hex8, 16)
    return n - (1 << 32) if n >= 1 << 31 else n


def build(out=OUT):
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    dst = sqlite3.connect(tmp)
    # (Small pages: a book's chunk is a few kilobytes, and fills its last page only partly.)
    dst.execute("PRAGMA page_size = 1024")
    dst.execute("CREATE TABLE schema (db TEXT NOT NULL, type TEXT NOT NULL, name TEXT NOT NULL, sql TEXT NOT NULL)")
    dst.execute("CREATE TABLE chunks (kind TEXT NOT NULL, text TEXT NOT NULL, div INTEGER NOT NULL, rows BLOB NOT NULL, "
                "PRIMARY KEY (kind, text, div))")  # (not WITHOUT ROWID: its rows are large)
    dst.execute("CREATE TABLE dicts (kind TEXT NOT NULL, id INTEGER NOT NULL, value TEXT, "
                "PRIMARY KEY (kind, id)) WITHOUT ROWID")
    chunked = {table for _, table, _ in FULL.values()}
    srcs = {db: read_only(path) for db, path in SOURCES.items()}
    hidden = hidden_texts()
    # The schemas, and the small tables as they are.
    for db, src in srcs.items():
        for type_, name, sql in src.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
                                            "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, rowid"):
            dst.execute("INSERT INTO schema VALUES (?, ?, ?, ?)", (db, type_, name, sql))
            if type_ == "table" and name not in chunked:
                dst.execute(sql)
                cols = [r[1] for r in src.execute(f'PRAGMA table_info("{name}")')]
                where, params = not_hidden(name, cols, hidden)
                rows = src.execute(f'SELECT * FROM "{name}" WHERE {where} ORDER BY rowid', params).fetchall()
                dst.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(cols))})', rows)
    # The big tables, a chunk per book.
    ids = {kind: {} for kind in DICTS}

    def intern(kind, value):
        d = ids[kind]
        if value not in d:
            d[value] = len(d)
        return d[value]

    segments = {}  # (text, div, seq) -> content, for the units
    where, params = not_hidden("segments", ["text"], hidden)
    for row in srcs["library"].execute(f"SELECT text, div, seq, content FROM segments WHERE {where}", params):
        segments[row[:3]] = row[3]
    for kind, (db, table, cols) in FULL.items():
        chunks = {}
        where, params = not_hidden(table, ["text"], hidden)
        for row in srcs[db].execute(f'SELECT {", ".join(cols)} FROM "{table}" WHERE {where} ORDER BY text, div, rowid',
                                    params):
            r = dict(zip(cols, row))
            out_row = []
            for name in CHUNK_COLUMNS[kind]:
                if name.endswith("_id"):
                    dkind = name[:-3]
                    value = tuple(r[c] for c in DICTS[dkind])
                    out_row.append(intern(dkind, value if len(value) > 1 else value[0]))
                elif name == "content?":  # (units) as its segment's: left out
                    same = segments.get((r["text"], r["div"], r["seq"])) == r["content"]
                    out_row.append(None if same else r["content"])
                elif name == "body?":  # (notes) the parts' texts joined: left out
                    joined = r["parts"] is not None and "".join(t for _, t in json.loads(r["parts"])) == r["body"]
                    out_row.append(None if joined else r["body"])
                else:
                    out_row.append(r[name])
            chunks.setdefault((r["text"], r["div"]), []).append(out_row)
        dst.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?)",
                        [(kind, text, div, pack(rows)) for (text, div), rows in chunks.items()])
    for kind, values in ids.items():
        dst.executemany("INSERT INTO dicts VALUES (?, ?, ?)",
                        [(kind, i, json.dumps(list(v) if isinstance(v, tuple) else v, ensure_ascii=False))
                         for v, i in values.items()])
    for src in srcs.values():
        src.close()
    dst.commit()
    version = content_version(dst)
    dst.execute(f"PRAGMA application_id = {signed32(version[:8])}")
    dst.execute(f"PRAGMA user_version = {signed32(version[8:])}")
    dst.execute("VACUUM")
    dst.close()
    tmp.replace(out)
    return out


def sizes(path):
    con = read_only(path)
    by = {}
    for name, size in con.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"):
        by[name] = size
    kinds = dict(con.execute("SELECT kind, SUM(length(rows)) FROM chunks GROUP BY kind"))
    con.close()
    return by, kinds


def check(out=OUT, samples=400, seed=1):
    """Restore the full databases from the compact one and compare: every table, row for
    row (the hidden texts' left out); then the API's JSON for a sample (of texts listed).
    True if all is the same."""
    ok = True
    hidden = hidden_texts()
    with tempfile.TemporaryDirectory() as tmp:
        restored = restore(out, tmp)
        rest = {"library": restored["library"], "audio": restored["audio"]}
        # 1. Every table, row for row (values and their types).
        for db, path in SOURCES.items():
            a, b = read_only(path), read_only(rest[db])
            for (name,) in a.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
                cols = [r[1] for r in a.execute(f'PRAGMA table_info("{name}")')]
                order = ", ".join(f'"{c}"' for c in cols)
                where, params = not_hidden(name, cols, hidden)
                ra = [repr(r) for r in a.execute(f'SELECT * FROM "{name}" WHERE {where} ORDER BY {order}', params)]
                rb = [repr(r) for r in b.execute(f'SELECT {order} FROM "{name}" ORDER BY {order}')]
                same = ra == rb
                ok &= same
                print(f"  {db}.{name}: {len(ra)} rows {'identical' if same else 'DIFFER'}"
                      + ("" if same else f" ({len(rb)} restored)"))
            for type_ in ("index",):
                ia = {n for (n,) in a.execute("SELECT name FROM sqlite_master WHERE type = ? AND sql IS NOT NULL", (type_,))}
                ib = {n for (n,) in b.execute("SELECT name FROM sqlite_master WHERE type = ? AND sql IS NOT NULL", (type_,))}
                if ia != ib:
                    ok = False
                    print(f"  {db}: indexes differ: {sorted(ia ^ ib)}")
            a.close()
            b.close()
        # 2. The API's JSON, as serve.py builds it, from the full databases and the restored.
        import serve
        rng = random.Random(seed)
        con = read_only(LIBRARY)
        texts = [s for (s,) in con.execute("SELECT slug FROM texts ORDER BY sort") if s not in hidden]
        divs = {s: [n for (n,) in con.execute("SELECT DISTINCT div FROM segments WHERE text = ? ORDER BY div", (s,))]
                for s in texts}
        calls = [("library_json", ())]
        for s in texts:
            ns = divs[s]
            picks = ns if s == "iliad" else sorted({ns[0], rng.choice(ns)}) if ns else []
            for n in picks:
                calls.append(("text_json", (s, n)))
                calls.append(("offline_json", (s, n)))
        calls += [("book_json", (b,)) for b in range(1, 25)]
        calls += [("words_json", ("iliad", 1))]
        where, params = not_hidden("", ["text"], hidden)
        for table in ("words", "treebank", "auto_words"):
            rows = con.execute(f"SELECT DISTINCT text, div, seq, word_index FROM {table} WHERE {where} "
                               "ORDER BY random() LIMIT ?", (*params, samples)).fetchall()
            calls += [("lexword_json", r) for r in rows]
        for text, div, a_, b_ in con.execute(f"SELECT text, div, pos_from, pos_to FROM notes WHERE {where} "
                                             "ORDER BY random() LIMIT ?", (*params, samples // 2)).fetchall():
            calls.append(("notes_json", (text, div, a_, b_)))
        calls += [("scholia_json", (b, rng.randint(1, 400))) for b in range(1, 25)]
        con.close()

        def run(paths):
            serve.LIBRARY, serve.LIBRARY_AUDIO = paths["library"], paths["audio"]
            return [json.dumps(getattr(serve, f)(*args), ensure_ascii=False, sort_keys=True) for f, args in calls]

        original = run({"library": LIBRARY, "audio": LIBRARY_AUDIO})
        again = run({"library": Path(rest["library"]), "audio": Path(rest["audio"])})
        serve.LIBRARY, serve.LIBRARY_AUDIO = LIBRARY, LIBRARY_AUDIO
        differ = [(calls[i], original[i][:200], again[i][:200]) for i in range(len(calls)) if original[i] != again[i]]
        counts = {}
        for f, _ in calls:
            counts[f] = counts.get(f, 0) + 1
        print(f"  API JSON: {len(calls)} calls ({', '.join(f'{f} {n}' for f, n in counts.items())}): "
              + ("identical" if not differ else f"{len(differ)} DIFFER"))
        for d in differ[:10]:
            print("   ", d)
        ok &= not differ
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only check the compact file there")
    ap.add_argument("--no-check", action="store_true", help="write it without checking")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    if not args.check:
        t = time.time()
        build(args.out)
        print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB) in {time.time() - t:.0f} s")
        by, kinds = sizes(args.out)
        full = sum(p.stat().st_size for p in SOURCES.values())
        print(f"  (the full databases: {full / 1e6:.1f} MB)")
        for kind, size in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"  {kind:22} {size / 1e6:6.1f} MB")
        print(f"  {'dicts':22} {by.get('dicts', 0) / 1e6:6.1f} MB")
        small = sum(v for k, v in by.items() if k not in ("chunks", "dicts", "schema"))
        print(f"  {'small tables':22} {small / 1e6:6.1f} MB")
    if not args.no_check:
        print("checking:")
        if not check(args.out):
            sys.exit("the compact library does NOT reproduce the full databases")
        print("the compact library reproduces the full databases exactly")


if __name__ == "__main__":
    main()
