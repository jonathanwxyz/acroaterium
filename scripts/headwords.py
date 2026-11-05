"""A noun's or an adjective's dictionary form, for the flashcards (serve.py's /api/anki):
a noun with its article and its genitive's ending (ὁ λόγος, -ου; ἡ πόλις, -εως; τὸ ὄνομα,
-ατος; ὁ ἀγών, -ῶνος), an adjective with its feminine's and neuter's (ἀγαθός, -ή, -όν;
ἄδικος, -ον; ἀληθής, -ές), the whole word where the ending wouldn't say it (ἡ γυνή,
γυναικός; ἡ νύξ, νυκτός; πᾶς, πᾶσα, πᾶν).

The forms are Morpheus's (data/morpheus, scripts/install_morpheus.sh): its stem files give
each lemma's stem, its class (os_ou masc: λόγος) and gender, and its generator, `gener`,
gives the lemma's forms from them, accented; the Attic ones are taken (not λόγοιο, epic).
Without Morpheus, or for a lemma it hasn't, there's none.

    python3 scripts/headwords.py λόγος ἀγαθός γυνή   (to try some)
"""
import os
import re
import subprocess
import threading
import unicodedata

import morpheus
from import_commentaries import beta_to_unicode

STEMS = morpheus.HOME / "stemlib" / "Greek" / "stemsrc"
GENER = morpheus.HOME / "bin" / "gener"
# The stem files, the general ones first (a lemma's entry is the first found): then those
# of the irregular words, then those made from one author's words.
FIRST = ["lsj.nom", "nom01", "nom02", "nom03", "nom04", "nom05", "nom06", "nom07", "irreg.nom.src", "nom.irreg"]
# A form so tagged is a dialect's or a poet's, not the dictionary's (Attic).
DIALECTS = {"epic", "ionic", "doric", "aeolic", "poetic", "homeric", "lyric", "later", "late", "byz", "tragic",
            "nu_movable", "laconia", "laconian", "boeotian", "cretan", "arcadian", "cyprian", "thessalian", "elean",
            "argive", "megarian", "corinthian", "rhodian", "lesbian", "syracusan", "hellenistic"}
ARTICLES = {"masc": "ὁ", "fem": "ἡ", "neut": "τὸ"}
VOWELS = set("αεηιουω")

# The commonest irregular nouns, whose genitives Morpheus hasn't, or has wrong (ἀνέρος for
# ἀνδρός): (article, genitive).
IRREGULAR_NOUNS = {
    "ἀνήρ": ("ὁ", "ἀνδρός"), "πατήρ": ("ὁ", "πατρός"), "μήτηρ": ("ἡ", "μητρός"), "θυγάτηρ": ("ἡ", "θυγατρός"),
    "γαστήρ": ("ἡ", "γαστρός"), "γυνή": ("ἡ", "γυναικός"), "χείρ": ("ἡ", "χειρός"), "ναῦς": ("ἡ", "νεώς"),
    "ὕδωρ": ("τὸ", "ὕδατος"), "παῖς": ("ὁ/ἡ", "παιδός"), "πούς": ("ὁ", "ποδός"), "μήν": ("ὁ", "μηνός"),
    "φρήν": ("ἡ", "φρενός"), "βοῦς": ("ὁ/ἡ", "βοός"), "δόρυ": ("τὸ", "δόρατος"), "γόνυ": ("τὸ", "γόνατος"),
    "οὖς": ("τὸ", "ὠτός"), "φῶς": ("τὸ", "φωτός"), "ἔρως": ("ὁ", "ἔρωτος"), "κύων": ("ὁ/ἡ", "κυνός"),
    "ἅλς": ("ὁ", "ἁλός"), "ὗς": ("ὁ/ἡ", "ὑός"), "θρίξ": ("ἡ", "τριχός"), "υἱός": ("ὁ", "υἱοῦ"),
    "ὀσφῦς": ("ἡ", "ὀσφύος"), "κάρα": ("τὸ", "κρατός"), "γραῦς": ("ἡ", "γραός"), "πῦρ": ("τὸ", "πυρός"),
    "γάλα": ("τὸ", "γάλακτος"), "ἦπαρ": ("τὸ", "ἥπατος"), "μάρτυς": ("ὁ", "μάρτυρος"), "ἄστυ": ("τὸ", "ἄστεως"),
    "πρέσβυς": ("ὁ", "πρέσβεως"), "κρέας": ("τὸ", "κρέως"), "γέρας": ("τὸ", "γέρως"), "τέρας": ("τὸ", "τέρατος"),
    "κέρας": ("τὸ", "κέρως"), "αἰδώς": ("ἡ", "αἰδοῦς"), "ἥρως": ("ὁ", "ἥρωος"), "ἀστήρ": ("ὁ", "ἀστέρος"),
    "ὄρνις": ("ὁ/ἡ", "ὄρνιθος"), "νηῦς": ("ἡ", "νηός"), "γῆ": ("ἡ", "γῆς"), "ἀμνός": ("ὁ", "ἀμνοῦ"), "Ἰησοῦς": ("ὁ", "Ἰησοῦ"), "κλέος": ("τὸ", "κλέους"), "Ζεύς": ("ὁ", "Διός"),
}
# The commonest irregular adjectives, whole: masculine, feminine, neuter.
IRREGULAR_ADJECTIVES = {
    "πᾶς": "πᾶς, πᾶσα, πᾶν", "μέγας": "μέγας, μεγάλη, μέγα", "πολύς": "πολύς, πολλή, πολύ",
    "πρᾶος": "πρᾶος, πραεῖα, πρᾶον", "εἷς": "εἷς, μία, ἕν", "οὐδείς": "οὐδείς, οὐδεμία, οὐδέν",
    "μηδείς": "μηδείς, μηδεμία, μηδέν", "ἅπας": "ἅπας, ἅπασα, ἅπαν", "σύμπας": "σύμπας, σύμπασα, σύμπαν",
    "τρεῖς": "τρεῖς, τρία", "τέσσαρες": "τέσσαρες, τέσσαρα", "δύο": "δύο",
}

