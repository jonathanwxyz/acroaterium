#!/usr/bin/env python3
"""Sanity-check verse timings (verse_timings) against the pauses in each recording.

    python3 scripts/check_timings.py [book ...]

A correctly placed verse start falls where speech resumes after a pause, so for each
book this reports the share of verse starts at a pause (Book 1: ~96%; the rest are
run-on lines read without one), the spoken title's length, and verses squeezed to
under a second (the sign of an aligner that lost its place). Pauses are detected with
ffmpeg's silencedetect and cached in cache/<file>.silences.txt next to the recording.
"""
import re
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "iliad.sqlite"
RECORDINGS = ROOT / "recordings"
MODEL = "mms"


def pauses(recording):
    """[(start, end)] of silences of 0.15 s or more in the recording."""
    path = RECORDINGS / recording  # e.g. Iliad/iliad01.m4a
    cache = path.parent / "cache" / f"{path.name}.silences.txt"
    if not cache.exists():
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", "silencedetect=noise=-35dB:d=0.15", "-f", "null", "-"],
            capture_output=True, text=True).stderr
        cache.parent.mkdir(exist_ok=True)
        cache.write_text("\n".join(re.findall(r"silence_(?:start|end): [0-9.]+", out)))
    spans, start = [], None
    for kind, value in re.findall(r"silence_(start|end): ([0-9.]+)", cache.read_text()):
        if kind == "start":
            start = float(value)
        elif start is not None:
            spans.append((start, float(value)))
            start = None
    return spans


def main():
    con = sqlite3.connect(DB)
    books = [int(b) for b in sys.argv[1:]] or [b for (b,) in con.execute(
        "SELECT DISTINCT book FROM verse_timings WHERE model = ? ORDER BY book", (MODEL,))]
    print("book  recording       verses  at pause  median off  title     short (<1 s)")
    for book in books:
        rec = con.execute("SELECT recording FROM verse_timings WHERE book = ? AND model = ? "
                          "GROUP BY recording ORDER BY recording LIKE '%.m4a' DESC LIMIT 1",
                          (book, MODEL)).fetchone()
        if not rec:
            print(f"{book:4}  (no timings)")
            continue
        rows = con.execute("SELECT line, start, speech_end, end FROM verse_timings "
                           "WHERE book = ? AND model = ? AND recording = ? ORDER BY start",
                           (book, MODEL, rec[0])).fetchall()
        sil = pauses(rec[0])
        ends = [e for _, e in sil]
        at_pause = sum(1 for _, a, _, _ in rows if any(s - 0.05 <= a <= e + 0.25 for s, e in sil))
        off = statistics.median(min(abs(a - e) for e in ends) for _, a, _, _ in rows)
        title = next(((e - a) for line, a, e, _ in rows if line == 0), None)
        short = [line for line, a, _, b in rows if b - a < 1.0]
        print(f"{book:4}  {rec[0]:14} {len(rows):6}  {at_pause / len(rows):7.1%}  {off:9.2f}s  "
              f"{f'{title:.1f}s' if title is not None else '-':8}  "
              f"{len(short)}{' e.g. ' + ', '.join(map(str, short[:6])) if short else ''}")


if __name__ == "__main__":
    main()
