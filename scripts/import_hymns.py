#!/usr/bin/env python3
"""The apolytikia and kontakia of the year, in polytonic Greek, from the AGES Initiatives'
library of the service books (the text behind the GOA's Digital Chant Stand):
https://github.com/AGES-Initiatives/ages-alwb-library-gr-gr-cog (no licence stated; the
hymns themselves are old, a few recent: Gerasimos Mikragiannanites's).

Tables in data/library.sqlite (the server picks a day's: serve.py hymn_days):
  hymn_days (day, title)                            a day of a service book and its heading
  hymns     (day, seq, kind, mode, poet, text)      its apolytikia (troparia), then its kontakia

A day is the book's own name for it:
  me.m11.d25   the Menaion's 25th of November; me.m12.dFF and the like, its Sundays in a
               stretch of days (the Forefathers, before and after the Nativity, the Fathers
               of the 4th and 7th Councils)
  tr.d001-070  the Triodion, from the Sunday of the Publican and the Pharisee (70 days
               before Pascha) to Holy Saturday
  pe.d071-127  the Pentecostarion, from Pascha to All Saints
  oc.m1-8      the Octoechos's Sunday (resurrectional) hymns of each mode, 5-8 the plagal

Each is the book's apolytikia (Vespers; Pascha's and Holy Week's troparia, Matins), then
its kontakia (Matins, the Liturgy), the alternatives the book gives (alt1., …) after its
own; a hymn given twice is kept once.

The files are fetched once (a shallow, sparse git clone, ~45 MB) into data/raw/ages_gr/.
AGES's text is carefully accented; data/hymn_corrections.tsv mends its few slips, in the
hymns and the days' headings (a breathing typed for an accent, σὐ; a missing iota
subscript; a heading in capitals). A micro sign (µ) is written μ, and ῤῥ ρρ, as the
library's other texts write it. --check lists the words that still look wrong.
"""
import argparse
import re
import sqlite3
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

REPO = "https://github.com/AGES-Initiatives/ages-alwb-library-gr-gr-cog.git"
ROOT = Path(__file__).resolve().parent.parent
CLONE = ROOT / "data" / "raw" / "ages_gr"
LIB = CLONE / "net.ages.liturgical.workbench.library_gr_GR_cog"
PARTS = ["Books-Collections/Menaion", "Books-Collections/Triodion", "Books-Collections/Pentecostarion",
         "Books-Collections/Octoechos", "Books-Collections/Heirmologion", "Constants"]
CORRECTIONS = ROOT / "data" / "hymn_corrections.tsv"
DB = ROOT / "data" / "library.sqlite"
SUFFIX = "_gr_GR_cog"

# The day's heading, by book (the first found)
HEADINGS = ["DA.commemoration.full", "DA.commemoration.short"]
# A hymn's key: [alt1.]meVE.Apolytikion2ger.text → (alternative, section, kind, number)
# (Pascha's and Holy Week's troparia: a Troparion alone, its halves 1a, 1b left out)
HYMN_KEY = re.compile(r"(?:(alt\d+)\.)?(?:me|tr|pe|oc)(VE|MA|LI)\.(?:(Apolytikion|Kontakion)(\d*)[a-z]*|(Troparion))$")
KINDS = {"Apolytikion": "Ἀπολυτίκιον", "Troparion": "Τροπάριον", "Kontakion": "Κοντάκιον"}


def fetch():
    if LIB.exists():
        return
    CLONE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", REPO, str(CLONE)], check=True)
    subprocess.run(["git", "-C", str(CLONE), "sparse-checkout", "set",
                    *(f"{LIB.name}/{p}" for p in PARTS)], check=True)


def parse(path):
    """A .ares file as {key: ("text", value) | ("ref", resource, key)}: lines key = "text"
    or key = resource.key, // and /* */ comments."""
    src = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    out = {}
    for line in src.splitlines():
        m = re.match(r'\s*([\w.\-]+)\s*=\s*(?:"(.*)"|([\w.\-]+))?\s*(?://.*)?$', line)
        if not m or m[1] == "A_Resource_Whose_Name":
            continue
        if m[2] is not None:
            out[m[1]] = ("text", m[2])
        elif m[3] and SUFFIX in m[3]:
            resource, _, key = m[3].partition(SUFFIX + ".")
            out[m[1]] = ("ref", resource, key)
    return out


class Library:
    """The AGES files, read as needed, references followed."""

    def __init__(self):
        self.paths = {p.name.removesuffix(SUFFIX + ".ares"): p for part in PARTS for p in (LIB / part).rglob("*.ares")}
        self.files = {}

    def file(self, resource):
        if resource not in self.files:
            self.files[resource] = parse(self.paths[resource]) if resource in self.paths else {}
        return self.files[resource]

    def get(self, resource, key, depth=0):
        v = self.file(resource).get(key)
        if v is None or depth > 10:
            return None
        if v[0] == "text":
            return v[1].strip() or None
        return self.get(v[1], v[2], depth + 1)