_entries = None
_lock = threading.Lock()
_cache = {}  # lemma -> headword (or None)


def available():
    return GENER.is_file() and STEMS.is_dir()


def entries():
    """{lemma in Beta Code: its stem entry's lines}, from Morpheus's noun and adjective stem files."""
    global _entries
    with _lock:
        if _entries is None:
            out = {}
            files = [STEMS / f for f in FIRST] + sorted(p for p in STEMS.iterdir()
                                                       if p.name not in FIRST and "nom" in p.name and "." not in p.name[:3]
                                                       and not p.name.endswith((".c", ".failed")))
            for path in files:
                if not path.is_file():
                    continue
                lemma, lines = None, []
                for line in path.read_text(errors="ignore").splitlines() + [":le:"]:
                    if line.startswith(":le:"):
                        if lemma and lines and lemma not in out:
                            out[lemma] = lines
                        lemma, lines = re.sub(r"\d+$", "", line[4:].strip()), []
                    elif line.strip() and not line.startswith("#") and lemma:
                        lines.append(line.rstrip())
            _entries = out
    return _entries


def dialectal(tags):
    """A form (or a stem) so tagged is a dialect's or a poet's, and not Attic too."""
    return bool(tags & DIALECTS) and "attic" not in tags


def form_of(beta):
    """A form of Morpheus's in Unicode (NFC): its quantities (_ ^) and a compound's join (-) dropped."""
    return unicodedata.normalize("NFC", beta_to_unicode(re.sub(r"[-_^!]", "", beta)))


def generate(lemmas):
    """{lemma: [(form, tags)]}: each lemma's forms, Unicode, NFC: an irregular word's as its
    entry lists them (:wd:), the others as Morpheus's generator makes them from its stems
    (the Attic ones, where a dialect's are given too: θεός's, not Laconian σιός's). Those
    Morpheus hasn't are left out."""
    stems = entries()
    asked, out = {}, {}
    for lemma in lemmas:
        beta = morpheus.to_beta(lemma)
        if not beta or beta not in stems:
            continue
        for line in stems[beta]:
            if line.startswith(":wd:") or (not line.startswith(":") and "@" not in line):  # (a whole word)
                form, *tags = line.removeprefix(":wd:").split()
                out.setdefault(lemma, []).append((form_of(form), set(" ".join(tags).replace("/", " ").split())))
        lines = [l for l in stems[beta] if l.startswith((":no:", ":aj:"))]
        plain = [l for l in lines if not dialectal(set(l.split()))]
        if plain or lines:
            asked[beta] = (lemma, plain or lines)
    todo = list(asked)
    while todo:  # (the generator dies on the odd entry: on from the one after it)
        text = "".join(f":le:{beta}\n" + "\n".join(asked[beta][1]) + "\n\n" for beta in todo)
        try:
            proc = subprocess.run(["stdbuf", "-oL", str(GENER)], input=text, capture_output=True, text=True, timeout=120,
                                  env={**os.environ, "MORPHLIB": str(morpheus.HOME / "stemlib")})
        except (OSError, subprocess.SubprocessError):
            break
        current, last = None, None
        for line in proc.stdout.splitlines():
            if line.startswith(":le:"):
                last = line[4:].strip()
                current = asked.get(last, (None,))[0]
            elif current and (m := re.match(r"<G>([^<]+)</G>\s+(.*)", line)):
                out.setdefault(current, []).append((form_of(m[1]), set(m[2].replace("/", " ").split())))
        if proc.returncode == 0 or last not in todo:
            break
        todo = todo[todo.index(last) + 1:]
    return out


