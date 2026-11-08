"""A noun's or an adjective's forms as Homer has them, for the flashcards (serve.py's /api/anki),
where they differ from the Attic ones the card's front gives (scripts/headwords.py) beyond his
regular endings, which dictionaries leave to the Attic form (πολέμοιο, πέτρης, βασιλῆος, κρατερή):
his own nominative (νηῦς, ξεῖνος), another stem (Ζεύς, Διός / Ζηνός; δόρυ, δουρός), another
feminine (μέσση; ἀθάνατος, -η, -ον).

From the Iliad's and the Odyssey's treebanks: the forms Homer's words have, the commonest (and
the next, where Homer has it often too). A Homeric word (lemma_notes.epic, one LSJ cites from Homer
and the epic poets only) has them as its front, another at the head of its Homeric line. A Homeric
adjective has the endings Cunliffe's headword gives it where they aren't the front's in number
(ἐννεάπηχυς, -υ: no feminine of its own, where Morpheus made one).

    python3 scripts/homeric_forms.py πόλεμος ναῦς ἀθάνατος ἐννεάπηχυς   (to try some)
"""
import collections
import re
import unicodedata

import headwords
import lemma_definitions
from headwords import bare, ending

HOMER = ("iliad", "odyssey")
_attested = {}  # library's mtime -> {lemma: {slot: Counter of forms}}
_cunliffe = []  # [{headword: its entries}], once read
ARTICLE = {"ὁ", "ἡ", "τό", "ὁ,", "ἡ,"}
FEMININE = re.compile(r"(η|α|σσα|ουσα|εια|αινα)$")  # (a feminine's ending, in bare letters)
NEUTER = re.compile(r"(ον|ο|υ|ες|εν|αν)$")


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
        if postag[7] == "g" and (postag[0] == "n" or postag[6] in ("m", "-")) \
                and "\u0345" not in unicodedata.normalize("NFD", form):  # (no genitive has ι subscript: πνοιῇς, a dative)
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


def cunliffe_head(lemma):
    """Cunliffe's headword line for lemma (ῥωγαλέος, η, -ον), to its etymology or first stop; None
    where it hasn't the word, or isn't at hand (data/raw/definitions/cunliffe.txt, which
    scripts/lemma_definitions.py fetches: never fetched here)."""
    if not _cunliffe:
        path = lemma_definitions.RAW / "cunliffe.txt"
        _cunliffe.append(lemma_definitions.cunliffe() if path.exists() else {})
    entries = lemma_definitions.lookup(_cunliffe[0], lemma)
    if not entries:
        return None
    head = re.split(r"\s\[|\.\s", entries[0], maxsplit=1)[0]
    if (fem := re.search(r"Fem\.\s*(-?[^\s,.]+)", entries[0][:200])) and "Fem." not in head:
        head += f". Fem. {fem[1]}"
    return lemma_definitions.plain(head)


def cunliffe_shape(lemma):
    """An adjective's endings as Cunliffe's headword gives them: ("two", neuter) where it gives no
    feminine of its own (ἐννεάπηχυς, υ), ("three", feminine, neuter or None) where it does
    (ῥωγαλέος, η, -ον; κτίδεος, η; παιπαλόεις, εντος. Fem. -εσσα); None where it gives none."""
    head = cunliffe_head(lemma)
    if not head:
        return None
    dash = lambda t: t if t.startswith("-") or len(bare(t)) > 4 else "-" + t
    if fem := re.search(r"Fem\.\s*(-?[^\s,.]+)", head):
        return ("three", dash(fem[1]), None)
    if " and " in head:  # (πολύφορβος, ον, and -η, -ον: both; the one with a feminine)
        head = head.split(",")[0] + ", " + head.split(" and ", 1)[1]
    tokens = [t.strip(" ,.") for t in head.split(",")[1:]]
    tokens = [t for t in tokens if t and t not in ARTICLE and not re.search("(ος|ου|ης)$", bare(t))]
    if not tokens:
        return None
    if len(tokens) >= 2:
        return ("three", dash(tokens[0]), dash(tokens[1]))
    if FEMININE.search(bare(tokens[0])):
        return ("three", dash(tokens[0]), None)
    if NEUTER.search(bare(tokens[0])):
        return ("two", dash(tokens[0]))
    return None


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


def morpheus_fem(forms):
    """A Homeric adjective's feminine from Morpheus's forms: the epic or Ionic one (ἀκαχμένη), else the plain."""
    fems = [(f, t) for f, t in forms if {"fem", "nom", "sg"} <= t and not t & {"comp", "superl"}]
    return next((f for f, t in fems if t & {"epic", "ionic", "homeric"}), None) \
        or next((f for f, t in fems if not t & headwords.DIALECTS), None)


def same(lemma, form, said):
    """Whether form is what an Attic front says (said: -ατος or δώματος, for δῶμα's δώματος)."""
    return said in (form, ending(lemma, form), homer_ending(lemma, form))


# Homer's regular endings, as the Attic ones they stand for (bare letters): what a reader of Homer
# knows from the Attic form, and the card needn't say (πολέμοιο for πολέμου; πέτρης for πέτρας).
REGULAR = [("οιο", "ου"), ("αο", "ου"), ("εω", "ου"), ("ας", "ης"), ("ους", "#"), ("εος", "#"), ("ευς", "#"),
           ("εως", "#"), ("ηος", "#"), ("ιος", "#"), ("αος", "#"), ("ως", "#")]  # (γήραος, γήρως)


