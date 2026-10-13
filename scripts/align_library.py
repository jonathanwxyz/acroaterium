#!/usr/bin/env python3
"""Align recordings of library texts (data/library.sqlite) to their text, and give the
reader one audio file per book.

    .venv/bin/python scripts/align_library.py [SLUG ...] [--book N] [--no-audio]

Which recordings go with which book is in the local catalogue (library/catalogue.json),
an "audio" list on a text:

    "audio": [
      {"div": 1, "from": ["Work pt1", "Work pt2"], "credit": "Recording: …"},
      {"div": "2-24", "from": ["Playlist"], "credit": "Recording: …"}]

"from" names folders under recordings/ (as make_emissions.py --links made them: audio
files in reading order, their emissions in cache/<file>.mms.npy). With one book ("div":
1), all the audio files of those folders, in order, are joined into one recording. With
a range ("div": "2-24"), each book gets one file of the folder: the Nth file (in name
order) for book N, so a playlist of every book can start later (here, at book 2). With
"div": "all" and "file": N, the Nth file of the folder holds every division (a book of
the Bible read through, its chapters the divisions): it is aligned in one pass, and each
chapter plays from the same file; "file" may instead be the start of the file's name
("021", where a playlist has gaps). "order" gives the order the recording reads the
chapters in, where it isn't theirs: "reverse" (last to first, each one forwards), or a
list of them all. "start" and "end" ("2:1", "6:34") mark where a recording that doesn't
read the whole book begins and stops: verses outside get no timings.

The recordings often hold more than the text: an introduction or LibriVox's
announcements at the start and end of each part, a spoken title, a translation read
after each passage. So between any two units of text (and before the first and after
the last) the alignment may pass over audio that matches nothing, at a fixed cost per
frame (STAR_COST): speech that matches the text costs far less, speech that doesn't
far more.

Units: a line of verse; in prose, each sentence (or the part of one within a Stephanus
section: segments break at sections, and sentences carry across them). They are stored,
with their text, in data/library_audio.sqlite, which a rebuild of library.sqlite leaves
alone (realign after a rebuild that changes a text):

  recordings (text, div, file, credit, frames): the reader's audio file, a path under
      recordings/: the recording itself when a book has one file; parts are joined
      into recordings/<slug>-<div>.<ext> by stream copy (as they are, not re-encoded)
  units (text, div, seq, piece, content, start, speech_end, end, score): seq is the
      segment in library.sqlite, piece the sentence within it (0 for verse); times in
      seconds (start: first sound; speech_end: last sound; end: the next unit's start);
      score the mean log-probability per frame along the path (higher = surer)

Needs uroman (the text is romanized for the MMS aligner's vocabulary), numpy, ffmpeg;
the emissions come from make_emissions.py, no model is run here.
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "data" / "library.sqlite"
OUT = ROOT / "data" / "library_audio.sqlite"
RECORDINGS = ROOT / "recordings"
CATALOGUE = ROOT / "library" / "catalogue.json"
MODEL = "mms"
FRAME_S = 0.02
AUDIO_EXT = {".mp3", ".m4a", ".webm", ".opus", ".ogg", ".wav", ".flac", ".mp4", ".mkv", ".aac"}

# The MMS forced aligner's characters (MahmoudAshraf/mms-300m-1130-forced-aligner).
VOCAB = {c: i for i, c in enumerate(["<blank>", "<pad>", "</s>", "<unk>", *"aienoutsrmkldgyhbpwcvjzf'qx"])}
BLANK = 0

STAR_COST = -3.0            # log-probability per frame of audio that matches no text
SKIP_COST = -4.0            # per letter, to pass over a unit the recording doesn't read (a list
                            # of names left out, a chapter missing from the upload): reading
                            # it costs far less when it's there, and forcing it far more when not
STRAY_GAP = 100             # frames (2 s): letters at either end of a unit (up to a quarter of
                            # it) this far from the rest are stray matches in the audio around it
                            # (a spoken title, a closing announcement), not counted in its time
WINDOW, KEEP = 30, 24       # units aligned per window / accepted from it
SENTENCE_END = re.compile(r"(?<=[.;·;·!?])\s+")


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------- the text

def units_of(con, slug, div, form):
    """(seq, piece, text) for each unit of a division, in reading order."""
    rows = con.execute("SELECT seq, content FROM segments WHERE text = ? AND div = ? ORDER BY seq",
                       (slug, div)).fetchall()
    out = []
    for seq, content in rows:
        if form == "verse":
            out.append((seq, 0, content))
            continue
        pieces = [p for p in SENTENCE_END.split(content.strip()) if p]
        # A scrap (a lone "ναί.") is joined to the piece before it.
        merged = []
        for p in pieces:
            if merged and len(romanize_letters(p)) < 6:
                merged[-1] += " " + p
            else:
                merged.append(p)
        out.extend((seq, k, p) for k, p in enumerate(merged))
    return out


_uroman = None


def romanize_letters(text):
    global _uroman
    if _uroman is None:
        import uroman
        _uroman = uroman.Uroman()
    roman = _uroman.romanize_string(text, lcode="ell").lower()
    return [c for c in roman if c in VOCAB and c != "'"]


def tokens_of(text):
    return [VOCAB[c] for c in romanize_letters(text)]


# ------------------------------------------------------------------- alignment

def ctc_align(lp, units):
    """Viterbi CTC alignment of units (token lists) to all frames of lp, where the blank
    between two units, and before the first and after the last, may instead take any
    audio at STAR_COST a frame, and a unit may be passed over at SKIP_COST a letter.
    Returns, per unit, its (first frame, frame after its last sound), or None if passed
    over, and the path's per-frame score."""
    tokens, boundary = [], []  # boundary[k]: the blank before token k is between units
    for u in units:
        for j, t in enumerate(u):
            tokens.append(t)
            boundary.append(j == 0)
    T, L = len(lp), len(tokens)
    S = 2 * L + 1
    star = np.maximum(lp[:, BLANK], STAR_COST)
    ext = np.concatenate([lp, star[:, None]], axis=1)  # column V: blank-or-anything
    V = lp.shape[1]
    labels = np.full(S, BLANK)
    labels[1::2] = tokens
    labels[0] = labels[-1] = V
    for k in range(L):
        if boundary[k]:
            labels[2 * k] = V
    skip = np.zeros(S, bool)
    skip[3::2] = np.array(tokens[1:]) != np.array(tokens[:-1])
    bounds = np.cumsum([0] + [len(u) for u in units])
    B = 2 * bounds  # the blank (boundary) state before each unit, and the final one
    pass_over = SKIP_COST * np.diff(bounds)
    NEG = -1e30
    alpha = np.full(S, NEG)
    alpha[0], alpha[1] = ext[0, labels[0]], ext[0, labels[1]]
    back = np.zeros((T, S), np.int8)
    for t in range(1, T):
        step = np.concatenate(([NEG], alpha[:-1]))
        jump = np.where(skip, np.concatenate(([NEG, NEG], alpha[:-2])), NEG)
        over = np.full(S, NEG)
        over[B[1:]] = alpha[B[:-1]] + pass_over  # from before a unit to after it
        best = np.maximum(np.maximum(alpha, step), np.maximum(jump, over))
        back[t] = np.where(best == alpha, 0, np.where(best == step, 1, np.where(best == jump, 2, 3)))
        alpha = best + ext[t, labels]
    s = S - 1 if alpha[S - 1] >= alpha[S - 2] else S - 2
    first, last = np.zeros(L, int), np.full(L, -1)
    before = {int(b): int(a) for a, b in zip(B[:-1], B[1:])}  # boundary after a unit -> before it
    score = np.zeros(T)
    for t in range(T - 1, -1, -1):
        score[t] = ext[t, labels[s]]
        if s % 2:
            first[s // 2] = t
            if last[s // 2] < 0:
                last[s // 2] = t
        s = before[s] if back[t, s] == 3 else s - int(back[t, s])
    spans = []
    for k in range(len(units)):
        a, b = bounds[k], bounds[k + 1] - 1
        if last[b] < 0:  # passed over
            spans.append(None)
            continue
        edge = max(1, (b - a + 1) // 4)  # a stray run is a few letters at either end
        for j in range(a, min(a + edge, b)):  # the latest wide gap among the first letters
            if first[j + 1] - last[j] > STRAY_GAP:
                a = j + 1
        for j in range(b, max(b - edge, a), -1):  # the earliest among the last letters
            if first[j] - last[j - 1] > STRAY_GAP:
                b = j - 1
        spans.append((first[a], last[b] + 1))
    return spans, score


def align(lp, units):
    """(start, speech_end, end, score) in frames for each unit, or None for one passed
    over, aligning a window of units at a time; each window starts where the last unit
    read in the one before ended."""
    pace = len(lp) / max(1, sum(map(len, units)))  # frames per token, intros and all
    out, i, f0 = [], 0, 0
    while i < len(units):
        win = units[i:i + WINDOW]
        final = i + WINDOW >= len(units)
        n_tok = sum(map(len, win))
        f1 = len(lp) if final else min(len(lp), f0 + int(2.2 * pace * n_tok) + 1500)
        spans, score = ctc_align(lp[f0:f1], win)
        keep = len(win) if final else KEEP
        for k in range(keep):
            if spans[k] is None:
                out.append(None)
                continue
            a, e = spans[k]
            out.append([f0 + a, f0 + e, None, float(score[a:e].mean()) if e > a else -99.0])
        i += keep
        f0 = next((u[1] for u in reversed(out) if u), f0)  # after the last unit read's last sound
    read = [u for u in out if u]
    for k, u in enumerate(read):  # end: the next unit's start (pause included)
        u[2] = read[k + 1][0] if k + 1 < len(read) else len(lp)
    return out


# ------------------------------------------------------------------- recordings

def audio_files(folder):
    d = RECORDINGS / folder
    if not d.is_dir():
        sys.exit(f"recordings/{folder}: no such folder")
    return sorted(p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT)


def emissions_of(path):
    npy = path.parent / "cache" / f"{path.name}.{MODEL}.npy"
    if not npy.exists():
        sys.exit(f"{npy.relative_to(ROOT)}: missing (run make_emissions.py on {path.relative_to(ROOT)})")
    return np.load(npy)


def plan(entry, divisions):
    """{divs: [audio files]} for one "audio" entry of the catalogue: divs is a tuple of
    the divisions the files hold (one, or all of a text's)."""
    files = [f for folder in entry["from"] for f in audio_files(folder)]
    div = entry["div"]
    if div == "all":
        n = entry["file"]
        if isinstance(n, str):
            named = [f for f in files if f.name.startswith(n)]
            if len(named) != 1:
                sys.exit(f"{entry['from']}: {len(named)} files start with {n!r}")
            file = named[0]
        elif 1 <= n <= len(files):
            file = files[n - 1]
        else:
            sys.exit(f"{entry['from']}: no file {n} (it has {len(files)})")
        order = entry.get("order")
        if isinstance(order, list):
            if sorted(order) != sorted(divisions):
                sys.exit(f"{entry['from']} {n}: \"order\" must list every division once")
            divs = tuple(order)
        else:
            divs = tuple(sorted(divisions, reverse=order == "reverse"))
        return {divs: [file]}  # (with start/end, see main)
    if isinstance(div, int):
        return {(div,): files}
    a, b = map(int, str(div).split("-"))
    out = {}
    for n in range(a, b + 1):
        if n not in divisions:
            continue
        if n > len(files):
            sys.exit(f"{entry['from']}: no file for book {n} (it has {len(files)})")
        out[(n,)] = [files[n - 1]]
    return out


def reader_audio(parts, slug, div, make):
    """The reader's file for a book, as a path under recordings/: the recording itself, or
    its parts joined end to end by stream copy (the audio as it is, not re-encoded; the
    parts of one work share a format). make=False only names it."""
    if len(parts) == 1:
        return parts[0].relative_to(RECORDINGS).as_posix()
    ext = parts[0].suffix
    if any(p.suffix != ext for p in parts):
        sys.exit(f"{slug} {div}: parts in different formats ({', '.join(p.name for p in parts)}) can't be joined as they are")
    out = RECORDINGS / f"{slug}-{div:02d}{ext}"
    if make:
        listing = out.with_suffix(".parts.txt")
        quoted = (p.as_posix().replace("'", "'\\''") for p in parts)  # ffmpeg's concat list quoting
        listing.write_text("".join(f"file '{q}'\n" for q in quoted))
        tmp = out.with_name(out.stem + ".tmp" + ext)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-map", "0:a", "-c", "copy", str(tmp)], check=True)
        tmp.replace(out)
        listing.unlink()
    return out.relative_to(RECORDINGS).as_posix()


# ------------------------------------------------------------------- main

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    text   TEXT NOT NULL,
    div    INTEGER NOT NULL,
    file   TEXT NOT NULL,     -- under recordings/
    credit TEXT,
    frames INTEGER,           -- length of the aligned audio, 20 ms frames
    PRIMARY KEY (text, div));
CREATE TABLE IF NOT EXISTS units (
    text       TEXT NOT NULL,
    div        INTEGER NOT NULL,
    seq        INTEGER NOT NULL,   -- segments.seq in library.sqlite
    piece      INTEGER NOT NULL,   -- sentence within the segment (0 for verse)
    content    TEXT NOT NULL,
    start      REAL NOT NULL,
    speech_end REAL NOT NULL,
    end        REAL NOT NULL,
    score      REAL,
    PRIMARY KEY (text, div, seq, piece));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*", help="texts to align (default: every text with \"audio\")")
    ap.add_argument("--book", type=int, help="only this book")
    ap.add_argument("--no-audio", action="store_true", help="don't (re)join multi-part recordings")
    args = ap.parse_args()

    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    texts = [t for t in catalogue["texts"] if t.get("audio") and (not args.slugs or t["slug"] in args.slugs)]
    if not texts:
        sys.exit("no text with \"audio\" in the catalogue" + (f" among {args.slugs}" if args.slugs else ""))
    lib = sqlite3.connect(LIBRARY)
    out = sqlite3.connect(OUT)
    out.executescript(SCHEMA)

    for t in texts:
        slug = t["slug"]
        form = lib.execute("SELECT form FROM texts WHERE slug = ?", (slug,)).fetchone()
        if not form:
            log(f"== {slug}: not in library.sqlite (run scripts/library.py)")
            continue
        form = form[0]
        divisions = {n for (n,) in lib.execute("SELECT n FROM divisions WHERE text = ?", (slug,))}
        jobs, spans_read = {}, {}
        cv = lambda s: tuple(map(int, s.split(":"))) if s else None
        for entry in t["audio"]:
            for divs, files in plan(entry, divisions).items():
                jobs[divs] = (files, entry.get("credit"))
                if entry.get("start") or entry.get("end"):
                    spans_read[divs] = (cv(entry.get("start")), cv(entry.get("end")))
        for divs in sorted(jobs):
            if args.book and args.book not in divs:
                continue
            files, credit = jobs[divs]
            t0 = time.time()
            lps = [emissions_of(f) for f in files]
            lp = np.concatenate(lps) if len(lps) > 1 else lps[0]
            units = [(div, *u) for div in divs for u in units_of(lib, slug, div, form)]
            span = spans_read.get(divs)
            if span:  # a recording of part of the book: only the verses it reads
                refs = {(div, seq): ref for div, seq, ref in lib.execute(
                    "SELECT div, seq, ref FROM segments WHERE text = ?", (slug,))}
                pos = lambda u: (u[0], int(refs[(u[0], u[1])]) if refs[(u[0], u[1])].isdigit() else 0)
                first, last = span
                units = [u for u in units if (not first or pos(u) >= first) and (not last or pos(u) <= last)]
            toks = [tokens_of(text) for *_, text in units]
            keep = [k for k, tk in enumerate(toks) if tk]  # a unit with no letters can't be heard
            spans = align(lp, [toks[k] for k in keep])
            rows = [(slug, *units[k], span[0] * FRAME_S, span[1] * FRAME_S, span[2] * FRAME_S, span[3])
                    for k, span in zip(keep, spans) if span]
            skipped = len(keep) - len(rows)
            name = reader_audio(files, slug, divs[0], make=not args.no_audio)
            heard = {r[1] for r in rows}  # chapters the recording reads (some or all of)
            for div in divs:
                out.execute("DELETE FROM units WHERE text = ? AND div = ?", (slug, div))
                if div in heard:
                    out.execute("INSERT OR REPLACE INTO recordings VALUES (?, ?, ?, ?, ?)", (slug, div, name, credit, len(lp)))
                else:
                    out.execute("DELETE FROM recordings WHERE text = ? AND div = ?", (slug, div))
            out.executemany("INSERT INTO units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            out.commit()
            scores = np.array([r[-1] for r in rows])
            weak = int((scores < -2.0).sum())
            div = f"{divs[0]}" if len(divs) == 1 else f"{divs[0]}–{divs[-1]}"
            log(f"== {slug} {div}: {len(rows)} units over {len(lp) * FRAME_S / 60:.0f} min "
                f"({len(files)} file{'s' if len(files) > 1 else ''}), first at {rows[0][5]:.0f} s, "
                f"last ends {rows[-1][6]:.0f} s; score median {np.median(scores):.2f}, "
                f"{weak} below -2, {skipped} passed over · {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