def pick(forms, *want, like=None, degree=False):
    """The form tagged with all of want, in the dialect of like (a form's tags: πάτρη's Ionic,
    πάτρης), else the plainest: untagged (δονακέως), then Attic (τιμῆς, Attic, epic, Ionic),
    none a dialect's only."""
    hits = [(f, t) for f, t in forms if all(w in t for w in want)
            and (degree or not t & {"comp", "superl"})]  # (not κακίων for κακός's feminine; but of κρείσσων, its own)
    if like and (dialects := like[1] & DIALECTS):
        best = max(hits, key=lambda h: len(h[1] & dialects), default=None)
        if best and best[1] & dialects:
            return best
    return next((h for h in hits if not h[1] & (DIALECTS | {"attic"})), None) \
        or next((h for h in hits if not dialectal(h[1])), None)


def own(forms, lemma, *want):
    """The form that is the lemma itself (its nominative), with its tags."""
    return next(((f, t) for f, t in forms if f == lemma and all(w in t for w in want)), None) or pick(forms, *want)


def bare(s):
    """Letters only, small, without accents and breathings: λόγου → λογου."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if c.isalpha() and not unicodedata.combining(c))


def syllables(word):
    """How many syllables (vowel groups, a diphthong one) a word has, near enough."""
    return len(re.findall(r"(?:αι|ει|οι|υι|αυ|ευ|ηυ|ου|[αεηιουω])", bare(word)))


DIPHTHONGS = {"αι", "ει", "οι", "υι", "αυ", "ευ", "ηυ", "ου"}


def ending(base, form):
    """form as an ending after base, as dictionaries give it: λόγος, λόγου: -ου; ὄνομα,
    ὀνόματος: -ατος; ἐλπίς, ἐλπίδος: -ίδος; ἀγών, ἀγῶνος: -ῶνος; or None where only the
    whole word will do (base a syllable: νύξ, νυκτός; or nothing in common)."""
    b, f = bare(base), bare(form)
    n = 0
    while n < min(len(b), len(f)) and b[n] == f[n]:
        n += 1
    if b == f or n < 2 or syllables(base) < 2:
        return None
    if n >= len(f):  # (the form within base: γλυκύ of γλυκύς: -ύ, from its last vowel)
        n = len(f) - 1
        while n > 0 and f[n] not in VOWELS:
            n -= 1
        while n > 0 and f[n - 1:n + 1] in DIPHTHONGS:
            n -= 1
    if n == len(b) and f[n] in VOWELS:  # (base whole within the form: Ἀπόλλων, -ωνος, from its last vowel)
        while n > 0 and b[n - 1] not in VOWELS:
            n -= 1
        while n > 0 and b[n - 1] in VOWELS and b[n - 1:n + 1] in DIPHTHONGS | {b[n - 1] * 2}:
            n -= 1
        n -= 1
    elif f[n] not in VOWELS:  # (ὀνόμα|τος: from the vowel before, -ατος)
        while n > 0 and f[n - 1] not in VOWELS:
            n -= 1
        n -= 1
    elif f[n - 1:n + 1] in DIPHTHONGS or b[n - 1:n + 1] in DIPHTHONGS:  # (λόγο|υ, βασιλέ|ως: -ου, -εως)
        n -= 1
    # Where base is accented on its last syllable and the form before the ending, from that
    # syllable (ἀγών, ἀγῶνος: -ῶνος).
    letters = []  # the form's letters, each with its marks
    for c in unicodedata.normalize("NFD", form):
        if unicodedata.combining(c) and letters:
            letters[-1] += c
        else:
            letters.append(c)
    accent = next((i for i, x in enumerate(letters) if any(m in x for m in "\u0301\u0300\u0342")), None)
    if accent is not None and accent < n and re.search("[\u0301\u0342][^αεηιουω]*$", unicodedata.normalize("NFD", base.lower())):
        n = accent
        while n > 0 and f[n - 1] in VOWELS and f[n - 1:n + 1] in DIPHTHONGS:
            n -= 1
    if n < 2 or not any(c in VOWELS for c in f[:n]):  # (from the first syllable: πολύς, πολλή)
        return None
    return "-" + unicodedata.normalize("NFC", "".join(letters[n:]).lower())


def attic_alpha(lemma):
    """A feminine in -α's genitive, as Attic has it: -ας after ε, ι, ρ (χώρα, ἀλήθεια), else
    -ης (δόξα, θάλασσα); -ᾶς, -ῆς where the α is accented (στρατιά). (Morpheus has some as
    -η's, the -α Doric: δόξα, δόξας.)"""
    b = bare(lemma)
    last = re.search(r"α([\u0300-\u036f]*)$", unicodedata.normalize("NFD", lemma))  # (the final α's marks)
    accented = bool(last and re.search("[\u0301\u0342]", last[1]))
    vowel = "α" if len(b) > 1 and b[-2] in "ειρ" else "η"
    return "-" + unicodedata.normalize("NFC", vowel + ("\u0342" if accented else "") + "ς")


def irregular(forms):
    """Whether Morpheus has the word as irregular (γυνή, μέγας): given whole, as dictionaries do."""
    return any(t.startswith("irreg") for _, tags in forms for t in tags)


def headword(lemma, kind, forms, genders=None):
    """A lemma's dictionary form (kind: noun or adj), from its forms (generate's); genders:
    the library's count of its words' genders ({"masc": 9, "fem": 1}), to choose by where
    Morpheus gives it more than one. None where its forms don't give it."""
    if kind == "noun":
        nom = next(((f, t) for f, t in forms if f == lemma and "nom" in t), None) or pick(forms, "nom", "sg")  # (τὰ μηρία: plural)
        gen = nom and pick(forms, "gen", "sg" if "sg" in nom[1] else "pl", like=nom)
        if not nom or not gen:
            return None
        sex = [g for g in ("masc", "fem", "neut") if g in nom[1] or g in gen[1]]
        if genders and any(genders.values()):  # (as the library's words have it, where it has: ἡ ὁδός, which
            top = max(genders.values())         # Morpheus has as masculine; both where it has both, ὁ/ἡ)
            sex = [g for g in ("masc", "fem", "neut") if genders.get(g, 0) >= top / 4]
        if not sex:
            return None
        article = "/".join(ARTICLES[g] for g in sex) if "sg" in nom[1] else "/".join({"masc": "οἱ", "fem": "αἱ", "neut": "τὰ"}[g] for g in sex)
        end = not irregular(forms) and ending(lemma, gen[0])
        if gen[0] == lemma:  # (indeclinable: ὁ Ἰσραήλ)
            return f"{article} {lemma}"
        if sex == ["fem"] and "sg" in nom[1] and bare(lemma).endswith("α") and not irregular(forms):
            end = attic_alpha(lemma)
        return f"{article} {lemma}, {end or gen[0]}"
    masc = own(forms, lemma, "masc", "nom", "sg") or own(forms, lemma, "nom", "sg")
    # (a feminine of its own where Morpheus has one besides the masculine's: δῆλος, -η, -ον,
    # which it has with two endings too)
    degree = bool(masc and masc[1] & {"comp", "superl"})
    fem = pick([x for x in forms if not masc or bare(x[0])[-2:] != bare(masc[0])[-2:]],  # (not κρείττων for κρείσσων's)
               "fem", "nom", "sg", like=masc, degree=degree) \
        or pick(forms, "fem", "nom", "sg", like=masc, degree=degree)
    neut = pick(forms, "neut", "nom", "sg", like=masc, degree=degree)
    if not neut:
        return None
    rest = [f[0] for f in (fem, neut) if f and (f is neut or bare(f[0]) != bare((masc or neut)[0]))]  # (ἄδικος, -ον: two endings)
    ends = [ending(lemma, f) for f in rest]
    if irregular(forms) or not all(ends):
        return ", ".join([lemma, *rest])
    return ", ".join([lemma, *ends])


def headwords(lemmas):
    """{lemma: headword} for [(lemma, kind, genders)] (kind: noun, adj, or another, for
    which there's none but a hand-listed word's), those it can make (cached)."""
    for lemma, kind, _ in lemmas:  # (whole, however the library's parsing has them: πᾶς as a pronoun)
        if lemma in IRREGULAR_NOUNS:
            article, gen = IRREGULAR_NOUNS[lemma]
            _cache[lemma, kind] = f"{article} {lemma}, {gen}"
        elif lemma in IRREGULAR_ADJECTIVES:
            _cache[lemma, kind] = IRREGULAR_ADJECTIVES[lemma]
    todo = [x for x in lemmas if (x[0], x[1]) not in _cache and x[1] in ("noun", "adj")]
    if todo and available():
        made = generate([x[0] for x in todo])
        for lemma, kind, genders in todo:
            _cache[lemma, kind] = made.get(lemma) and headword(lemma, kind, made[lemma], genders)
    return {x[0]: _cache.get((x[0], x[1])) for x in lemmas if _cache.get((x[0], x[1]))}


if __name__ == "__main__":
    import sys
    made = generate(sys.argv[1:])
    for w in sys.argv[1:]:
        print(w, "| noun:", made.get(w) and headword(w, "noun", made[w]), "| adj:", made.get(w) and headword(w, "adj", made[w]))