def nfc(s):
    return unicodedata.normalize("NFC", s)


def tidy(text):
    """AGES's spelling as the library's: ῤῥ as ρρ, a micro sign (µ, in a few hymns) as μ,
    the verse marks (*) left out, a space after the upper stop, spaces single."""
    text = nfc(text).replace("ῤῥ", "ρρ").replace("Ῥῥ", "Ρρ").replace("\u00b5", "μ").replace("*", " ")
    text = re.sub(r"·(?=\S)", "· ", text)
    return re.sub(r"\s+", " ", text).strip()


def day_hymns(lib, resource):
    """[(kind, mode, poet, text)] of a day: apolytikia, then kontakia; its own, then the
    alternatives; each text once."""
    keys = [k[:-len(".text")] for k in lib.file(resource) if k.endswith(".text")]
    found = []
    for k in keys:
        m = HYMN_KEY.fullmatch(k)
        if not m:
            continue
        alt, section, kind, n, troparion = m.groups()
        kind = kind or troparion
        found.append((kind == "Kontakion", bool(alt), "VE MA LI".split().index(section), int(n or 0), kind, k))
    out, seen = [], set()
    for *_, kind, k in sorted(found):
        text = lib.get(resource, k + ".text")
        if not text or not re.search(r"[\u0370-\u03ff\u1f00-\u1fff]", text) or tidy(text) in seen:
            continue
        seen.add(tidy(text))
        mode = lib.get(resource, k + ".mode")
        ref = lib.file(resource).get(k + ".mode")
        if ref and ref[0] == "ref":  # the mode without its full stop: "Ἦχος πλ. αʹ"
            mode = lib.get(ref[1], ref[2] + ".naked") or mode
        out.append((KINDS[kind], mode and mode.rstrip(". "), lib.get(resource, k + ".poet"), tidy(text)))
    return out


def heading(lib, resource):
    """The day's heading: the book's full one, or (that in capitals) its short one; in
    capitals only if both are (unaccented then, as capitals are written)."""
    book = resource[:2]
    found = [tidy(t) for h in HEADINGS if (t := lib.get(resource, book + h))]
    return next((t for t in found if not t.isupper()), found[0] if found else None)


def load_corrections():
    """{day: [(wrong, right)]}; a line: day (* for every day), wrong, right[, note]."""
    out = {}
    if CORRECTIONS.exists():
        for line in CORRECTIONS.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                day, wrong, right = line.split("\t")[:3]
                out.setdefault(day, []).append((nfc(wrong), nfc(right)))
    return out


fixes_used = set()  # (day, wrong) of the corrections that found their word


def correct(text, fixes, day):
    for wrong, right in fixes:
        pattern = rf"(?<![\ẁ-ͯ]){re.escape(wrong)}(?![\ẁ-ͯ])"
        text, n = re.subn(pattern, right, text)
        if n:
            fixes_used.update({(day, wrong), ("*", wrong)})
    return text



# --- the check: words a careful polytonic text wouldn't have
PROCLITICS = set("ὁ ἡ οἱ αἱ ἐν εἰς ἐς ἐκ ἐξ οὐ οὐκ οὐχ ὡς εἰ".split())
ENCLITICS = set("μου μοι με σου σοι σε τις τι τινος τινι τινα τινες τινας τινων τισι τισιν πως που ποτε πω "
                "γε τε περ τοι εστι εστιν εισι εισιν ειμι εσμεν εστε φημι φησι φησιν".split())
DIPHTHONGS = {"αι", "ει", "οι", "υι", "αυ", "ευ", "ου", "ηυ"}
CRASIS = {"προὔθηκεν"}  # (a coronis looks like a breathing)
WORD = re.compile(r"[Ͱ-Ͽἀ-῿̀-ͯ]+['’ʼ᾽]?")


def bare(w):
    return "".join(c for c in unicodedata.normalize("NFD", w) if not unicodedata.combining(c)).lower().rstrip("'’ʼ᾽")


