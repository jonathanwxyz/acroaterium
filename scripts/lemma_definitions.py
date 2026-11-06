#!/usr/bin/env python3
"""Each lemma's definition, as the search, the references and the flashcards give it, and the
notes that say how Homer and the Bible use it where that differs:

    δόξα     opinion, expectation; reputation, honour, glory     (data/lemma_definitions.tsv)
    αἱρέω    to take, (mid.) to choose
             Homer: to take, take hold of, grasp; to kill, slay   (Cunliffe)
    εὐλογέω  to speak well of, praise, honour
             Bible: to praise, bless                              (STEPBible)

The definition (lemmas.definition) is, first, ours where data/lemma_definitions.tsv has one
(the commonest lemmas whose short definition misleads); else the short definitions' (LSJ's,
as library.py matched them), where that is a homonym that shares nothing with how our texts
use the word (κύριος: "having power"), the numbered homonym that does ("a lord, master");
else as library.py made it; a Homeric word's (one LSJ cites from Homer and the epic poets after him only:
μεταυδάω, ἐπορούω, δουρικλειτός) is Homer's, alone, and labelled Homeric. The notes (lemma_notes) are a Homer one, from Cunliffe's
Lexicon of the Homeric Dialect (else Autenrieth's Homeric Dictionary), and a Bible one, from
Dodson's New Testament lexicon (else STEPBible's brief lexicon, TBESG), each where the lemma's words are read in that part of the
library (Homer: the texts cited by line; the Bible: the Septuagint, the New Testament, the
Fathers and the services, all but Plato; three words at least) and the
note adds a word the card hasn't yet; the Biblical first, then the Homeric (the definition
itself being the Classical: the word card's order, Classical, Biblical, Homeric). Names have none.

The sources are fetched once into data/raw/definitions/:
  STEPBible TBESG (Tyndale House, CC BY 4.0), github.com/STEPBible/STEPBible-Data
  Cunliffe 1924 (public domain; the Internet Archive's text, as cleaned at github.com/jtauber/cunliffe)
  Autenrieth 1876 (public domain; Perseus's TEI, CC BY-SA)

library.py runs this after making the lemma index; it can be run alone too, on the library
as it is:  python3 scripts/lemma_definitions.py [--show λόγος δόξα …]
"""
import argparse
import html
import json
import re
import sqlite3
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "data" / "library.sqlite"
RAW = ROOT / "data" / "raw" / "definitions"
OVERRIDES = ROOT / "data" / "lemma_definitions.tsv"
SOURCES = {
    "tbesg.txt": "https://raw.githubusercontent.com/STEPBible/STEPBible-Data/master/Lexicons/"
                 "TBESG%20-%20Translators%20Brief%20lexicon%20of%20Extended%20Strongs%20for%20Greek%20-%20STEPBible.org%20CC%20BY.txt",
    "cunliffe.txt": "https://raw.githubusercontent.com/jtauber/cunliffe/master/cunliffe.txt",
    "autenrieth.xml": "https://www.perseus.tufts.edu/hopper/dltext?doc=Perseus%3Atext%3A1999.04.0073",
}
GREEK = "Ͱ-Ͽἀ-῿"
NOTE_MIN = 3  # (words in that part of the library, at least, for its note)
# A sense that describes the word's grammar, not its meaning ("interrog.", "negative particle")
GRAMMAR = re.compile(r"\b(particle|interrog|prep\.|genit|intrans|trans\.|adv\.|conj\.|pron\.|see|cf)\b|[（）]", re.I)
FUNCTION_WORD = re.compile(r"\b(particle|conjunction|preposition|pronoun|article)\b", re.I)
WORD = re.compile(r"[a-z]{3,}")
STOP = set("the with from into its his her their being thing things".split())


def nfc(s):
    return unicodedata.normalize("NFC", s)


def plain(s):
    """Without Cunliffe's and Wiktionary's marks of length (ῡ, ᾰ)."""
    return nfc("".join(c for c in unicodedata.normalize("NFD", s) if c not in "̄̆"))


def content(s):
    """A gloss's content words, as stems, to tell whether another says anything new."""
    return {w[:5] for w in WORD.findall((s or "").lower()) if w not in STOP}


