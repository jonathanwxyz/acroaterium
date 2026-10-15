#!/usr/bin/env python3
"""Automatic parsing for the words that have no gold parsing (no treebank, no CATSS or
Robinson): each word's lemma, part of speech and morphology, with its short definition,
from the lemmatiser in ~/code/lemmatisation (Morpheus's candidates, a GreBERTa model
choosing the lemma in context, Dilemma's tagger choosing the analysis; see its README).
Run with that project's environment:

    ~/code/lemmatisation/venv/bin/python scripts/lemmatise_library.py crito phaedrus ...
    LEMMATISATION=/path/to/lemmatisation ... (if it's elsewhere)

Only the sentences with words that lack gold parsing are lemmatised (the whole sentence,
for context): the words go to the lemmatiser as they stand in the library, so each result
belongs to one word, by its place in its segment. Results go to
data/raw/lemmatised/<slug>.json, each segment with the text it was made from: a division
whose text hasn't changed isn't done again. scripts/library.py takes them into the
library (auto_words) for the words without gold parsing.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "data" / "library.sqlite"
OUT = ROOT / "data" / "raw" / "lemmatised"
LEMMATISATION = Path(os.environ.get("LEMMATISATION", Path.home() / "code" / "lemmatisation"))
sys.path.insert(0, str(LEMMATISATION))
from lemeval.lemmatise import GreekLemmatiser, tokenise  # noqa: E402

SENTENCE_END = {".", ";", "·", ";", "!", "?"}
KEEP = ("lemma", "headword", "definition", "pos", "morph", "confidence", "source", "alternatives", "homographs")


def gold_words(con, slug):
    """{(div, seq, word index)} of a text's words with gold parsing."""
    out = set()
    for table in ("words", "treebank"):
        out |= set(con.execute(f"SELECT div, seq, word_index FROM {table} WHERE text = ?", (slug,)))
    return out


def lemmatise_division(lem, rows, wanted):
    """{seq: {word index: record}} for a division's segments (rows: (seq, content)): the
    sentences with a word wanted ((seq, word index): no gold parsing), whole."""
    sentences, places, cur, cur_places = [], [], [], []
    for seq, content in rows:
        for i, chunk in enumerate(content.split()):
            for tok in (t for s in tokenise(chunk) for t in s):
                cur.append(tok)
                cur_places.append((seq, i))
                if tok in SENTENCE_END:
                    sentences.append(cur)
                    places.append(cur_places)
                    cur, cur_places = [], []
    if cur:
        sentences.append(cur)
        places.append(cur_places)
    keep = [k for k, where in enumerate(places) if any(p in wanted for p in where)]
    sentences, places = [sentences[k] for k in keep], [places[k] for k in keep]
    out = {}
    for sent, where in zip(lem.lemmatise(sentences) if sentences else [], places):
        for rec, (seq, i) in zip(sent, where):
            if rec["source"] == "punctuation" or i in out.setdefault(seq, {}):
                continue  # (a word's first Greek token speaks for it)
            out[seq][i] = {k: rec.get(k) for k in KEEP}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--all-words", action="store_true", help="also the words with gold parsing")
    args = ap.parse_args()
    con = sqlite3.connect(LIBRARY)
    lem = None
    for slug in args.slugs:
        path = OUT / f"{slug}.json"
        done = json.loads(path.read_text()) if path.exists() else {}
        gold = set() if args.all_words else gold_words(con, slug)
        divs = {}
        for div, seq, content in con.execute(
                "SELECT div, seq, content FROM segments WHERE text = ? AND seq > 0 ORDER BY div, seq", (slug,)):
            divs.setdefault(div, []).append((seq, content))
        t0, words = time.time(), 0
        for div, rows in divs.items():
            # Not wanted (every word has gold parsing), or done already from this text.
            if all((div, seq, i) in gold for seq, c in rows for i in range(len(c.split()))):
                continue
            if all(done.get(f"{div}:{seq}", {}).get("text") == c for seq, c in rows):
                continue
            lem = lem or GreekLemmatiser(verbose=False)
            wanted = {(seq, i) for seq, c in rows for i in range(len(c.split())) if (div, seq, i) not in gold}
            result = lemmatise_division(lem, rows, wanted)
            for seq, c in rows:
                done[f"{div}:{seq}"] = {"text": c, "words": {str(i): r for i, r in sorted(result.get(seq, {}).items())}}
            words += sum(len(r) for r in result.values())
            print(f"  {slug} {div}: {len(rows)} segments", flush=True)
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(done, ensure_ascii=False))
        took = time.time() - t0
        print(f"{slug}: {words} words lemmatised in {took:.0f} s -> {path.relative_to(ROOT)}"
              + (f" ({words / took:.0f} words/s)" if words else ""), flush=True)


if __name__ == "__main__":
    main()