def suspicious(text):
    """[(word, why)]: Latin letters in a Greek word; a vowel-initial word without a breathing; a word without an accent
    (not a proclitic or an enclitic); a breathing inside a word (crasis apart); an acute on
    the last syllable before another word (a grave belongs there)."""
    out = [(m[0], "Latin letters in a Greek word") for m in re.finditer(r"\S*(?:[A-Za-zµ][\u0370-\u03ff\u1f00-\u1fff]|[\u0370-\u03ff\u1f00-\u1fff][A-Za-zµ])\S*", text)]
    toks = re.findall(r"[Ͱ-Ͽἀ-῿̀-ͯ]+['’ʼ᾽]?|[^\sͰ-Ͽἀ-῿]+", text)
    for i, w in enumerate(toks):
        if (not WORD.fullmatch(w) or not any(c.islower() for c in w) or toks[i + 1:i + 2] in (["ʹ"], ["\u0374"])
                or w[-1] in "ʹ\u0374" or (w == "πλ" and toks[i + 1:i + 2] == ["."])):
            continue  # (capitals, unaccented; a numeral, αʹ; πλ. of a plagal mode)
        d = unicodedata.normalize("NFD", w)
        letters = [(j, c) for j, c in enumerate(d) if not unicodedata.combining(c)]
        accents = [c for c in d if c in "́̀͂"]
        breathings = [j for j, c in enumerate(d) if c in "̓̔"]
        start = bare(w)[:2]
        if d[0].lower() in "αεηιουω" and not breathings:
            out.append((w, "no breathing"))
        for j in breathings:  # (on the first letter, or the second of a diphthong; crasis apart)
            before = sum(1 for k, _ in letters if k < j)
            if (before > 2 or (before == 2 and start not in DIPHTHONGS)) and nfc(w).lower() not in CRASIS:
                out.append((w, "breathing inside the word"))
        elided = w[-1] in "'’ʼ᾽᾿"
        if not accents and not elided and w.lower() not in PROCLITICS and bare(w) not in ENCLITICS and len(bare(w)) > 1:
            out.append((w, "no accent"))
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        last = re.search(r"([^̀-ͯ])([̀-ͯ]*)$", d)
        if (last and "́" in last[2] and len(accents) == 1 and WORD.fullmatch(nxt or "-")
                and bare(nxt) not in ENCLITICS and bare(w) not in ("τις", "τι")):
            out.append((f"{w} {nxt}", "acute before another word"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="list the words that look wrong, and change nothing")
    args = ap.parse_args()
    fetch()
    lib = Library()
    days = sorted(r for r in lib.paths if re.fullmatch(r"(me\.m\d\d\.d\w\w|tr\.d\d{3}|pe\.d\d{3})", r))
    days += [f"oc.m{m}.d1" for m in range(1, 9)]
    corrections = load_corrections()
    rows, titles = [], []
    for resource in days:
        hymns = day_hymns(lib, resource)
        if not hymns:
            continue
        day = resource.removesuffix(".d1") if resource.startswith("oc.") else resource
        fixes = corrections.get(day, []) + corrections.get("*", [])
        title = f"Ἀναστάσιμα, {hymns[0][1]}" if day.startswith("oc.") else heading(lib, resource)
        titles.append((day, title and correct(title, fixes, day)))
        for seq, (kind, mode, poet, text) in enumerate(hymns):
            rows.append((day, seq, kind, mode, poet, correct(text, fixes, day)))
    unused = [(d, w) for d, fixes in corrections.items() for w, _ in fixes if (d, w) not in fixes_used]
    if args.check:
        flagged = [(day, w, why) for day, text in [(r[0], r[5]) for r in rows] + [t for t in titles if t[1]]
                   for w, why in suspicious(text)]
        for day, w, why in flagged:
            print(f"{day}\t{w}\t{why}")
        print(f"{len(rows)} hymns, {len(flagged)} words to look at: {dict(Counter(f[2] for f in flagged))}")
    for d, w in unused:
        print(f"correction not applied (no such word): {d}\t{w}")
    if args.check:
        return
    con = sqlite3.connect(DB)
    con.executescript("""
        DROP TABLE IF EXISTS hymn_days;
        DROP TABLE IF EXISTS hymns;
        CREATE TABLE hymn_days (            -- a day of a service book (scripts/import_hymns.py)
            day   TEXT PRIMARY KEY,         --   me.m11.d25, tr.d014, pe.d110, oc.m5
            title TEXT                      --   its heading, "Μνήμη τῆς Ἁγίας …"
        );
        CREATE TABLE hymns (                -- its apolytikia, then its kontakia
            day   TEXT NOT NULL REFERENCES hymn_days,
            seq   INTEGER NOT NULL,
            kind  TEXT NOT NULL,            --   Ἀπολυτίκιον, Τροπάριον, Κοντάκιον
            mode  TEXT,                     --   Ἦχος πλ. αʹ
            poet  TEXT,                     --   Ποίημα Γερασίμου.
            text  TEXT NOT NULL,
            PRIMARY KEY (day, seq)
        );""")
    con.executemany("INSERT INTO hymn_days VALUES (?, ?)", titles)
    con.executemany("INSERT INTO hymns VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    print(f"{len(titles)} days, {len(rows)} hymns")


if __name__ == "__main__":
    main()
