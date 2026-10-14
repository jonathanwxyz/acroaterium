#!/usr/bin/env python3
"""Load the Perseus/Logeion short English definitions of Greek lemmas
(https://github.com/helmadik/shortdefs) and match them to the treebank's lemmas.

Tables in data/iliad.sqlite:
  shortdefs   (lemma, definition)                  the whole list, ~100k entries
  lemma_defs  (lemma, definition, entry, method)   one row per treebank lemma found

Run after import_treebank.py. Treebank lemmas are matched to entries by, in order:
  exact        the same spelling
  alias        data/lemma_aliases.tsv (Homeric/variant forms; a few own definitions)
  no_diacr.    ignoring diaeresis and iota subscript (Πηλείδης ~ Πηλεΐδης, θνήσκω ~ θνῄσκω)
  accents      ignoring accents too (keeping breathings and case); when that is ambiguous,
               an enclitic written with a final accent (ποτέ) takes the unaccented entry (ποτε)
The list has no licence statement; its README asks for credit to Perseus and Logeion.
"""
import re
import sqlite3
import unicodedata
import urllib.request
from collections import defaultdict
from pathlib import Path

URL = "https://raw.githubusercontent.com/helmadik/shortdefs/master/shortdefsGreekEnglishLogeion"
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "shortdefs_greek.txt"
ALIASES = ROOT / "data" / "lemma_aliases.tsv"
DB = ROOT / "data" / "iliad.sqlite"

DIAERESIS_SUBSCRIPT = "̈ͅ"
ACCENTS = "́̀͂"


def nfc(s):
    return unicodedata.normalize("NFC", s)


def drop_marks(s, marks):
    return nfc("".join(c for c in unicodedata.normalize("NFD", s) if c not in marks))


def final_accent(s):
    """True if the last vowel carries an acute or grave (how AGDT writes enclitics: ποτέ, πέρ)."""
    letters = [c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c)[0] in "LM"]
    for c in reversed(letters):
        if c in ACCENTS:
            return True
        if unicodedata.category(c) == "Ll" and c in "αεηιουω":
            return False
    return False


def load_list():
    if not RAW.exists():
        with urllib.request.urlopen(URL) as r:
            RAW.write_bytes(r.read())
    defs = {}
    for line in RAW.read_text(encoding="utf-8").splitlines():
        lemma, _, definition = line.partition("\t")
        if definition and re.search(r"[Ͱ-Ͽἀ-῿]", lemma):  # skip numeral entries
            defs.setdefault(nfc(lemma.strip()), definition.strip())
    return defs


def load_aliases():
    aliases = {}
    for line in ALIASES.read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip() and not line.startswith("#"):
            lemma, see, definition = (line.split("\t") + ["", ""])[:3]
            aliases[nfc(lemma)] = (nfc(see) if see else None, definition or None)
    return aliases


def matcher(defs=None, aliases=None):
    """match(lemma) -> (definition, entry, method) or None, as described above (also used by
    library.py for its texts' treebank lemmas)."""
    defs = load_list() if defs is None else defs
    aliases = load_aliases() if aliases is None else aliases
    by_no_diacr = defaultdict(list)
    by_no_accent = defaultdict(list)
    for entry in defs:
        by_no_diacr[drop_marks(entry, DIAERESIS_SUBSCRIPT)].append(entry)
        by_no_accent[drop_marks(entry, DIAERESIS_SUBSCRIPT + ACCENTS)].append(entry)

    def match(lemma):
        clean = re.sub(r"^[^\u0370-\u03ff\u1f00-\u1fff]+", "", lemma)  # stray marks before some AGDT lemmas
        for cand in dict.fromkeys((lemma, clean, clean[:1].upper() + clean[1:])):
            if cand in defs:
                return defs[cand], cand, "exact"
        if lemma in aliases:
            see, own = aliases[lemma]
            if own:
                return own, None, "alias"
            if see in defs:
                return defs[see], see, "alias"
        hits = by_no_diacr.get(drop_marks(clean, DIAERESIS_SUBSCRIPT), [])
        if len(hits) == 1:
            return defs[hits[0]], hits[0], "no_diacritics"
        hits = by_no_accent.get(drop_marks(clean, DIAERESIS_SUBSCRIPT + ACCENTS), [])
        if len(hits) > 1 and final_accent(clean):
            hits = [h for h in hits if not any(c in ACCENTS for c in unicodedata.normalize("NFD", h))]
        if len(hits) == 1:
            return defs[hits[0]], hits[0], "accents"
        return None

    return match


def main():
    defs = load_list()
    aliases = load_aliases()
    match = matcher(defs, aliases)
    con = sqlite3.connect(DB)
    lemmas = con.execute(
        "SELECT lemma, count(*) FROM tokens WHERE lemma IS NOT NULL AND pos IS NOT 'punctuation' "
        "GROUP BY lemma").fetchall()
    rows, missing = [], []
    for lemma, n in lemmas:
        m = match(lemma)
        if m:
            rows.append((lemma, *m))
        else:
            missing.append((n, lemma))

    con.executescript(
        """
        DROP TABLE IF EXISTS shortdefs;
        CREATE TABLE shortdefs (
            lemma      TEXT PRIMARY KEY,
            definition TEXT NOT NULL
        ) WITHOUT ROWID;
        DROP TABLE IF EXISTS lemma_defs;
        CREATE TABLE lemma_defs (
            lemma      TEXT PRIMARY KEY,  -- as in tokens.lemma
            definition TEXT NOT NULL,
            entry      TEXT,              -- the shortdefs lemma it came from (NULL: own definition)
            method     TEXT NOT NULL      -- exact, alias, no_diacritics, accents
        ) WITHOUT ROWID;
        """
    )
    con.executemany("INSERT INTO shortdefs VALUES (?, ?)", defs.items())
    con.executemany("INSERT INTO lemma_defs VALUES (?, ?, ?, ?)", rows)
    con.commit()

    covered = sum(n for (lemma, n) in lemmas if lemma not in {m for _, m in missing})
    total = sum(n for _, n in lemmas)
    print(f"{len(defs)} short definitions; {len(rows)}/{len(lemmas)} treebank lemmas matched "
          f"({covered / total:.1%} of words)")
    for method, n in con.execute("SELECT method, count(*) FROM lemma_defs GROUP BY method ORDER BY 2 DESC"):
        print(f"  {method:14} {n}")
    print("most frequent unmatched:", ", ".join(f"{l} ({n})" for n, l in sorted(missing, reverse=True)[:20]))
    con.close()


if __name__ == "__main__":
    main()