def fetch(name):
    path = RAW / name
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        print(f"fetching {name} …", file=sys.stderr)
        req = urllib.request.Request(SOURCES[name], headers={"User-Agent": "acroaterium (library build)"})
        with urllib.request.urlopen(req, timeout=300) as r:
            path.write_bytes(r.read())
    return path.read_text(encoding="utf-8")


def variants(lemma):
    """Other spellings to look a lemma up by in a lexicon: without an iota subscript
    (ἀποθνῄσκω) or diaeresis, Koine γιν- for γιγν-, -σσ- for -ττ-, Homer's -η (ἡμέρη), …"""
    d = unicodedata.normalize("NFD", lemma)
    out = [lemma, nfc(d.replace("ͅ", "")), nfc(d.replace("̈", ""))]
    for a, b in (("γιγν", "γιν"), ("ττ", "σσ"), ("ρρ", "ρσ"), ("ναῦς", "νηῦς"), ("παῖς", "πάϊς"), ("ἴσος", "ἶσος")):
        out += [lemma.replace(a, b), lemma.replace(b, a)]  # (one at a time)
    out.append(re.sub(r"α(ς?)$", r"η\1", lemma))
    return list(dict.fromkeys(out))


def lookup(table, lemma):
    return next((table[v] for v in variants(lemma) if v in table), None)


def fill(senses, limit=50, most=3):
    """A note from a lexicon's senses: each sense's first item, then its second, as fit
    (about limit letters, most items); an item's commas as fit."""
    items = []
    for s in senses:
        its = []
        for x in s.split(";"):
            x = x.strip()
            if len(x) > 32:
                parts = x.split(",")
                while len(parts) > 1 and len(",".join(parts)) > 32:
                    parts.pop()
                x = ",".join(parts).strip()
            if x and len(x) <= 40 and meaningful(x):  # (not a fragment the cut left: "thus", "to")
                its.append(x)
        items.append(its)
    chosen, seen = [], set()
    for depth in range(2):
        for its in items:
            if depth < len(its) and len(chosen) < most and its[depth].lower() not in seen:
                if chosen and len("; ".join(chosen + [its[depth]])) > limit:
                    continue
                chosen.append(its[depth])
                seen.add(its[depth].lower())
    return "; ".join(chosen) or None


# ---------------------------------------------------------------- the lexicons

def stepbible():
    """{lemma: brief gloss} from STEPBible's TBESG ("lord: God", "to praise, bless")."""
    out = {}
    for line in fetch("tbesg.txt").splitlines():
        r = line.split("\t")
        if re.match(r"^G\d", r[0]) and len(r) > 6 and r[6].strip():
            gloss = r[6].strip().replace("/", ", ").replace("?!", "?")
            if gloss[:1].isupper() and not gloss[1:2].isupper():
                gloss = gloss[0].lower() + gloss[1:]  # ("Three"; a name's own capital stays: its lemma's is upper)
            out.setdefault(nfc(r[3]), gloss)
    return out


def cunliffe():
    """{headword: [entry text]}: an entry from its headword at the left margin to the row of asterisks."""
    out, cur = {}, None
    for line in fetch("cunliffe.txt").splitlines():
        if line.startswith("****"):
            cur = None
            continue
        m = re.match(rf"^[†*]*([{GREEK}̀-ͯ]+)", line)
        if m and cur is None:
            cur = plain(m[1])
            out.setdefault(cur, []).append([])
        if cur:
            out[cur][-1].append(line.strip())
    return {k: [" ".join(e) for e in v] for k, v in out.items()}


def meaningful(sense):
    """Whether a sense says what the word means: not a cross-reference ("= prec.", "= next"), an
    abbreviation ("app.", "pple."), or a stray word ("thus", "to")."""
    sense = sense.strip(" ,;.")
    words = re.findall(r"[A-Za-z]{2,}", sense)
    return bool(words) and not re.match(r"^(=|app\b|pple\b|prob\b|perh\b|see\b|cf\b|sc\b|i\.e\b|v\.\s)", sense) \
        and not (len(words) == 1 and words[0].lower() in {"thus", "so", "as", "to", "on", "in", "of", "by", "with", "for", "at",
                                                          "the", "subj", "opt", "imp", "inf", "ind", "gen", "dat", "acc", "nom",
                                                          "voc", "pf", "aor", "fut", "mid", "pass", "app", "pple"}) \
        and any(len(w) >= 3 for w in words)


