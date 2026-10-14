#!/usr/bin/env python3
"""Align recordings of library texts (data/library.sqlite) to their text, and give the
reader one audio file per book.

    .venv/bin/python scripts/align_library.py [SLUG ...] [--book N] [--no-audio] [-j JOBS]

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

A dialogue's speaker labels (ΣΩ.) may be read out, by the reader or by a second voice
announcing each speaker. The catalogue's "speakers" ({"ΣΩ.": "Σωκράτης", ...}) gives the
names to listen for: each label is aligned as a unit of its own (passed over where it
isn't read), and where it is heard, the sentence after it starts with it, so that playing
the sentence doesn't skip the announcement. The text shown keeps the labels as they are.

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
import difflib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
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
NAME_MAX = 1.6              # seconds: a speaker's name, said, takes less, and the pause after
NAME_GAP = 3.0              # it this; a name heard otherwise is a stray match
NAME_LEAD = 0.25            # seconds: an announced speech starts this before the name is heard
                            # (the model hears a letter a little after it begins to sound)
WINDOW, KEEP = 30, 24       # units aligned per window / accepted from it
SENTENCE_END = re.compile(r"(?<=[.;·;·!?])\s+")


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------- the text

def units_of(con, slug, div, form, speakers=None):
    """(seq, piece, text) for each unit of a division, in reading order. A speaker label
    read out (speakers: label -> name) is a unit of its own before the speech, piece -1."""
    rows = con.execute("SELECT seq, content, speaker FROM segments WHERE text = ? AND div = ? ORDER BY seq",
                       (slug, div)).fetchall()
    out = []
    for seq, content, speaker in rows:
        if speaker and speakers and speakers.get(speaker):
            out.append((seq, -1, speakers[speaker]))
        if form == "verse" or seq <= 0:  # a line of verse, or a heading line: one unit
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


def romanize_word(word):
    global _uroman
    if _uroman is None:
        import uroman
        _uroman = uroman.Uroman()
    roman = _uroman.romanize_string(word, lcode="ell").lower()
    return "".join(c for c in roman if c in VOCAB and c != "'")


# uroman is slow and the words recur: each is romanized once (word by word gives the same
# letters as a whole line), and kept between runs in data/raw/romanized.json.
ROMANIZED = ROOT / "data" / "raw" / "romanized.json"
_romanized, _new_words = None, {}


def romanize_letters(text):
    global _romanized
    if _romanized is None:
        try:
            _romanized = json.loads(ROMANIZED.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _romanized = {}
    letters = []
    for w in text.split():
        if w not in _romanized:
            _romanized[w] = _new_words[w] = romanize_word(w)
        letters += _romanized[w]
    return letters


def save_romanized(words):
    """Add words romanized in this run (by every worker) to the file kept between runs."""
    if not words:
        return
    try:
        kept = json.loads(ROMANIZED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    kept.update(words)
    ROMANIZED.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROMANIZED.with_suffix(".tmp")
    tmp.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ROMANIZED)


# A number in Greek numerals is read out as its letters' names, in modern pronunciation
# ("Κεφάλαιον ΙΣΤʹ": γιώτα σίγμα ταυ), unless what it says follows in brackets ("Κεφάλαιον
# Δʹ (Τέταρτον)": only that is read).
LETTER_NAMES = {"Α": "άλφα", "Β": "βήτα", "Γ": "γάμα", "Δ": "δέλτα", "Ε": "έψιλον", "Ϛ": "σίγμα ταυ",
                "Ζ": "ζήτα", "Η": "ήτα", "Θ": "θήτα", "Ι": "γιώτα", "Κ": "κάπα", "Λ": "λάμδα", "Μ": "μι",
                "Ν": "νι", "Ξ": "ξι", "Ο": "όμικρον", "Π": "πι", "Ϟ": "κόπα", "Ρ": "ρο", "Σ": "σίγμα",
                "Τ": "ταυ", "Υ": "ύψιλον", "Φ": "φι", "Χ": "χι", "Ψ": "ψι", "Ω": "ωμέγα", "Ϡ": "σαμπί"}


def spoken(text):
    words = text.split()
    out = []
    for k, w in enumerate(words):
        if "ʹ" in w:
            if k + 1 < len(words) and words[k + 1].startswith("("):
                continue
            out += [LETTER_NAMES.get(c, "") for c in w if c != "ʹ"]
        else:
            out.append(w)
    return " ".join(out)


def tokens_of(text):
    return [VOCAB[c] for c in romanize_letters(spoken(text))]


# ------------------------------------------------------------------- alignment

def ctc_align(lp, units, glued=frozenset()):
    """Viterbi CTC alignment of units (token lists) to all frames of lp, where the blank
    between two units, and before the first and after the last, may instead take any
    audio at STAR_COST a frame, and a unit may be passed over at SKIP_COST a letter. After
    a unit in glued (a speaker's name), the next follows at once: only a pause between.
    Returns, per unit, its (first frame, frame after its last sound), or None if passed
    over, and the path's per-frame score."""
    tokens, boundary = [], []  # boundary[k]: the blank before token k is between units
    for n, u in enumerate(units):
        for j, t in enumerate(u):
            tokens.append(t)
            boundary.append(j == 0 and (n - 1) not in glued)
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
    over_from = np.full(S, -1, np.int64)  # the boundary after a unit: from the one before it
    over_cost = np.zeros(S)
    over_from[B[1:]] = B[:-1]
    over_cost[B[1:]] = pass_over
    first, last, score = viterbi(np.ascontiguousarray(ext), labels.astype(np.int64), skip, over_from, over_cost)
    spans = []
    for k in range(len(units)):
        a, b = bounds[k], bounds[k + 1] - 1
        if last[b] < 0:  # passed over
            spans.append(None)
            continue
        edge = max(1, (b - a + 1) // 4)  # a stray run is a few letters at either end

        def apart(x, y):  # letters x and y (frames) are separated, not one stretch of speech
            return y - x > STRAY_GAP

        for j in range(a, min(a + edge, b)):  # the latest such gap among the first letters
            if apart(last[j], first[j + 1]):
                a = j + 1
        for j in range(b, max(b - edge, a), -1):  # the earliest among the last letters
            if apart(last[j - 1], first[j]):
                b = j - 1
        spans.append((first[a], last[b] + 1))
    return spans, score


def viterbi_py(ext, labels, skip, over_from, over_cost):
    """The CTC Viterbi pass and its backtrace: per token its first and last frame (-1 if
    passed over), and the path's score per frame. Moves into a state: stay, step from the
    one before, jump a blank between different tokens, or pass over a unit (over_from);
    ties go in that order. (Compiled by numba where it's installed: viterbi.)"""
    T, S = len(ext), len(labels)
    NEG = -1e30
    alpha = np.full(S, NEG)
    alpha[0], alpha[1] = ext[0, labels[0]], ext[0, labels[1]]
    back = np.zeros((T, S), np.int8)
    for t in range(1, T):
        prev = alpha.copy()
        for s in range(S):
            best, move = prev[s], 0
            if s >= 1 and prev[s - 1] > best:
                best, move = prev[s - 1], 1
            if skip[s] and prev[s - 2] > best:
                best, move = prev[s - 2], 2
            if over_from[s] >= 0 and prev[over_from[s]] + over_cost[s] > best:
                best, move = prev[over_from[s]] + over_cost[s], 3
            back[t, s] = move
            alpha[s] = best + ext[t, labels[s]]
    L = (S - 1) // 2
    first, last = np.zeros(L, np.int64), np.full(L, -1, np.int64)
    score = np.zeros(T)
    s = S - 1 if alpha[S - 1] >= alpha[S - 2] else S - 2
    for t in range(T - 1, -1, -1):
        score[t] = ext[t, labels[s]]
        if s % 2:
            first[s // 2] = t
            if last[s // 2] < 0:
                last[s // 2] = t
        move = back[t, s]
        s = over_from[s] if move == 3 else s - move
    return first, last, score


try:  # compiled, the loop runs many times faster; the same results either way
    from numba import njit
    viterbi = njit(cache=True)(viterbi_py)
except ImportError:
    viterbi = viterbi_py


def heard_letters(lp):
    """How many letters the model hears in some audio (its greedy reading)."""
    if not len(lp):
        return 0
    ids = lp.argmax(1)
    starts = np.concatenate(([True], ids[1:] != ids[:-1]))
    return int(((ids > 3) & starts).sum())


def align(lp, units, glued=frozenset()):
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
        spans, score = ctc_align(lp[f0:f1], win, {k - i for k in glued if i <= k < i + len(win)})
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


# LibriVox's spoken notices, which the model transcribes as English: at the start of each
# file "[title,] part 2 of 3. This is a LibriVox recording. ... please visit librivox dot
# org", at the end "End of [title]...". They're found in the model's reading of the audio
# and blocked out before aligning, so that no text can be matched inside them (it fits
# stray letters there otherwise, a name or a sentence's first words to "…dot org").
NOTICE_START = re.compile(r"part|t[yh]isis|lib\w{0,3}[vb]ox|recording|publicdomain|volunt|visit|d?o?torg")
NOTICE_END = re.compile(r"endof")


def greedy_reading(lp):
    """The model's reading of some audio: (letters, frame of each)."""
    ids = lp.argmax(1)
    starts = np.flatnonzero(np.concatenate(([True], ids[1:] != ids[:-1])) & (ids > 3))
    inv = {i: c for c, i in VOCAB.items()}
    return "".join(inv[int(ids[t])] for t in starts), starts


def block_notices(lp, first):
    """lp with a LibriVox file's notices blocked out (letters impossible, a pause dearer
    than leaving the text); first: the work's first file, whose opening title is text."""
    text, at = greedy_reading(lp)
    head = [m for m in NOTICE_START.finditer(text) if at[m.start()] < 60 / FRAME_S]
    if not any("box" in m.group() or "vox" in m.group() or "record" in m.group() for m in head):
        return lp  # not a LibriVox recording
    out = lp.copy()
    block = lambda a, b: (out.__setitem__((slice(a, b), slice(1, None)), -50.0),
                          out.__setitem__((slice(a, b), BLANK), STAR_COST - 0.5))
    a = 0 if not first else max(0, int(at[head[0].start()]) - 15)
    block(a, min(len(out), int(at[head[-1].end() - 1]) + int(1.2 / FRAME_S)))  # (+ "dot org")
    tail = [m for m in NOTICE_END.finditer(text) if at[m.start()] > len(lp) - 60 / FRAME_S]
    if tail:
        block(max(0, int(at[tail[0].start()]) - 10), len(out))
    return out


def emissions_path(path):
    return path.parent / "cache" / f"{path.name}.{MODEL}.npy"


def emissions_of(path):
    npy = emissions_path(path)
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
    cut        INTEGER,            -- 1: the gap after it is speech that isn't text (an
                                   -- announcement), to be skipped at once, not as a pause
    PRIMARY KEY (text, div, seq, piece));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*", help="texts to align (default: every text with \"audio\")")
    ap.add_argument("--book", type=int, help="only this book")
    ap.add_argument("--no-audio", action="store_true", help="don't (re)join multi-part recordings")
    ap.add_argument("--jobs", "-j", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                    help="recordings aligned at once (default: half the cores)")
    args = ap.parse_args()

    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    texts = [t for t in catalogue["texts"] if t.get("audio") and (not args.slugs or t["slug"] in args.slugs)]
    if not texts:
        sys.exit("no text with \"audio\" in the catalogue" + (f" among {args.slugs}" if args.slugs else ""))
    lib = sqlite3.connect(LIBRARY)
    out = sqlite3.connect(OUT)
    out.executescript(SCHEMA)
    if "cut" not in {r[1] for r in out.execute("PRAGMA table_info(units)")}:  # (made before there was one)
        out.execute("ALTER TABLE units ADD COLUMN cut INTEGER")

    work = []  # one job per recording (a book, or all of a text's divisions in one file)
    for t in texts:
        slug = t["slug"]
        form = lib.execute("SELECT form FROM texts WHERE slug = ?", (slug,)).fetchone()
        if not form:
            log(f"== {slug}: not in library.sqlite (run scripts/library.py)")
            continue
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
            work.append({"slug": slug, "form": form[0], "divs": divs, "files": files, "credit": credit,
                         "span": spans_read.get(divs), "speakers": t.get("speakers"), "make": not args.no_audio})
    lib.close()

    # Several recordings at once, each in a process of its own (the alignment is one core's
    # work); the longest first, so that the last to finish isn't a long one started late.
    # Only this process writes to library_audio.sqlite, a recording's units as it's done.
    work.sort(key=lambda j: -sum(emissions_path(f).stat().st_size for f in j["files"] if emissions_path(f).exists()))
    jobs_n = max(1, min(args.jobs, len(work)))
    if jobs_n == 1:
        results = map(align_recording, work)
    else:
        pool = ProcessPoolExecutor(jobs_n)
        results = (f.result() for f in as_completed([pool.submit(align_recording, j) for j in work]))
    for r in results:
        slug, divs = r["slug"], r["divs"]
        heard = {row[1] for row in r["rows"]}  # chapters the recording reads (some or all of)
        for div in divs:
            out.execute("DELETE FROM units WHERE text = ? AND div = ?", (slug, div))
            if div in heard:
                out.execute("INSERT OR REPLACE INTO recordings VALUES (?, ?, ?, ?, ?)",
                            (slug, div, r["name"], r["credit"], r["frames"]))
            else:
                out.execute("DELETE FROM recordings WHERE text = ? AND div = ?", (slug, div))
        out.executemany("INSERT INTO units (text, div, seq, piece, content, start, speech_end, end, score, cut) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", r["rows"])
        out.commit()
        save_romanized(r["words"])
        log(r["report"])


def align_recording(job):
    """One recording aligned to its text: {slug, divs, rows (for units), name (the reader's
    file), credit, frames, report}. Runs in a worker process (with its own connection)."""
    slug, form, divs, files, span = job["slug"], job["form"], job["divs"], job["files"], job["span"]
    lib = sqlite3.connect(LIBRARY)
    t0 = time.time()
    raw = [emissions_of(f) for f in files]
    heard = np.concatenate(raw) if len(raw) > 1 else raw[0]  # what was said, notices and all
    lps = [block_notices(x, first=k == 0) for k, x in enumerate(raw)]
    lp = np.concatenate(lps) if len(lps) > 1 else lps[0]
    units = [(div, *u) for div in divs for u in units_of(lib, slug, div, form, job["speakers"])]
    if span:  # a recording of part of the book: only the verses it reads
        refs = {(div, seq): ref for div, seq, ref in lib.execute(
            "SELECT div, seq, ref FROM segments WHERE text = ?", (slug,))}
        pos = lambda u: (u[0], int(refs[(u[0], u[1])]) if refs[(u[0], u[1])].isdigit() else 0)
        first, last = span
        units = [u for u in units if (not first or pos(u) >= first) and (not last or pos(u) <= last)]
    lib.close()
    toks = [tokens_of(text) for *_, text in units]
    keep = [k for k, tk in enumerate(toks) if tk]  # a unit with no letters can't be heard
    # A speaker's name comes straight before the speech, nothing between.
    glued = {n for n, k in enumerate(keep) if units[k][2] == -1}
    spans = align(lp, [toks[k] for k in keep], glued)
    rows, announced, skipped, heard_names, unheard, led = [], None, 0, 0, set(), set()
    for k, span in zip(keep, spans):
        if units[k][2] == -1:  # a speaker's name: the sentence after it starts with it
            announced = span if span else None
            heard_names += bool(span)
            if not span:  # (said, likely, but not made out: room is kept for it, see below)
                unheard.add((units[k][0], units[k][1]))
            continue
        if not span:
            skipped += 1
            continue
        start = span[0]
        if announced is not None and (announced[1] - announced[0] <= NAME_MAX / FRAME_S
                                      and span[0] - announced[1] <= NAME_GAP / FRAME_S):
            start = announced[0]
            led.add(len(rows))
        announced = None
        rows.append([slug, *units[k], start * FRAME_S, span[1] * FRAME_S, span[2] * FRAME_S, span[3], 0])
    # A gap after a unit where the model hears letters (an announcement, a part's
    # LibriVox notice) is skipped at once; a quiet one is a pause, kept.
    order = sorted(range(len(rows)), key=lambda r: rows[r][5])
    for a, b in zip(order, order[1:] + [None]):
        if b in led:  # (not into the last unit's speech)
            rows[b][5] = max(rows[a][6] + 0.05, rows[b][5] - NAME_LEAD)
        if b is not None and rows[b][3] == 0 and (rows[b][1], rows[b][2]) in unheard:
            # A name surely said but not made out: the second before the speech is kept.
            rows[b][5] = max(rows[a][6] + 0.1, rows[b][5] - 1.0)
        if b is not None:  # end: the next unit's start (as adjusted for its speaker's name)
            rows[a][7] = rows[b][5]
        gap_from = int(rows[a][6] / FRAME_S) + 5  # (a little past its last sound)
        gap_to = int(rows[b][5] / FRAME_S) if b is not None else len(lp)
        # (A short gap is a pause even if something's heard in it: a breath, a name's start.)
        speech = gap_to - gap_from >= 1.5 / FRAME_S and heard_letters(heard[gap_from:gap_to]) >= 3
        if speech and b is not None:
            # The next unit's own first words, said before a pause (a psalm's title, "Εἶπε δὲ
            # Κύριος…"), and left out of it as strays: it starts with them, nothing's skipped.
            said, frames = greedy_reading(heard[gap_from:gap_to])
            ahead = "".join(romanize_letters(spoken(rows[b][4])))[:len(said) + 4]
            if difflib.SequenceMatcher(None, said, ahead, autojunk=False).ratio() >= 0.6:
                rows[b][5] = rows[a][7] = max(rows[a][6], (gap_from + frames[0] - 5) * FRAME_S)
                speech = False
        rows[a][-1] = int(speech)
    name = reader_audio(files, slug, divs[0], make=job["make"])
    scores = np.array([r[8] for r in rows])
    weak = int((scores < -2.0).sum())
    div = f"{divs[0]}" if len(divs) == 1 else f"{divs[0]}–{divs[-1]}"
    report = (f"== {slug} {div}: {len(rows)} units over {len(lp) * FRAME_S / 60:.0f} min "
              f"({len(files)} file{'s' if len(files) > 1 else ''}), first at {rows[0][5]:.0f} s, "
              f"last ends {rows[-1][6]:.0f} s; score median {np.median(scores):.2f}, "
              f"{weak} below -2, {skipped} passed over"
              + (f", {heard_names}/{sum(u[2] == -1 for u in units)} speakers heard" if any(u[2] == -1 for u in units) else "")
              + f" · {time.time() - t0:.0f} s")
    new_words = dict(_new_words)
    _new_words.clear()
    return {"slug": slug, "divs": divs, "rows": rows, "name": name, "credit": job["credit"],
            "frames": len(lp), "report": report, "words": new_words}

if __name__ == "__main__":
    main()
