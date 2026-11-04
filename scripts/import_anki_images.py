#!/usr/bin/env python3
"""Pictures for lemmas, from Anki decks: each note's pictures under the lemma of its Greek
word, for the reader to show by a word (on hover, and in the word card).

The decks (.apkg) are in images/, and library/image_decks.json lists them, best first (all
local, like the pictures made from them; library/image_decks.example.json shows the
format): a lemma with pictures from several decks shows the best deck's first
(image_sources.rank; the API gives them all, in that order). A deck marked "hidden" is
imported all the same, but its pictures aren't shown.

Tables in data/library.sqlite (library.py leaves them be; compact_library.py leaves them
out: the pictures are served, not kept on the device):
  image_sources (source, name, rank, deck, hidden)
  lemma_images  (source, note, ord, file, headword, lemma, method)
      one row per picture of each note with a Greek word, matched or not (lemma NULL;
      method says why), so that nothing is lost when the matching or the policy changes.
      lemma is as in lemmas.lemma (not its id: lemma_index renumbers them).
Pictures: data/lemma_images/<source>/<media number>.webp (at most 480 px), made once;
served by serve.py at /lemma-images/.

A note's Greek word: the deck's Greek field (or its first field with Greek in it), its
first line up to a comma, without an article or an ending given after it (ὁ δοῦλος -ου:
δοῦλος). Several words left (a phrase, a sentence): not matched ("phrase").
Matched to the library's lemmas (method):
  lemma      the word as the lemma index would key it (lemma_key: the short definitions'
             headword, data/lemma_aliases.tsv), or as a parsing spells it (lemma_forms)
  variant    so, its contracted or middle verb, Attic ττ, prefix written apart as the
             lemma has them (variants())
  accents    the same letters and breathings, accents aside (the lemma with most words),
             for a word of five letters or more (not ποτε: πότε)
  bare       the same letters (search_key), likewise
  inflection a form printed in the texts (lemma_inflections: ἀκούουσιν), with its accents
             or, five letters or more, without (the lemma with most words so printed)
  hand       image_decks.json's "overrides" (where the above go wrong: ἀλλᾶς, not ἄλλος)
Rerun after rebuilding the lemma index (fast: pictures already made are kept).
"""
import argparse
import html
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_shortdefs import matcher  # noqa: E402
from library import lemma_key, search_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DECKS_DIR = ROOT / "images"
OUT = ROOT / "data" / "lemma_images"
DB = ROOT / "data" / "library.sqlite"
DECKS = ROOT / "library" / "image_decks.json"  # local (as the catalogue): image_decks.example.json shows it

SIZE = 480
GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
ARTICLES = {"ὁ", "ἡ", "τό", "τὸ", "οἱ", "αἱ", "τά", "τὰ", "ὀ", "η", "ῆ"}  # (and as some decks misspell them)


def open_deck(path):
    """(collection db, {file name: media member})"""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        member = next(n for n in ("collection.anki21b", "collection.anki21", "collection.anki2") if n in names)
        if member.endswith("b"):
            sys.exit(f"{path.name}: the newest deck format (its media list) isn't read yet")
        data, media = z.read(member), z.read("media")
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.write(data)
    tmp.close()
    return sqlite3.connect(tmp.name), {name: num for num, name in json.loads(media).items()}


def field_names(db):
    """{note type id: [field names]}"""
    try:
        types = {}
        for ntid, name in db.execute("SELECT ntid, name FROM fields ORDER BY ntid, ord"):
            types.setdefault(ntid, []).append(name)
        return types
    except sqlite3.OperationalError:
        models = json.loads(db.execute("SELECT models FROM col").fetchone()[0])
        return {int(k): [f["name"] for f in m["flds"]] for k, m in models.items()}


def plain(field):
    """A field's text, line by line."""
    s = re.sub(r"\[sound:[^\]]*\]", "", field)
    s = re.sub(r"<\s*(br|div|/div|p|/p)\b[^>]*>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ")
    return [line.strip() for line in s.splitlines() if line.strip()]


def greek_word(text):
    """The headword of a deck's Greek (ὁ δοῦλος -ου, ἁπαλός, -ή, -όν): (word, None), or
    (None, why not)."""
    text = re.sub(r"(?<=\w)\((\w{1,2})\)", r"\1", text)  # (οὕτω(ς), πρόσ(σ)ω)
    first = re.split(r"[,;(\[/·…]|\.\.", text, maxsplit=1)[0]
    words = [re.sub(r"^[^\wʼ’']+|[^\wʼ’']+$", "", w) for w in first.split() if not w.startswith(("-", "–"))]
    words = [w for w in words if w and GREEK.search(w)]
    if len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    if len(words) == 1:
        return words[0], None
    return None, "phrase" if words else "no Greek"


