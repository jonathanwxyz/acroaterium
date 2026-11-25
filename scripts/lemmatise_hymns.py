#!/usr/bin/env python3
"""Automatic parsing of the days' hymns (hymns in library.sqlite, scripts/import_hymns.py),
as lemmatise_library.py does a text's: each day of the service books a division (its
number, hymn_days.n), each hymn a segment (its seq). Run with the lemmatiser's environment:

    ~/code/lemmatisation/venv/bin/python scripts/lemmatise_hymns.py [--redo]

Results go to data/raw/lemmatised/hymns.json ("n:seq": the hymn's text and its words); a
hymn whose text hasn't changed isn't done again (unless --redo). Then run
scripts/import_hymns.py again: it takes them in (hymn_words) for the word cards.
"""
import argparse
import json
import sqlite3
import time

from lemmatise_library import LIBRARY, OUT, ROOT, GreekLemmatiser, lemmatise_division

SLUG = "hymns"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--redo", action="store_true", help="lemmatise again what was done already")
    args = ap.parse_args()
    path = OUT / f"{SLUG}.json"
    done = json.loads(path.read_text()) if path.exists() and not args.redo else {}
    con = sqlite3.connect(LIBRARY)
    divs = {}
    for n, seq, text in con.execute("SELECT d.n, h.seq, h.text FROM hymns h JOIN hymn_days d USING (day) "
                                    "ORDER BY d.n, h.seq"):
        divs.setdefault(n, []).append((seq, text))
    con.close()
    lem, t0, words = None, time.time(), 0
    OUT.mkdir(parents=True, exist_ok=True)
    for k, (n, rows) in enumerate(divs.items()):
        if all(done.get(f"{n}:{seq}", {}).get("text") == text for seq, text in rows):
            continue
        lem = lem or GreekLemmatiser(verbose=False)
        result = lemmatise_division(lem, rows, {(seq, i) for seq, text in rows for i in range(len(text.split()))})
        for seq, text in rows:
            done[f"{n}:{seq}"] = {"text": text, "words": {str(i): r for i, r in sorted(result.get(seq, {}).items())}}
        words += sum(len(r) for r in result.values())
        print(f"  {n}: {len(rows)} hymns ({k + 1}/{len(divs)})", flush=True)
        if k % 20 == 0:  # (kept as it goes: a long run stopped loses little)
            path.write_text(json.dumps(done, ensure_ascii=False))
    path.write_text(json.dumps(done, ensure_ascii=False))
    took = time.time() - t0
    print(f"{SLUG}: {words} words lemmatised in {took:.0f} s -> {path.relative_to(ROOT)}"
          + (f" ({words / took:.0f} words/s)" if words else ""), flush=True)


if __name__ == "__main__":
    main()
