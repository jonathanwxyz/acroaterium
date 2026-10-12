#!/usr/bin/env python3
"""Run the alignment model over recordings and save its output (the emissions), the
slow half of aligning audio to text. Meant for a machine with a GPU, in bulk; the
fast half (romanizing the text and fitting it to the emissions) is done afterwards by
align_audio.py, on any machine, from the saved files.

    .venv/bin/python scripts/make_emissions.py RECORDING_OR_FOLDER ... [--out DIR]
    .venv/bin/python scripts/make_emissions.py --links links.csv [--downloads DIR]

--links downloads the recordings first, from a file of `name;url` lines (blank
lines and lines starting with # are ignored). Each name gets its own folder under
--downloads (default recordings/, which git ignores), and several lines may share a
name. The files in a folder are named so that sorting them gives the reading order,
which is what the text alignment follows when it joins the parts of a work:
  YouTube video or playlist (or any site yt-dlp knows)
                  <title> [<id>].<ext>, playlists "<NNN> - <title> [<id>].<ext>"
  LibriVox book page (librivox.org/...) or its RSS feed
                  every section, "<NNN> - <file>.mp3", in the book's order
  zip file (e.g. LibriVox's "download" link)
                  its audio files, "<NNN> - <file>", in name order
  direct link to an audio file
                  "<NNN> - <file>", numbered by the line's place among that name's lines
What has been downloaded is listed in each folder's downloaded.txt and skipped next
time, so the same file can be run again as playlists grow. The audio is kept as it is
served (never re-encoded); ffmpeg reads any of it.

From YouTube it takes the AAC stream (M4A, format 140) where there is one, since every
browser plays it, iPhones included; the Opus stream (WebM) only where there is no AAC.
A folder downloaded before this took Opus: its downloaded.txt still lists those videos,
so to fetch them again as AAC, use a new folder (or delete downloaded.txt first).

Folders are searched recursively for audio files. Each recording gives
<file name>.<model>.npy (e.g. iliad01.m4a.mms.npy): frame-level log-probabilities,
one row per 20 ms, float32. By default it goes in a cache/ folder next to the
recording, where align_audio.py looks for it; --out puts them all in one folder
instead. Files already made are skipped, and each is written whole or not at all,
so an interrupted run can simply be started again.

No language is needed here: the model's output depends only on the audio. The
language matters only when the text is romanized, in align_audio.py.

Needs PyTorch (a CUDA build to use an NVIDIA GPU), transformers and ffmpeg, and
yt-dlp for --links; see requirements-audio.txt. The model (~1.2 GB) downloads on
first use.
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import numpy as np
from transformers import AutoProcessor, Wav2Vec2ForCTC

from align_audio import FRAME_S, MODELS, ROOT, emissions, load_audio, pick_device

AUDIO = {".m4a", ".mp3", ".opus", ".ogg", ".oga", ".wav", ".flac", ".aac", ".webm", ".mka", ".mp4", ".mkv"}


def read_links(path):
    """[(name, url)] from `name;url` lines."""
    links = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, url = line.rpartition(";")
        if not sep or not name.strip() or not url.strip().startswith("http"):
            sys.exit(f"{path}:{n}: expected `name;url`, got {line!r}")
        links.append((name.strip(), url.strip()))
    return links


UA = {"User-Agent": "Mozilla/5.0 (make_emissions.py; audio for text alignment)"}


def fetch(url, dest):
    """Download url to dest (via dest.part, so a file is whole or absent)."""
    if dest.exists():
        return
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.replace(dest)


def url_name(url):
    return unquote(Path(urlparse(url).path).name)


def librivox_sections(url):
    """The mp3 of every section of a LibriVox book, in order, from its page or RSS feed."""
    if "/rss/" not in url:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
            m = re.search(r"https?://librivox\.org/rss/\d+", r.read().decode("utf-8", "replace"))
        if not m:
            raise ValueError("no RSS feed found on the LibriVox page")
        url = m[0]
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
        feed = ElementTree.fromstring(r.read())
    return [e.get("url") for e in feed.iter("enclosure") if e.get("url")]


def download(links, root):
    """Fetch each link's audio into root/<name>/; returns the folders, in order."""
    ytdlp = [shutil.which("yt-dlp")] if shutil.which("yt-dlp") else [sys.executable, "-m", "yt_dlp"]
    folders, count = [], {}
    for name, url in links:
        folder = root / re.sub(r'[/\\:*?"<>|]', "_", name)
        folder.mkdir(parents=True, exist_ok=True)
        if folder not in folders:
            folders.append(folder)
        count[folder] = count.get(folder, 0) + 1
        archive = folder / "downloaded.txt"
        done = set(archive.read_text().split()) if archive.exists() else set()
        host, path = urlparse(url).netloc.lower(), unquote(urlparse(url).path + "?" + urlparse(url).query).lower()
        print(f"download  {name}: {url}", flush=True)
        try:
            if "librivox.org" in host:
                for i, u in enumerate(librivox_sections(url), 1):
                    if u not in done:
                        fetch(u, folder / f"{i:03d} - {url_name(u)}")
                        done.add(u)
                        with open(archive, "a") as f:
                            print(u, file=f)
            elif ".zip" in path or "/compress/" in path:
                if url not in done:
                    tmp = folder / "download.zip"
                    fetch(url, tmp)
                    with zipfile.ZipFile(tmp) as z:
                        members = sorted(m for m in z.namelist() if Path(m).suffix.lower() in AUDIO)
                        for i, m in enumerate(members, 1):
                            with z.open(m) as src, open(folder / f"{i:03d} - {Path(m).name}", "wb") as dst:
                                shutil.copyfileobj(src, dst, 1 << 20)
                    tmp.unlink()
                    with open(archive, "a") as f:
                        print(url, file=f)
            elif Path(urlparse(url).path).suffix.lower() in AUDIO:
                if url not in done:
                    fetch(url, folder / f"{count[folder]:03d} - {url_name(url)}")
                    with open(archive, "a") as f:
                        print(url, file=f)
            else:
                r = subprocess.run(ytdlp + [
                    # AAC (M4A) first: it plays everywhere; then any audio, then audio+video.
                    "-f", "bestaudio[ext=m4a]/bestaudio/best",
                    "--no-overwrites", "--ignore-errors", "--no-progress",
                    "--download-archive", str(archive),
                    "-o", str(folder / "%(playlist_index&{:03d} - |)s%(title)s [%(id)s].%(ext)s"),
                    url])
                if r.returncode:
                    print(f"WARN  yt-dlp reported errors for {name} (exit {r.returncode}); "
                          "continuing with what was downloaded")
        except Exception as e:  # one bad link shouldn't stop the rest
            print(f"FAIL  {name}: {url}: {e}")
    return folders