def acute(s):
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", s).replace("̀", "́"))


def no_accents(s):
    return unicodedata.normalize("NFC", "".join(
        c for c in unicodedata.normalize("NFD", s) if c not in "̀́͂̈ͅ"))


# A verb as a dictionary has it (ἀκολουθῶ: ἀκολουθέω; λυποῦμαι: λυπέω, λυπέομαι;
# ἀνίσταμαι: ἀνίστημι), the endings NFC
ENDINGS = [("οῦμαι", ("έομαι", "όομαι", "έω", "όω")), ("ῶμαι", ("άομαι", "άω")), ("ῶ", ("έω", "άω", "όω")),
           ("αμαι", ("ημι",)), ("εμαι", ("ημι",)), ("υμαι", ("υμι",)), ("ομαι", ("ω", "ωμι"))]


def variants(word):
    """Other spellings of a deck's word: its contracted or middle verb as the lemma has it, its
    Attic ττ/ρρ as σσ/ρσ (θάλαττα: θάλασσα), its prefix as joined (ἀπ-έρχομαι, συνμαθητής)."""
    word = word.replace("-", "")  # (ἀπ-έρχομαι)
    assimilated = re.sub(r"ν(?=[γκχξ])", "γ", re.sub(r"ν(?=[μπβφψ])", "μ", word))  # (συνμαθητής)
    words = dict.fromkeys((word, word.replace("ττ", "σσ"), word.replace("ρρ", "ρσ"), assimilated))
    for w in words:
        yield w
        for end, others in ENDINGS:
            if w.endswith(end):
                for other in others:
                    yield w[:-len(end)] + other
                break


def lemma_finder(con):
    """find(word) -> (lemma or None, method)"""
    key = lemma_key(matcher())
    n = dict(con.execute("SELECT lemma, n FROM lemmas"))
    forms = dict(con.execute("SELECT f.form, l.lemma FROM lemma_forms f JOIN lemmas l ON l.id = f.lemma"))
    by_accents, by_bare = {}, {}
    for lemma in n:
        by_accents.setdefault(no_accents(lemma), []).append(lemma)
        by_bare.setdefault(search_key(lemma), []).append(lemma)
    inflections = {}
    for bare, lemma, form, k in con.execute(
            "SELECT i.bare, l.lemma, i.form, i.n FROM lemma_inflections i JOIN lemmas l ON l.id = i.lemma"):
        inflections.setdefault(bare, []).append((k, lemma, form))
    most = lambda lemmas: max(lemmas, key=lambda x: (n[x], x)) if lemmas else None

    def exact(word):
        for w in dict.fromkeys((word, word.lower(), word[:1].upper() + word[1:])):
            for cand in (key(w), acute(w)):
                if cand in n:
                    return cand
            if w in forms:
                return forms[w]

    def find(word):
        word = unicodedata.normalize("NFC", word)
        if (hit := exact(word)):
            return hit, "lemma"
        for v in variants(word):
            if (hit := exact(v)):
                return hit, "variant"
        if len(search_key(word)) >= 5:  # (not ποτε: πότε, οἱ: οἷ)
            for w in dict.fromkeys((word, word.lower())):
                if (hit := most(by_accents.get(no_accents(acute(w))))):
                    return hit, "accents"
            if (hit := most(by_bare.get(search_key(word)))):
                return hit, "bare"
        hits = inflections.get(search_key(word), [])
        same = [h for h in hits if h[2] in (acute(word), acute(word).lower())]
        if same or (hits and len(search_key(word)) >= 5):
            return max(same or hits)[1], "inflection"
        return None, "no lemma"
    return find


def load_decks():
    """(decks [{source, name, file, field}], best first; {(source, headword): lemma or None})"""
    if not DECKS.exists():
        sys.exit(f"{DECKS.relative_to(ROOT)} not found (see image_decks.example.json beside it)")
    spec = json.loads(DECKS.read_text(encoding="utf-8"))
    nfc = lambda s: unicodedata.normalize("NFC", s) if s else None
    return spec["decks"], {(o["source"], nfc(o["headword"])): nfc(o.get("lemma")) for o in spec.get("overrides", [])}


