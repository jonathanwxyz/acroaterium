"""A noun's or an adjective's forms as Homer has them, for the flashcards (serve.py's /api/anki):
the genitive (πόλεμος, -οιο / -ου; νηῦς, νηός) and an adjective's feminine (ἀθάνατος, -η, -ον),
where they differ from the Attic ones the card's front gives (scripts/headwords.py).

From the Iliad's and the Odyssey's treebanks: the forms Homer's words have, the commonest (and
the next, where Homer has it often too: πολέμοιο 99, πολέμου 25). A Homeric word (lemma_notes.epic,
one LSJ cites from Homer and the epic poets only), which Homer may not have in the genitive at all,
has Morpheus's epic forms instead (ἐγχείη, -ης; βρότος, -οιο / -ου).

    python3 scripts/homeric_forms.py πόλεμος ναῦς ἀθάνατος ἐγχείη   (to try some)
"""
import collections
import re
import unicodedata

import headwords
from headwords import bare, ending

HOMER = ("iliad", "odyssey")
_attested = {}  # library's mtime -> {lemma: {slot: Counter of forms}}


def plain(form):
    """A form of the treebank's as a dictionary writes it: small (but a name's capital), its grave
    acute, an enclitic's extra accent gone (θεοῖό: θεοῖο), its diaeresis kept; None if elided."""
    if re.search(r"[’'ʼ᾽\u0313]$", unicodedata.normalize("NFD", form)):
        return None
    letters = []
    for c in unicodedata.normalize("NFD", form.lower()):
        if unicodedata.combining(c) and letters:
            letters[-1] += c.replace("̀", "́")
        else:
            letters.append(c)
    accented = [i for i, x in enumerate(letters) if any(m in x for m in "́͂")]
    for i in accented[1:]:  # (the second, from an enclitic after it)
        letters[i] = letters[i].replace("́", "")
    return unicodedata.normalize("NFC", "".join(letters))


def attested(con, stamp):
    """{lemma: {"gen": Counter, "nom": Counter, "fem": Counter, "femsg": Counter}}: the singular's
    genitives and nominatives of Homer's nouns and adjectives, an adjective's feminine nominatives,
    and its feminine singulars of any case (which say whether it has -η or -α)."""
    if stamp in _attested:
        return _attested[stamp]
    out = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for lemma, postag, form in con.execute(
            "SELECT l.lemma, t.postag, t.form FROM treebank t JOIN lemma_words w ON w.text = t.text AND w.div = t.div "
            "AND w.seq = t.seq AND w.word_index = t.word_index AND w.part = t.part JOIN lemmas l ON l.id = w.lemma "
            f"WHERE t.text IN ({','.join('?' * len(HOMER))}) AND t.postag IS NOT NULL AND t.form IS NOT NULL", HOMER):
        if len(postag) < 8 or postag[0] not in "na" or postag[2] != "s" or postag[8:9] in ("c", "s"):
            continue
        form = plain(form)
        if not form or re.search("(θι|θεν|φιν?)$", bare(form)):  # (ἠῶθι, -φι: not the case a dictionary gives)
            continue
        if postag[7] == "g" and (postag[0] == "n" or postag[6] in ("m", "-")):
            out[lemma]["gen"][form] += 1
        if postag[7] == "n" and (postag[0] == "n" or postag[6] in ("m", "-")):
            out[lemma]["nom"][form] += 1
        if postag[0] == "a" and postag[6] == "f":
            out[lemma]["femsg"][form] += 1
            if postag[7] == "n":
                out[lemma]["fem"][form] += 1
    _attested.clear()
    _attested[stamp] = out
    return out


def kept(counts):
    """The commonest form, and the next where Homer has it often too (twice, and a twelfth as often: Διός 253, Ζηνός 22)."""
    ranked = counts.most_common()
    top = ranked[0][1]
    return [f for i, (f, n) in enumerate(ranked) if i == 0 or (n >= 2 and n >= top / 12)][:2]


def capital(form, lemma):
    """form with lemma's capital, if it has one (Ἀχιλλεύς: Ἀχιλῆος)."""
    return form[:1].upper() + form[1:] if lemma[:1].isupper() else form