def cunliffe_senses(entry):
    """The English of each numbered sense, before its references and quotations."""
    body = re.sub(r"\[[^\]]*\]", " ", entry)  # (the etymology first: it may have stops of its own)
    body = re.sub(rf"^[†*]*[{GREEK}̀-ͯ]+[^.]*?\.\s*", "", body, count=1)  # (headword, endings, gender)
    out = []
    for p in re.split(r"\s(?:[IVX]+|\d{1,2}|[a-h]|\([a-hα-ω]\))\.\s", " " + body):
        p = re.split(rf"\s[{GREEK}]\d{{1,3}}\b|:|\(cf\.|\bCf\.|--|\bSee\b", p)[0]
        p = re.sub(r"\([^)]*\)", "", p)
        p = re.sub(r"(?<![A-Za-z])['‘’]|['‘’](?![A-Za-z])", "", p)  # (quotation marks, not one's)
        p = re.sub(r"\s+", " ", p).strip(" ,;.")
        p = re.sub(r"^In (?:pl|dual|sing|mid|pass)\.,?\s*", "", p)  # ("In pl., small cattle": its sense)
        p = re.sub(r"^With (?:neg|genit|dat|acc|infin|pple|inf|adv|prep)\.(?:\s+(?:and|or)\s+\w+\.)?,?\s*", "", p)  # ("With neg., not to care for")
        p = re.sub(r"^Absol\.,?\s*", "", p)
        p = re.sub(r"\s+in gen\b\.?", "", p)
        p = re.sub(r"\.\s+Epithet of ([^.]+)$", r" (epithet of \1)", p)  # ("slayer of men (epithet of Enyalius)")
        p = re.split(r"\.\s+(?=[A-Z])", p)[0]  # (not a sense's later note, "thoughtlessness. In pl"; but "i.e. as")
        p = re.sub(r"^(?:The|A|An)\s+", "", p)
        heading = re.match(r"^(?:As |Of |In |Referring|Hence|With |Said |Used |Applied |Vaguely|without|Specifically$)", p) \
            or p.endswith(("hence", "Hence"))
        if p and not heading and re.search(r"[a-z]{3}", p) and not re.search(rf"[{GREEK}]", p) and len(p) < 90:
            out.append(p[0].lower() + p[1:] if not p[:2].isupper() else p)
    out = [x for x in out if meaningful(x)]
    if not out:  # (no numbered senses: its English sentences, after the forms, "To break in pieces, shiver:")
        for sentence in re.split(r"(?<=[.:;])\s+|--", body):
            sentence = re.split(rf"\s[{GREEK}]\d{{1,3}}\b|:|\(cf\.|\bCf\.", sentence)[0]
            sentence = re.sub(r"\([^)]*\)", "", sentence).strip(" ,;.")
            if re.match(r"^(To|A|An|The)\s+[a-z]", sentence) and not re.search(rf"[{GREEK}]", sentence) and len(sentence) < 90:
                sentence = re.sub(r"^(?:The|A|An)\s+", "", sentence)
                out.append(sentence[0].lower() + sentence[1:])
    return [x for x in out if meaningful(x)]


def autenrieth():
    """{headword: entry text}: Perseus's TEI, headwords in Beta Code."""
    from import_commentaries import beta_to_unicode
    out = {}
    for m in re.finditer(r'<entryFree[^>]*key="([^"]+)"[^>]*>(.*?)</entryFree>', fetch("autenrieth.xml"), re.S):
        key = plain(beta_to_unicode(re.sub(r"\d+$", "", m[1])))
        out.setdefault(key, re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m[2]))).strip())
    return out