def convert(job):
    deck, member, out = job
    with zipfile.ZipFile(deck) as z, tempfile.NamedTemporaryFile() as tmp:
        tmp.write(z.read(member))
        tmp.flush()
        r = subprocess.run(["magick", tmp.name + "[0]", "-auto-orient", "-resize", f"{SIZE}x{SIZE}>",
                            "-strip", "-quality", "80", str(out)], capture_output=True, text=True)
    return out, r.returncode, r.stderr.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", action="store_true", help="list the notes not matched")
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    find = lemma_finder(con)
    decks, overrides = load_decks()
    rows, jobs = [], {}
    for d in decks:
        source, deck, greek_field = d["source"], d["file"], d.get("field")
        path = DECKS_DIR / deck
        if not path.exists():
            print(f"{source}: {deck} not found, skipped")
            continue
        db, media = open_deck(path)
        types = field_names(db)
        (OUT / source).mkdir(parents=True, exist_ok=True)
        counts, unmatched = {}, []
        for note, ntid, flds in db.execute("SELECT id, mid, flds FROM notes ORDER BY id"):
            fields = flds.split("\x1f")
            pics = [html.unescape(p) for f in fields for p in re.findall(r"<img[^>]*\bsrc=\"([^\"]+)\"", f, re.I)]
            pics = [p for p in dict.fromkeys(pics) if p in media]
            if not pics:
                continue
            names = types.get(ntid, [])
            if greek_field in names:
                text = (plain(fields[names.index(greek_field)]) or [""])[0]
            else:
                text = next((line for f in fields for line in plain(f) if GREEK.search(line)), "")
            if not GREEK.search(text):
                continue
            text = unicodedata.normalize("NFC", text)
            word, why = greek_word(text)
            if (source, text) in overrides:
                lemma, method = overrides[source, text], "hand"
            else:
                lemma, method = find(word) if word else (None, why)
            counts[method] = counts.get(method, 0) + 1
            if not lemma:
                unmatched.append((method, text))
            for i, pic in enumerate(pics):
                file = f"{source}/{media[pic]}.webp"
                rows.append((source, note, i, file, text, lemma, method))
                if not (OUT / file).exists():
                    jobs[file] = (path, media[pic], OUT / file)
        print(f"{source}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
        if args.v:
            for method, text in unmatched:
                print(f"    {method}: {text}")
    if jobs:
        print(f"making {len(jobs)} pictures…")
        with ThreadPoolExecutor() as pool:
            for out, code, err in pool.map(convert, jobs.values()):
                if code:
                    print(f"  {out.relative_to(ROOT)}: {err}")
    made = [r for r in rows if (OUT / r[3]).exists()]
    if len(made) < len(rows):
        print(f"{len(rows) - len(made)} pictures not made, left out")
    con.executescript("""
        DROP TABLE IF EXISTS image_sources; DROP TABLE IF EXISTS lemma_images;
        CREATE TABLE image_sources (     -- the Anki decks the lemmas' pictures come from
            source TEXT PRIMARY KEY,
            name   TEXT NOT NULL,
            rank   INTEGER NOT NULL,     -- 1: the best; a lemma's pictures are shown in this order
            deck   TEXT NOT NULL,        -- its file (under images/)
            hidden INTEGER NOT NULL);    -- 1: its pictures kept but not shown
        CREATE TABLE lemma_images (      -- a picture of a deck's note (scripts/import_anki_images.py)
            source   TEXT NOT NULL REFERENCES image_sources,
            note     INTEGER NOT NULL,   -- the note's id in the deck
            ord      INTEGER NOT NULL,   -- the picture's place in the note
            file     TEXT NOT NULL,      -- under data/lemma_images/
            headword TEXT NOT NULL,      -- the note's Greek, as the deck has it
            lemma    TEXT,               -- as in lemmas.lemma; NULL: not matched
            method   TEXT NOT NULL,      -- how it was matched (or why not)
            PRIMARY KEY (source, note, ord));
        CREATE INDEX lemma_images_lemma ON lemma_images (lemma);
    """)
    con.executemany("INSERT INTO image_sources VALUES (?, ?, ?, ?, ?)",
                    [(d["source"], d["name"], rank, d["file"], int(bool(d.get("hidden"))))
                     for rank, d in enumerate(decks, 1)])
    con.executemany("INSERT INTO lemma_images VALUES (?, ?, ?, ?, ?, ?, ?)", made)
    con.commit()
    lemmas = con.execute("SELECT count(DISTINCT lemma) FROM lemma_images").fetchone()[0]
    print(f"{len(made)} pictures, {lemmas} lemmas")


if __name__ == "__main__":
    main()
