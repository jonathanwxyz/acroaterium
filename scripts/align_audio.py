#!/usr/bin/env python3
"""Align a recording of one Iliad book to its verses (CTC forced alignment) and
store per-verse start/end times in data/iliad.sqlite (`verse_timings`).

    .venv/bin/python scripts/align_audio.py --book 1 --audio recordings/Iliad/iliad01.m4a --model greek

Models (both wav2vec2, 300M parameters):
  mms    MahmoudAshraf/mms-300m-1130-forced-aligner   Meta MMS, romanized (uroman) text (default)
  greek  jonatasgrosman/wav2vec2-large-xlsr-53-greek  modern Greek, monotonic uppercase letters;
         on Book 1 it lost its place after ~line 100, MMS did not

The spoken book title (`books.title`) is aligned as line 0, before line 1.
Frame-level model output (the emissions) is cached next to the recording
(recordings/Iliad/cache/). That is nearly all the work, and it depends only on the audio,
so it can be made on a faster machine and copied over:

    .venv/bin/python scripts/align_audio.py --audio recordings/Iliad/iliad01.m4a --emissions-only

needs neither the database nor --book. Copy recordings/Iliad/cache/<file>.<model>.npy back
and run the full command here: it finds the cache and aligns in seconds. For many
recordings at once, use make_emissions.py. The audio itself isn't needed here once
its emissions exist:

    .venv/bin/python scripts/align_audio.py --book 1 --emissions cache/iliad01.m4a.mms.npy

The model runs on a GPU when there is one (--device auto: CUDA, then Apple's MPS, else
CPU); a GPU is many times faster than a laptop CPU. It needs a GPU build of PyTorch,
see requirements-audio.txt.
"""
import argparse
import sqlite3
import subprocess
import time
import unicodedata
from pathlib import Path

import numpy as np
import torch
from transformers import AutoProcessor, Wav2Vec2ForCTC

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "iliad.sqlite"
RECORDINGS = ROOT / "recordings"
MODELS = {
    "greek": "jonatasgrosman/wav2vec2-large-xlsr-53-greek",
    "mms": "MahmoudAshraf/mms-300m-1130-forced-aligner",
}
SR = 16000
HOP = 320  # samples per model frame (20 ms)
FRAME_S = HOP / SR

CHUNK_S, CONTEXT_S = 20, 2  # emission chunks, with context on each side
WINDOW_LINES, KEEP_LINES = 25, 20  # verses aligned per window / accepted from it


# ---------------------------------------------------------------- audio + model

def load_audio(path):
    """Decode any ffmpeg-readable file to 16 kHz mono float32."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def pick_device(name):
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def emissions(audio, model, processor, device):
    """Frame-level log-probabilities (frames x vocab) for the whole recording."""
    chunk, ctx = CHUNK_S * SR, CONTEXT_S * SR
    out = []
    with torch.inference_mode():
        for s0 in range(0, len(audio), chunk):
            s1 = min(s0 + chunk, len(audio))
            a0, a1 = max(0, s0 - ctx), min(len(audio), s1 + ctx)
            x = processor(audio[a0:a1], sampling_rate=SR, return_tensors="pt").input_values.to(device)
            lp = torch.log_softmax(model(x).logits[0].float(), dim=-1).cpu().numpy()
            first = round((s0 - a0) / HOP)
            out.append(lp[first:first + round((s1 - s0) / HOP)])
    return np.concatenate(out)


# ---------------------------------------------------------------- text -> tokens

def greek_monotonic_upper(text):
    """Polytonic -> modern monotonic uppercase, as in the Greek model's vocabulary."""
    out = []
    for ch in unicodedata.normalize("NFD", text):
        if unicodedata.category(ch) != "Mn":
            out.append(ch.upper())
        elif ch in "́̀͂" and out:  # acute, grave, circumflex -> tonos
            out[-1] += "́"
        elif ch == "̈" and out:  # diaeresis
            out[-1] += "̈"
    return [unicodedata.normalize("NFC", c) for c in out]