def autenrieth_senses(entry):
    """The English of Autenrieth's entry, before its first reference ("Il. 4.472", "†"): read a
    comma's chunk at a time, those with its Greek (in Beta Code: "sun - a/gnu_mi", "aor. -e/aca")
    or only grammar ("only ipf.") or doubt left out; "epith. of X" as "epithet of X"."""
    t = entry.split("):", 1)[1] if "):" in entry[:90] else entry
    t = re.sub(r"\bepith\.\s+of\b", "epithet of", t)
    t = re.split(r"[,;]?\s*\b(?:Il|Od|Hes|h\.\s*\w+)\.\s*\d|†|\bcf\.|\bv\.\s", t)[0]  # (to its first reference)
    t = re.sub(r"\([^)]*\)|[‘’“”]", " ", t)
    kept = []
    for chunk in re.split(r"[,;:]", t):
        chunk = re.sub(r"\s+", " ", chunk).strip(" .")
        if not chunk or re.search(r"[)(/\\=|+_*]|\d|^-|\s-|-\s", chunk) or not re.search(r"[a-z]{3}", chunk):
            continue  # (its Greek, a number, a stem's hyphen)
        if re.match(r"^(only|freq|ipf|aor|perf|pres|fut|mid|pass|act|adv|subst|pl|sing|dual|ep|poet|also|w\.|abs|"
                    r"doubtful word|prob|perh|i\.e|fig|lit|sc|esp|usu|cf)\b\.?", chunk):
            continue  # (grammar, doubt)
        if len(chunk.split()) == 1 and len(chunk) < 4:
            continue  # (a stray syllable)
        kept.append(chunk)
    t = re.sub(r"\s+(from|of|by|in|with|to|and|or)$", "", ", ".join(k for k in kept if meaningful(k)))
    return [t] if t and meaningful(t) and not re.fullmatch(r"(epith|epithet)", t) else []


# ---------------------------------------------------------------- epic words (LSJ)

LSJ_FILES = [f"grc.lsj.perseus-eng{i}.xml" for i in range(1, 28)]
LSJ_URL = "https://raw.githubusercontent.com/PerseusDL/lexica/master/CTS_XML_TEI/perseus/pdllex/grc/lsj/{}"
# Homer (0012) and the epic poets after him, by TLG number as LSJ's citations give it: the
# Hymns, Hesiod, Apollonius, Quintus, Nonnus, Oppian, Aratus, the Orphica, Tryphiodorus,
# Musaeus, the Batrachomyomachia, Callimachus, Theocritus, Moschus, Bion, the epic fragments.
EPIC_TLG = {"0012", "0013", "0020", "0001", "2046", "2045", "0023", "0024", "0653", "0579", "0647", "0576",
            "1220", "0533", "0005", "0035", "0036", "1444", "1445"}
# The same, cited without a number (h.Cer., Hes.Th.); and those who cite Homer's words as his:
# the scholia, Eustathius, the grammarians and lexica (Sch. Od. 17.407 is a scholion on that line).
EPIC_AUTHOR = re.compile(r"^(h\.|Hom|Il\.|Od\.|Hes\.|A\.R\.|Q\.S\.|Nonn|Opp|Arat|Orph|Tryph|Musae|Colluth|Batr|Call\.|Theoc|"
                         r"Mosch|Bion|Epic|Cypr|Id\.|ib\.)")
COMMENTATOR = re.compile(r"\b(Sch|Eust|Hsch|EM|Suid|Phot|Gramm|Et\.Gud|Et\.Magn|Apollon|Hdn|A\.D|Aristarch|Zenod|Ammon|Poll|Gal)\.?"
                         r"(\s*(ad|in)?\s*)?$")