def recordings(paths):
    for p in paths:
        if p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO and f.is_file())
        else:
            yield p


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path, help="recordings, or folders of them")
    ap.add_argument("--links", type=Path, help="file of `name;url` lines: download these from YouTube first")
    ap.add_argument("--downloads", type=Path, default=ROOT / "recordings",
                    help="where --links downloads go, one folder per name (default: recordings/)")
    ap.add_argument("--out", type=Path, help="one folder for all the output (default: cache/ next to each recording)")
    ap.add_argument("--model", choices=MODELS, default="mms")
    ap.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default="auto",
                    help="where the model runs (default: a GPU if there is one)")
    args = ap.parse_args()
    if not args.paths and not args.links:
        ap.error("give recordings or folders, or --links")
    paths = list(args.paths)
    if args.links:
        paths += download(read_links(args.links), args.downloads)

    todo = []
    for rec in recordings(paths):
        out = (args.out or rec.parent / "cache") / f"{rec.name}.{args.model}.npy"
        if out.exists():
            print(f"skip  {rec} (done)")
        else:
            todo.append((rec, out))
    if not todo:
        return

    name = MODELS[args.model]
    device = pick_device(args.device)
    processor = AutoProcessor.from_pretrained(name)
    model = Wav2Vec2ForCTC.from_pretrained(name).eval().to(device)
    print(f"{len(todo)} recording(s), model {name} on {device}")

    start = time.time()
    total = 0.0
    for i, (rec, out) in enumerate(todo, 1):
        t0 = time.time()
        try:
            lp = emissions(load_audio(rec), model, processor, device)
        except Exception as e:  # a bad file shouldn't stop a long batch
            print(f"FAIL  {rec}: {e}")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        with open(tmp, "wb") as f:
            np.save(f, lp)
        tmp.replace(out)
        secs = len(lp) * FRAME_S
        total += secs
        print(f"[{i}/{len(todo)}] {rec}: {secs / 60:.0f} min of audio in {time.time() - t0:.0f} s -> {out}")
    print(f"done: {total / 3600:.1f} h of audio in {(time.time() - start) / 60:.0f} min")


if __name__ == "__main__":
    main()