def regular(form):
    """A genitive (bare) with Homer's regular endings as the Attic ones: πολεμοιο → πολεμου."""
    for homer, attic in REGULAR:
        if form.endswith(homer):
            return form[:-len(homer)] + attic
    if len(form) > 2 and form.endswith("ω") and form[-2] in headwords.VOWELS:  # (Αἰνείω, contracted)
        return form[:-1] + "ου"
    return form


def whole(lemma, said):
    """A form an Attic front gives as an ending (-ου, -ατος, -ά), whole, in bare letters: from the
    lemma's last vowel, or the letter like the ending's first just before it (πόλεμ|ου, δῶμ|ατος,
    βασιλ|έως, μακρ|ά)."""
    if not said.startswith("-"):
        return bare(said)
    e, b = bare(said[1:]), bare(lemma)
    v = max((i for i, c in enumerate(b) if c in headwords.VOWELS), default=len(b))
    i = next((i for i in range(len(b) - 1, max(v - 1, 0) - 1, -1) if b[i] == e[:1]), v)
    return b[:i] + e


def same_nominative(lemma, nom):
    """Homer's nominative the lemma's but for his η for α (πέτρη, βίη)."""
    return bare(nom) == bare(lemma) or bare(nom) == re.sub("α(ς?)$", r"η\1", bare(lemma))


def irregular_gens(lemma, nom, gens, attic):
    """Whether Homer's genitives (or his nominative) aren't the Attic front's with his regular endings:
    Ζηνός, ἀνέρος, δουρός, νηῦς (not πολέμοιο, πόλιος, βασιλῆος, πέτρη)."""
    if not same_nominative(lemma, nom):
        return True
    if not attic or not attic[2]:
        return True
    a = regular(re.sub("ας$", "ης", whole(lemma, attic[2][0])))
    said = regular(re.sub("ας$", "ης", bare(attic[2][0])))
    # (each as the whole form, or as the front's ending: νέκυς, -ος, which is νέκυος)
    return any(regular(bare(g)) != a and regular(bare(ending(lemma, g) or g)) != said for g in gens)


def irregular_fem(lemma, fem, attic):
    """Whether Homer's feminine isn't the Attic front's with his η for α: μέσση, λίγεια, ὠκέα (not
    κρατερή); or the front has none (ἀθάνατος, -ον: Homer's ἀθανάτη)."""
    if not attic or len(attic[2]) < 2:
        return True
    norm = lambda f: re.sub("η$", "α", f)
    return norm(whole(lemma, fem)) != norm(whole(lemma, attic[2][0])) and norm(bare(fem)) != norm(bare(attic[2][0]))


def attic_parts(front):
    """An Attic front's parts (headwords'): ("ὁ", "πόλεμος", ["-ου"]), ("", "ἀγαθός", ["-ή", "-όν"])."""
    if not front:
        return None
    head, *rest = [p.strip() for p in front.split(",")]
    words = head.split(" ")
    article = " ".join(words[:-1])
    return article, words[-1], rest


def homeric(con, stamp, lemmas, epic=()):
    """{lemma: (front, line)} for [(lemma, kind, attic front or None)] (kind noun or adj): Homer's
    forms where they differ from the Attic front's beyond his regular endings (which the Attic form
    implies, as dictionaries leave them: πολέμοιο, πέτρης, κρατερή): his own nominative (νηῦς,
    ξεῖνος), stem (Ζηνός, δουρός), feminine (μέσση; ἀθάνατος, -η, -ον). A Homeric word's (in epic)
    as its front, another's for its Homeric line (Ζεύς, Διός / Ζηνός)."""
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
            if not gens or not irregular_gens(lemma, nom, gens, attic):
                continue  # (Homer's regular endings, which the Attic front's imply: πολέμοιο for πολέμου)
            said = ends(nom, gens)
            if lemma in epic:
                if attic:
                    out[lemma] = (f"{attic[0]} {nom}, {said}".strip(), None)
            else:
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
            shape = cunliffe_shape(lemma) if lemma in epic and not fem else None
            if shape and shape[0] == "two":  # (Cunliffe: no feminine of its own, where the front gives one)
                if not attic or len(attic[2]) != 1:
                    out[lemma] = (f"{lemma}, {shape[1]}", None)
                continue
            if shape:
                fem = shape[1]
                if attic and len(attic[2]) == 2 and bare(fem).endswith(bare(attic[2][0])) or \
                        attic and len(attic[2]) == 2 and bare(attic[2][0]).endswith(bare(fem)):
                    shape, fem = None, attic[2][0]  # (the same: χαλκοβαρής, -βάρεια, as the front's -άρεια)
            if not fem:  # (Morpheus's, Homer's regular endings: the Attic front stands, as dictionaries give it)
                continue
            fem_end = fem if fem.startswith("-") else (ending(lemma, fem) or fem)
            if fem == lemma or not fem.startswith("-") and (bare(fem)[-2:] == bare(lemma)[-2:] or re.search("(ος|ως)$", bare(fem))):
                # (two endings: the masculine's, or its contraction, ἀγήρως)
                continue
            neut = shape[2] if shape and shape[2] else attic[2][-1] if attic and len(attic[2]) >= 1 else None
            if not neut:
                made_n = [f for f, t in made.get(lemma, []) if {"neut", "nom", "sg"} <= t and not t & {"comp", "superl"}]
                neut = made_n and (ending(lemma, made_n[0]) or made_n[0])
            parts = [lemma, fem_end] + ([neut] if neut else [])
            said = ", ".join(parts)
            if not attic and lemma in epic and not shape:
                continue
            if not irregular_fem(lemma, fem_end, attic):
                continue
            out[lemma] = (said, None) if lemma in epic else (None, said)
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