def homer_ending(nom, form):
    """form as an ending after nom (headwords.ending), from nom's last vowel at least: πόλις,
    πόλιος: -ιος, not -ος (what the Attic -εως needn't)."""
    e = ending(nom, form)
    if not e:
        return None
    b, f = bare(nom), bare(form)
    k = len(b)
    while k > 0 and b[k - 1] not in headwords.VOWELS:
        k -= 1
    k -= 1  # (its last vowel, or diphthong: βασιλ|εύς, ἐγχεί|η)
    if k > 0 and b[k - 1:k + 1] in headwords.DIPHTHONGS:
        k -= 1
    letters = []  # the form's letters, each with its marks
    for c in unicodedata.normalize("NFD", form):
        if unicodedata.combining(c) and letters:
            letters[-1] += c
        else:
            letters.append(c)
    start = len(letters) - len(bare(e))
    if f[:k] != b[:k]:  # (another stem: ἀτραπός, ἀταρπιτοῦ; ἑταῖρος, ἑτάροιο, whole)
        return None
    if k < start:
        return "-" + unicodedata.normalize("NFC", "".join(letters[k:]).lower())
    return e


def ends(nom, forms):
    """Forms as endings after nom where they will do (-οιο / -ου), else whole (Διός / Ζηνός;
    Ἀχιλῆος / Ἀχιλλῆος, the same ending)."""
    out = [homer_ending(nom, f) for f in forms]
    return " / ".join(out) if all(out) and len(set(out)) == len(out) else " / ".join(forms)


def morpheus_gen(forms):
    """A Homeric word's genitive from Morpheus's forms: the epic one (ἐγχείης, βρότοιο), and the
    plain one too where the epic one is only an alternative (-οιο / -ου, not -ης / -ας), and a
    masculine in -ης's -εω and -αο."""
    gens = [(f, t) for f, t in forms if "gen" in t and "sg" in t and not t & {"comp", "superl", "contr"}]
    # (Homer's: δολομήτεω, δολομήταο; not the contracted δολομήτω, nor a Doric's only)
    epic = sorted([(f, t) for f, t in gens if t & {"epic", "ionic", "homeric"}], key=lambda x: bool(x[1] & {"doric", "aeolic"}))
    plain_ = next((f for f, t in gens if not t & headwords.DIALECTS), None)
    if not epic:
        return [plain_] if plain_ else []
    first = epic[0]
    out = [first[0]]
    if bare(first[0]).endswith("εω"):  # (a masculine in -ης: -εω and -αο both, Πηληϊάδεω, Ἀτρεΐδαο)
        out += [f for f, _ in epic if bare(f).endswith("αο")][:1]
    elif plain_ and bare(first[0]).endswith("οιο"):  # (βρότοιο / βρότου: Homer has both)
        out.append(plain_)
    return out


def morpheus_fem(forms):
    """A Homeric adjective's feminine from Morpheus's forms: the epic or Ionic one (ἀκαχμένη), else the plain."""
    fems = [(f, t) for f, t in forms if {"fem", "nom", "sg"} <= t and not t & {"comp", "superl"}]
    return next((f for f, t in fems if t & {"epic", "ionic", "homeric"}), None) \
        or next((f for f, t in fems if not t & headwords.DIALECTS), None)


def same(lemma, form, said):
    """Whether form is what an Attic front says (said: -ατος or δώματος, for δῶμα's δώματος)."""
    return said in (form, ending(lemma, form), homer_ending(lemma, form))


def attic_parts(front):
    """An Attic front's parts (headwords'): ("ὁ", "πόλεμος", ["-ου"]), ("", "ἀγαθός", ["-ή", "-όν"])."""
    if not front:
        return None
    head, *rest = [p.strip() for p in front.split(",")]
    words = head.split(" ")
    article = " ".join(words[:-1])
    return article, words[-1], rest