def tokenize(text, model_name, vocab, uroman=None):
    """Token ids for one segment (title or verse)."""
    if model_name == "greek":
        ids = []
        for c in greek_monotonic_upper(text):
            if c.isspace():
                if ids and ids[-1] != vocab["|"]:
                    ids.append(vocab["|"])
                continue
            for cand in (c, unicodedata.normalize("NFC", "".join(ch for ch in unicodedata.normalize("NFD", c)
                                                                 if ch != "́"))):
                if cand in vocab:
                    ids.append(vocab[cand])
                    break
        return ids
    roman = uroman.romanize_string(text, lcode="ell").lower()
    return [vocab[c] for c in roman if c in vocab and c != "'"]


# ---------------------------------------------------------------- alignment

def ctc_align(lp, tokens, blank):
    """Viterbi CTC alignment of tokens to all frames of lp. Returns the first and
    last frame at which each token is emitted, and the path's per-frame scores."""
    T, L = len(lp), len(tokens)
    S = 2 * L + 1
    labels = np.full(S, blank)
    labels[1::2] = tokens
    skip = np.zeros(S, bool)  # may jump from s-2 (non-blank, differs from s-2)
    skip[3::2] = np.array(tokens[1:]) != np.array(tokens[:-1])
    NEG = -1e30
    alpha = np.full(S, NEG)
    alpha[0], alpha[1] = lp[0, labels[0]], lp[0, labels[1]]
    back = np.zeros((T, S), np.int8)
    for t in range(1, T):
        stay = alpha
        step = np.concatenate(([NEG], alpha[:-1]))
        jump = np.where(skip, np.concatenate(([NEG, NEG], alpha[:-2])), NEG)
        best = np.maximum(stay, np.maximum(step, jump))
        back[t] = np.where(best == stay, 0, np.where(best == step, 1, 2))
        alpha = best + lp[t, labels]
    s = S - 1 if alpha[S - 1] >= alpha[S - 2] else S - 2
    first, last = np.zeros(L, int), np.full(L, -1)
    frame_score = np.zeros(T)
    for t in range(T - 1, -1, -1):
        frame_score[t] = lp[t, labels[s]]
        if s % 2:
            first[s // 2] = t
            if last[s // 2] < 0:
                last[s // 2] = t
        s -= int(back[t, s])
    return first, last, frame_score


def align(lp, segments, blank):
    """Align a list of token lists to the emissions, a window of segments at a time.
    Returns (start_frame, speech_end_frame, end_frame, mean log-prob) per segment:
    end is the next segment's start, speech_end the end of this one's last sound."""
    total_tokens = sum(map(len, segments))
    frames_per_token = len(lp) / total_tokens  # average pace over the recording
    results, i, f0 = [], 0, 0
    while i < len(segments):
        final = i + WINDOW_LINES >= len(segments)
        seg = segments[i:i + WINDOW_LINES]
        tokens = [t for s in seg for t in s]
        f1 = len(lp) if final else min(len(lp), f0 + int(1.6 * frames_per_token * len(tokens)) + 500)
        first, last, score = ctc_align(lp[f0:f1], tokens, blank)
        bounds = np.cumsum([0] + [len(s) for s in seg])
        keep = len(seg) if final else KEEP_LINES
        for k in range(keep):
            a = f0 + first[bounds[k]]
            b = f0 + (first[bounds[k + 1]] if bounds[k + 1] < len(tokens) else (f1 - f0))  # next segment's start
            results.append((a, f0 + last[bounds[k + 1] - 1] + 1, b, float(score[a - f0:b - f0].mean())))
        i += keep
        f0 = results[-1][2]  # next window starts where the next verse starts
    return results


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", type=int)
    ap.add_argument("--audio", type=Path)
    ap.add_argument("--emissions", type=Path,
                    help="a saved <recording>.<model>.npy (from make_emissions.py) instead of --audio")
    ap.add_argument("--model", choices=MODELS, default="mms")
    ap.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default="auto",
                    help="where the model runs (default: a GPU if there is one)")
    ap.add_argument("--emissions-only", action="store_true",
                    help="only compute and cache the model output (no database or --book needed)")
    ap.add_argument("--lines", type=int, help="only the first N verses (with --seconds, for tests)")
    ap.add_argument("--seconds", type=float, help="only the first N seconds of audio")
    args = ap.parse_args()
    if args.book is None and not args.emissions_only:
        ap.error("--book is required (unless --emissions-only)")
    if (args.audio is None) == (args.emissions is None):
        ap.error("give either --audio or --emissions")
    if args.emissions:
        suffix = f".{args.model}.npy"
        if args.emissions_only or not args.emissions.name.endswith(suffix) or not args.emissions.exists():
            ap.error(f"--emissions must be an existing <recording>{suffix} file (and not with --emissions-only)")
        recording = args.emissions.name[:-len(suffix)]
    else:
        # The reader's name for it: its path under recordings/ (e.g. Iliad/iliad01.m4a).
        try:
            recording = args.audio.resolve().relative_to(RECORDINGS.resolve()).as_posix()
        except ValueError:
            recording = args.audio.name

    name = MODELS[args.model]
    processor = AutoProcessor.from_pretrained(name)
    cache = args.emissions or args.audio.parent / "cache" / f"{recording}.{args.model}.npy"  # e.g. iliad01.m4a.mms.npy
    t0 = time.time()
    if cache.exists():
        lp = np.load(cache)
        if args.emissions_only:
            print(f"emissions: already cached in {cache}")
            return
    else:
        device = pick_device(args.device)
        model = Wav2Vec2ForCTC.from_pretrained(name).eval().to(device)
        lp = emissions(load_audio(args.audio), model, processor, device)
        cache.parent.mkdir(exist_ok=True)
        np.save(cache, lp)
        print(f"emissions: {len(lp) * FRAME_S:.0f} s of audio in {time.time() - t0:.0f} s on {device}")
        if args.emissions_only:
            print(f"-> {cache}")
            return
    if args.seconds:
        lp = lp[:int(args.seconds / FRAME_S)]

    con = sqlite3.connect(DB)
    title = con.execute("SELECT title FROM books WHERE book = ?", (args.book,)).fetchone()
    verses = con.execute("SELECT line, homer FROM verses WHERE book = ? ORDER BY line", (args.book,)).fetchall()
    if args.lines:
        verses = verses[:args.lines]
    segments = ([(0, title[0])] if title and title[0] else []) + verses

    vocab = processor.tokenizer.get_vocab()
    blank = vocab.get("<blank>", vocab.get("<pad>"))
    uroman = None
    if args.model == "mms":
        import uroman as ur
        uroman = ur.Uroman()

    tokens = [tokenize(text, args.model, vocab, uroman) for _, text in segments]
    t1 = time.time()
    spans = align(lp, tokens, blank)
    print(f"alignment: {len(segments)} segments in {time.time() - t1:.1f} s")

    rows = [(recording, args.model, args.book, line, a * FRAME_S, e * FRAME_S, b * FRAME_S, score)
            for (line, _), (a, e, b, score) in zip(segments, spans)]
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS verse_timings (
            recording TEXT    NOT NULL,  -- file name under recordings/
            model     TEXT    NOT NULL,  -- aligner used (greek, mms)
            book      INTEGER NOT NULL,
            line      INTEGER NOT NULL,  -- 0 = spoken book title
            start      REAL   NOT NULL,  -- seconds: first sound of the verse
            speech_end REAL   NOT NULL,  -- seconds: last sound of the verse
            end        REAL   NOT NULL,  -- seconds: start of the next verse (pause included)
            score     REAL,              -- mean log-probability along the path (higher = surer)
            PRIMARY KEY (recording, model, book, line)
        );
        """
    )
    con.execute("DELETE FROM verse_timings WHERE recording = ? AND model = ? AND book = ?",
                (recording, args.model, args.book))
    con.executemany("INSERT INTO verse_timings VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    print(f"{len(rows)} verse timings -> {DB.relative_to(ROOT)} (verse_timings, model={args.model})")


if __name__ == "__main__":
    main()
