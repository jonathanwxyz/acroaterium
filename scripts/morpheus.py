"""Morpheus (Perseus's morphological analyser, the perseids-tools build), for the library's
search: every lemma a word may be of, with the analyses under each (λόγου: λόγος, masc gen
sg; λογόω, pres imperat act 2nd sg, ...).

It's built into data/morpheus by scripts/install_morpheus.sh (or MORPHEUS names a build
elsewhere, ~/code/lemmatisation/models/morpheus say); without it, analyse gives nothing.
Its `cruncher` reads Beta Code (lo/gou) and writes the analyses:

    <NL>N lo/gos  masc gen sg\t\t\tos_ou</NL><NL>V logo/w  pres imperat act 2nd sg\t...</NL>

(with -n, accents ignored, the form as accented first: "lo/gou,lo/gos").
"""
import functools
import os
import re
import subprocess
import unicodedata
from pathlib import Path

from import_commentaries import beta_to_unicode

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("MORPHEUS", ROOT / "data" / "morpheus"))
CRUNCHER = HOME / "bin" / "cruncher"

LETTERS = dict(zip("αβγδεζηθικλμνξοπρστυφχψως", "abgdezhqiklmncoprstufxyws"))
MARKS = {"̓": ")", "̔": "(", "̈": "+", "́": "/", "̀": "/", "͂": "=", "ͅ": "|"}
ORDER = ")(+/=|"  # (as Morpheus has them: breathing, diaeresis, accent, iota subscript)
ELISION = "’ʼ᾽᾿'"


def available():
    return os.access(CRUNCHER, os.X_OK)


def to_beta(word):
    """A Greek word in Beta Code (λόγου: lo/gou; Ἀχιλλεύς: *)axilleu/s; a grave as an acute,
    an elision mark as '), or None where it has what isn't a Greek letter."""
    out = []
    for c in unicodedata.normalize("NFD", word):
        if c.lower() in LETTERS:
            out.append(("*" if c.isupper() else "") + LETTERS[c.lower()])
        elif c in MARKS and out:
            out[-1] += MARKS[c]
        elif c in ELISION and out:
            out.append("'")
        else:
            return None
    # a capital's marks come before its letter (*)a), the others' after (a)), in order
    return "".join(f"*{''.join(sorted(b[2:], key=ORDER.index))}{b[1]}" if b[0] == "*"
                   else b[0] + "".join(sorted(b[1:], key=ORDER.index)) for b in out)


def marked(word):
    return any(c in MARKS for c in unicodedata.normalize("NFD", word))


def crunch(beta, ignore_accents):
    """[(form or None, lemma, analysis, dialects)] in Beta Code, as Morpheus gives them."""
    try:
        proc = subprocess.run([str(CRUNCHER), "-S"] + (["-n"] if ignore_accents else []),
                              input=beta + "\n", capture_output=True, text=True, timeout=5,
                              env={**os.environ, "MORPHLIB": str(HOME / "stemlib")})
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for m in re.finditer(r"<NL>\S+ (\S+) ([^<]*)</NL>", proc.stdout):
        form, _, lemma = m[1].rpartition(",")
        fields = [f.strip() for f in m[2].split("\t")]
        out.append((form or None, lemma, fields[0], fields[1] if len(fields) > 1 else ""))
    return out


@functools.lru_cache(maxsize=4096)
def analyse(word):
    """[{lemma, form, parses}]: the lemmas a word (as typed: accents, where it has them, are
    held to; capitals tried in small letters too) may be of, in Morpheus's order, each with
    the word as accented and its analyses ("masc gen sg", "aor ind act 3rd sg (epic ionic)")."""
    word = unicodedata.normalize("NFC", word.strip())
    if not word or not available():
        return []
    rows = []
    for w in dict.fromkeys([word, word.lower()]):
        if (beta := to_beta(w)) is None:
            return []
        rows = (marked(w) and crunch(beta, False)) or crunch(beta, True)
        if rows:
            break
    bare = lambda s: "".join(c for c in unicodedata.normalize("NFD", s.lower()) if c not in MARKS)
    found = {}
    for form, lemma, analysis, dialects in rows:
        lemma = beta_to_unicode(re.sub(r"[_^]|\d+$", "", lemma))  # (_ ^: long, short; λέγω2)
        form = beta_to_unicode(form) if form else w
        x = found.setdefault(lemma, {"lemma": lemma, "form": form if bare(form) == bare(w) else w, "parses": {}})
        if analysis:  # (an analysis in some dialects only, as such; one in all, without them)
            had = x["parses"].setdefault(analysis, set())
            if had is not None:
                x["parses"][analysis] = had | set(dialects.split()) if dialects else None
    return [{**x, "parses": [a + (f" ({' '.join(sorted(d))})" if d else "") for a, d in x["parses"].items()]}
            for x in found.values()]

if __name__ == "__main__":
    import sys
    for w in sys.argv[1:]:
        print(w, analyse(w))