def homeric(con, stamp, lemmas, epic=()):
    """{lemma: (front, line)} for [(lemma, kind, attic front or None)] (kind noun or adj): a Homeric
    word's (in epic) front in Homer's forms (ὁ βρότος, -οιο / -ου), another's forms as Homer has
    them, for its Homeric line (πόλεμος, -οιο / -ου), where they differ from the Attic front's."""
    seen = attested(con, stamp)
    epic = set(epic)
    need = [l for l, kind, _ in lemmas if l in epic and kind in ("noun", "adj")]
    made = headwords.generate(need, dialects=True) if need and headwords.available() else {}
    out = {}
    for lemma, kind, front in lemmas:
        if kind not in ("noun", "adj"):
            continue
        has = seen.get(lemma, {})
        attic = attic_parts(front)
        if kind == "noun":
            noms = has.get("nom")
            nom = lemma
            if noms:  # (Homer's own nominative, where he has another: νηῦς for ναῦς)
                top, n = noms.most_common(1)[0]
                if n >= 3 and bare(top) != bare(lemma) and noms[lemma] * 2 < n:
                    nom = capital(top, lemma)
            gens = [capital(f, lemma) for f in kept(has["gen"])] if has.get("gen") else []
            if not gens and lemma in epic and not (attic and attic[0] in ("οἱ", "αἱ", "τὰ")):  # (not a plural's)
                gens = morpheus_gen(made.get(lemma, []))
            if not gens:
                continue
            said = ends(nom, gens)
            if lemma in epic:
                article = attic[0] if attic and attic[0] else ""
                out[lemma] = (f"{article} {nom}, {said}".strip(), None)
            elif nom != lemma or not attic or not attic[2] or not all(same(lemma, g, attic[2][0]) for g in gens):
                out[lemma] = (None, f"{nom}, {said}")
        else:
            fem = None
            fems = collections.Counter({f: n for f, n in (has.get("fem") or {}).items()
                                        if all(m not in bare(f) or m in bare(lemma) for m in ("τατ", "τερ"))})
            if fems and (lemma not in epic or fems.most_common(1)[0][1] >= 2 or not morpheus_fem(made.get(lemma, []))):
                fem = fems.most_common(1)[0][0]  # (not φερτέρη for φέρτατος; a Homeric word's, twice at least)
            elif has.get("femsg") and attic and attic[2]:
                # (no nominative, but its -η, -ην: the Attic -α's, as Homer has it; not ταχεῖα's, whose
                # genitive alone has η, ταχείης)
                if attic[2][0].startswith("-") and bare(attic[2][0]).endswith("α") \
                        and any(re.search("ην?$", bare(f)) and "\u0345" not in unicodedata.normalize("NFD", f)
                                for f in has["femsg"]) \
                        and not any(re.search("αν?$", bare(f)) for f in has["femsg"]):
                    a = attic[2][0]
                    fem = a[:-1] + {"α": "η", "ά": "ή", "ᾶ": "ῆ"}.get(a[-1], a[-1])
            if not fem and lemma in epic:
                fem = morpheus_fem(made.get(lemma, []))
            if not fem:
                continue
            fem_end = fem if fem.startswith("-") else (ending(lemma, fem) or fem)
            if fem == lemma or bare(fem)[-2:] == bare(lemma)[-2:] and not fem.startswith("-"):  # (two endings: the masculine's)
                continue
            neut = attic[2][-1] if attic and attic[2] else None
            if not neut:
                made_n = [f for f, t in made.get(lemma, []) if {"neut", "nom", "sg"} <= t and not t & {"comp", "superl"}]
                neut = made_n and (ending(lemma, made_n[0]) or made_n[0])
            parts = [lemma, fem_end] + ([neut] if neut else [])
            said = ", ".join(parts)
            if lemma in epic:
                out[lemma] = (said, None)
            elif not attic or len(attic[2]) < 2 or not (same(lemma, fem, attic[2][0]) or bare(attic[2][0]) == bare(fem_end)):
                out[lemma] = (None, said)
    return out


if __name__ == "__main__":
    import sqlite3
    import sys
    import serve
    con = sqlite3.connect(serve.LIBRARY)
    kinds = serve.lemma_kinds(con)
    asked = [(w, kinds.get(w, ("other", {}))[0]) for w in sys.argv[1:]]
    fronts = headwords.headwords([(w, k, kinds.get(w, ("other", {}))[1]) for w, k in asked])
    epic = {r[0] for r in con.execute("SELECT l.lemma FROM lemma_notes n JOIN lemmas l ON l.id = n.lemma WHERE n.epic = 1")}
    found = homeric(con, serve.LIBRARY.stat().st_mtime, [(w, k, fronts.get(w)) for w, k in asked], epic)
    for w, k in asked:
        print(w, k, "| Attic:", fronts.get(w), "| Homeric:", found.get(w))