def lsj_epic():
    """The lemmas LSJ cites from Homer and the epic poets after him only (the scholia and
    grammarians aside, who cite Homer's words as his), every entry of the headword so; not one
    whose entry says "etc." or "freq.", which may hide others. {lemma: "homer" (Homer alone) or
    "epic"}. The files are fetched once into data/raw/definitions/lsj/ (Perseus, CC BY-SA)."""
    from import_commentaries import beta_to_unicode
    verdicts = {}
    for name in LSJ_FILES:
        path = RAW / "lsj" / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            print(f"fetching LSJ {name} …", file=sys.stderr)
            with urllib.request.urlopen(LSJ_URL.format(name), timeout=300) as r:
                path.write_bytes(r.read())
        for key, body in re.findall(r'<entryFree[^>]*key="([^"]+)"[^>]*>(.*?)</entryFree>', path.read_text(encoding="utf-8"), re.S):
            lemma = plain(beta_to_unicode(re.sub(r"\d+$", "", key)))
            kinds = set()
            for m in re.finditer(r"<bibl\b([^>]*)>(.*?)</bibl>", body, re.S):
                before = re.sub(r"<[^>]+>", "", body[max(0, m.start() - 60):m.start()])[-16:]
                tlg = re.search(r"urn:cts:greekLit:tlg(\d{4})", m[1])
                author = re.search(r"<author[^>]*>([^<]+)", m[2])
                if COMMENTATOR.search(before) or (author and COMMENTATOR.match(author[1].strip() + ".")):
                    continue
                if tlg:
                    kinds.add("homer" if tlg[1] == "0012" else "epic" if tlg[1] in EPIC_TLG else "other")
                elif author and EPIC_AUTHOR.match(author[1].strip()):
                    kinds.add("homer" if re.match(r"^(Hom|Il\.|Od\.)", author[1].strip()) else "epic")
                elif author or m[2].strip():
                    kinds.add("other")
            text = re.sub(r"<[^>]+>", " ", body)
            verdict = None if "other" in kinds or "homer" not in kinds or re.search(r"\b(etc|freq)\.", text) \
                else "homer" if kinds == {"homer"} else "epic"
            verdicts.setdefault(lemma, []).append(verdict)
    return {k: ("homer" if all(v == "homer" for v in vs) else "epic") for k, vs in verdicts.items() if all(vs)}


def overrides():
    """{lemma: (definition, front)} from data/lemma_definitions.tsv (front: "" as made, "-" the lemma alone)."""
    out = {}
    if OVERRIDES.exists():
        for line in OVERRIDES.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            lemma, definition, *front = line.split("\t") + [""]
            if lemma != "lemma":
                out[nfc(lemma.strip())] = (definition.strip(), front[0].strip())
    return out


# ---------------------------------------------------------------- the library's

def usage_glosses(con):
    """{lemma: the glosses our texts give its words} (the New Testament's lexicon, the Septuagint's)."""
    out = {}
    for lemma, gloss in con.execute("SELECT coalesce(l.lemma, w.lemma), coalesce(l.gloss, w.gloss) FROM words w "
                                    "LEFT JOIN lexicon l ON l.strongs = w.strongs WHERE coalesce(l.gloss, w.gloss) IS NOT NULL"):
        if lemma:
            out.setdefault(lemma, set()).add(gloss)
    return {k: " ".join(v) for k, v in out.items()}


def parts_of_speech(con):
    """{lemma: its usual part of speech, a letter as the treebanks' (n, v, a, d, r, c, p, g, l, …)}."""
    from collections import Counter
    pos = {}
    upos = {"NOUN": "n", "VERB": "v", "ADJ": "a", "ADV": "d", "ADP": "r", "CCONJ": "c", "SCONJ": "c", "PRON": "p",
            "DET": "l", "PART": "g", "INTJ": "i", "NUM": "m"}
    for lemma, p, n in con.execute("SELECT lemma, substr(postag, 1, 1), count(*) FROM treebank WHERE lemma IS NOT NULL "
                                   "AND postag IS NOT NULL GROUP BY 1, 2"):
        pos.setdefault(re.sub(r"\d+$", "", lemma), Counter())[p] += n
    for lemma, p, n in con.execute("SELECT lemma, pos, count(*) FROM auto_words WHERE lemma IS NOT NULL GROUP BY 1, 2"):
        if p in upos:
            pos.setdefault(lemma, Counter())[upos[p]] += n
    return {k: c.most_common(1)[0][0] for k, c in pos.items()}


def text_periods(con):
    """{text: its period, for the reader's word card}: "homer" (cited by line), "classical" (Plato:
    by Stephanus sections, 172a), "bible" (by verse: the Septuagint, the New Testament), else
    "church" (the Fathers, the services)."""
    stephanus = {slug for (slug,) in con.execute(
        "SELECT DISTINCT text FROM segments WHERE text IN (SELECT slug FROM texts WHERE cite = 'section') "
        "AND ref GLOB '[0-9]*[a-e]'")}
    return {slug: "homer" if cite == "line" else "classical" if slug in stephanus else "bible" if cite == "verse" else "church"
            for slug, cite in con.execute("SELECT slug, cite FROM texts")}


def where_read(con, periods):
    """{lemma id: (words in Homer, in the Bible's Greek and the Church's after it: the Septuagint,
    the New Testament, the Fathers, the services)}."""
    out = {}
    for lid, slug, n in con.execute("SELECT lemma, text, count(*) FROM lemma_words GROUP BY lemma, text"):
        h, b = out.get(lid, (0, 0))
        out[lid] = (h + n, b) if periods.get(slug) == "homer" else (h, b + n) if periods.get(slug) in ("bible", "church") else (h, b)
    return out


def dodson(con):
    """{lemma: Dodson's brief gloss} (the New Testament's lexicon), of a lemma's Strong's numbers the
    one our texts use most."""
    best = {}
    for lemma, gloss, n in con.execute("SELECT l.lemma, l.gloss, count(w.strongs) FROM lexicon l LEFT JOIN words w "
                                       "ON w.strongs = l.strongs WHERE l.gloss IS NOT NULL GROUP BY l.strongs"):
        lemma = nfc(lemma)
        if lemma not in best or n > best[lemma][1]:
            best[lemma] = (re.sub(r"^I ", "to ", gloss.strip().rstrip(".")), n)  # ("I bless": "to bless", as the others)
    return {k: v[0] for k, v in best.items()}


def apply(con, show=(), fresh=False, verbose=False):
    """Rewrite lemmas.definition and lemma_notes (see above)."""
    from import_shortdefs import load_list, matcher
    defs = load_list()
    match = matcher(defs)
    step, cun, aut, ours = stepbible(), cunliffe(), autenrieth(), overrides()
    periods = text_periods(con)
    usage, counts, pos, nt = usage_glosses(con), where_read(con, periods), parts_of_speech(con), dodson(con)
    epic = lsj_epic()
    # The definitions as library.py made them, kept: run again, this starts from them, not from its
    # own (an override taken out gives the list's back). Made again with the lemma index (fresh).
    count = con.execute("SELECT count(*) FROM lemmas").fetchone()[0]
    has = con.execute("SELECT name FROM sqlite_master WHERE name = 'lemma_base_definitions'").fetchone()
    if fresh or not has or con.execute("SELECT count(*) FROM lemma_base_definitions").fetchone()[0] != count:
        con.executescript("""
            DROP TABLE IF EXISTS lemma_base_definitions;
            CREATE TABLE lemma_base_definitions (lemma INTEGER PRIMARY KEY, definition TEXT);
            INSERT INTO lemma_base_definitions SELECT id, definition FROM lemmas;""")
    con.executescript("""
        DROP TABLE IF EXISTS lemma_notes;
        CREATE TABLE lemma_notes (       -- how Homer and the Bible use a lemma (scripts/lemma_definitions.py)
            lemma   INTEGER PRIMARY KEY REFERENCES lemmas,
            notes   TEXT,                -- the flashcards': JSON [[label, text], …] ("Biblical", "Homeric"), where they
                                         --   add to the definition
            front   TEXT,                -- the flashcards' front, where ours: "-" the lemma alone (μήν: two words)
            homer   TEXT,                -- Homer's sense (Cunliffe, else Autenrieth), for the word card
            bible   TEXT,                -- the Bible's (Dodson, else STEPBible), likewise
            homer_n INTEGER,             -- its words in Homer; in the Bible, the Fathers and the services
            bible_n INTEGER,
            ours    INTEGER,             -- 1: lemmas.definition is ours (data/lemma_definitions.tsv)
            epic    INTEGER);            -- 1: a Homeric word (LSJ cites it from Homer and the epic poets only):
                                         --   its definition Homer's, labelled so
        DROP TABLE IF EXISTS text_periods;
        CREATE TABLE text_periods (      -- a text's period, for the word card's order: homer, classical, bible, church
            text   TEXT PRIMARY KEY,
            period TEXT NOT NULL);
    """)
    con.executemany("INSERT INTO text_periods VALUES (?, ?)", periods.items())
    changed, repaired, rows = 0, 0, []
    for lid, lemma, definition, now in con.execute("SELECT l.id, l.lemma, b.definition, l.definition FROM lemmas l "
                                                   "JOIN lemma_base_definitions b ON b.lemma = l.id").fetchall():
        homer = lookup(cun, lemma)
        homer_senses = [s for e in (homer or []) for s in cunliffe_senses(e)]
        if not homer_senses:
            a = lookup(aut, lemma)
            homer_senses = autenrieth_senses(a) if a else []
        # (an entry that calls the word a particle, conjunction, preposition, … before its senses
        # describes a function word's uses, not a meaning: none, rather than a later sense's "not
        # yet, never yet" for οὐ, "Negative particle")
        function_entry = any(FUNCTION_WORD.search(re.split(r"\s1\.\s", e, maxsplit=1)[0]) for e in (homer or []))
        homer_note = None if function_entry or homer_senses and GRAMMAR.search(homer_senses[0]) else \
            fill([s for s in homer_senses if not GRAMMAR.search(s)])
        bible_note = lookup(nt, lemma) or lookup(step, lemma)  # (Dodson: fuller, for the New Testament's words)
        used = content(" ".join(x for x in (usage.get(lemma), bible_note, homer_note) if x))
        main, front = definition, None
        if lemma in ours:
            main, front = ours[lemma][0] or definition, ours[lemma][1] or None
        elif not lemma[:1].isupper() and (m := match(lemma)) and m[1] and definition == m[0] and used \
                and not content(definition) & used:  # (not a name: Ἄργος isn't Argus)
            for i in range(2, 6):  # (a numbered homonym that does: κύριος2)
                alt = defs.get(f"{m[1]}{i}")
                if alt and content(alt) & used:
                    main, repaired = alt, repaired + 1
                    if verbose:
                        print(f"  repaired {lemma}: {definition!r} -> {alt!r}")
                    break
        h, b = counts.get(lid, (0, 0))
        # A Homeric word (LSJ's citations Homer's and the epic poets' only; not one our library
        # has outside them, nor a name, nor ours): its definition Homer's, alone, with room for
        # more of it; no other period's.
        is_epic = bool(lookup(epic, lemma) and not b and not lemma[:1].isupper() and lemma not in ours and homer_senses)
        if is_epic:
            main = fill([s for s in homer_senses if not GRAMMAR.search(s)], limit=60, most=4) or main
        if main != now:
            con.execute("UPDATE lemmas SET definition = ? WHERE id = ?", (main, lid))
        changed += main != definition
        notes = []
        function = pos.get(lemma) and pos[lemma] in "rcgpl"
        if not lemma[:1].isupper() and not is_epic:  # (names: none; a Homeric word: its definition is Homer's)
            seen = content(main)
            # (no Homer note for a preposition, particle, conjunction, pronoun or the article: Cunliffe
            # describes their uses, "into something, and", rather than giving a meaning)
            for label, n, text in (("Biblical", b or 0, bible_note), ("Homeric", 0 if function else h or 0, homer_note)):  # (always so)
                if text and n >= NOTE_MIN and text != main and content(text) - seen:
                    notes.append([label, text])
                    seen |= content(text)
        homer_card = None if function or lemma[:1].isupper() else main if is_epic else homer_note
        if notes or front or homer_card or bible_note or lemma in ours:
            rows.append((lid, json.dumps(notes, ensure_ascii=False) if notes else None, front, homer_card,
                         None if lemma[:1].isupper() else bible_note, h or 0, b or 0, int(lemma in ours), int(is_epic)))
        if lemma in show:
            print(f"{lemma}: {main}" + "".join(f"\n    {k}: {v}" for k, v in notes) + (f"\n    (front: {front})" if front else ""))
    con.executemany("INSERT INTO lemma_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    print(f"definitions: {changed} changed ({len(ours)} ours, {repaired} homonyms repaired, "
          f"{sum(r[8] for r in rows)} Homeric words Homer's); notes for {sum(1 for r in rows if r[1])} lemmas")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--show", nargs="*", default=(), help="lemmas to print as made")
    ap.add_argument("-v", action="store_true", help="list the homonyms repaired")
    args = ap.parse_args()
    con = sqlite3.connect(LIBRARY)
    apply(con, {nfc(x) for x in args.show}, verbose=args.v)
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
