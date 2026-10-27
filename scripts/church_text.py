#!/usr/bin/env python3
"""The Old Testament as the recordings read it: the Septuagint as Greek Wikisource gives it.

The recordings of the Old Testament are read from a Church edition of the Septuagint whose
wording differs from Rahlfs's in many places ("τοῖς ὀφθαλμοῖς αὐτοῦ εἶδε" where Rahlfs has
"εἶδεν"); the text of Greek Wikisource's Old Testament (el.wikisource.org, "Η Αγία Γραφή")
is that wording. This takes it into the library's verses (Rahlfs's chapters and verse
numbers): each chapter lined up by its words (chapters numbered otherwise, Susanna, Bel,
Sirach's prologue, matched by content; a verse that ends one of theirs and opens the next of
ours, Genesis 31:55 as Rahlfs's 32:1, moved to it), and where a verse begins otherwise in its
division, the words to a sentence's end go with the verse before. Rahlfs's wording is kept only where
the recording's audio clearly says the reader follows it (a misprint, a place the reading
departs): by the alignment model's likelihood of each over the verse. The books' wikitext
is fetched once, into data/raw/wikisource/; the verses go to library/church_text.json, which
scripts/library.py applies (realign after). A name left without accents (Rahlfs's Αδαμ, where
the reader follows Rahlfs, or a capital) is accented as Wikisource accents it elsewhere, or spelled as Wikisource spells it in that chapter
where that's a little otherwise (Ιωαχας: Ἰωάχαζ).

    .venv/bin/python scripts/church_text.py [--jobs N] [--fetch-only] [--library DB] [slug ...]
"""
import argparse
import difflib
import functools
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import align_library as A  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "raw" / "wikisource"
OUT = ROOT / "library" / "church_text.json"
API = "https://el.wikisource.org/w/api.php?action=parse&prop=wikitext&format=json&redirects=1&page={page}"
AGENT = {"User-Agent": "acroaterium/1.0 (personal study tool)"}
REVERT = 3.0  # log-likelihood going back to Rahlfs's wording must gain over a verse's audio
# The library's books as Wikisource names their pages (the Psalms: all on one).
BOOKS = {
    "genesis": "Γένεσις", "exodus": "Έξοδος", "leviticus": "Λευϊτικόν", "numbers": "Αριθμοί",
    "deuteronomy": "Δευτερονόμιον", "joshua": "Ιησούς του Ναυή", "judges": "Κριταί", "ruth": "Ρουθ",
    "1kingdoms": "Βασιλειών Α'", "2kingdoms": "Βασιλειών Β'", "3kingdoms": "Βασιλειών Γ'", "4kingdoms": "Βασιλειών Δ'",
    "1chronicles": "Παραλειπομένων Α'", "2chronicles": "Παραλειπομένων Β'", "1esdras": "Έσδρας Α'",
    "ezra": "Έσδρας Β'", "nehemiah": "Νεεμίας", "tobit": "Τωβίτ", "judith": "Ιουδίθ", "esther": "Εσθήρ",
    "1maccabees": "Μακκαβαίων Α'", "2maccabees": "Μακκαβαίων Β'", "3maccabees": "Μακκαβαίων Γ'",
    "4maccabees": "Μακκαβαίων Δ'", "psalms": "Ψαλμοί του Δαυίδ/Α-ΡΝΑ", "job": "Ιώβ", "proverbs": "Παροιμίαι",
    "ecclesiastes": "Εκκλησιαστής", "songofsongs": "Άσμα Ασμάτων", "wisdom": "Σοφία Σολομώντος",
    "sirach": "Σοφία Σειράχ", "hosea": "Ωσηέ", "amos": "Αμώς", "micah": "Μιχαίας", "joel": "Ιωήλ",
    "obadiah": "Οβδίας", "jonah": "Ιωνάς", "nahum": "Ναούμ", "habakkuk": "Αμβακούμ", "zephaniah": "Σοφονίας",
    "haggai": "Αγγαίος", "zechariah": "Ζαχαρίας", "malachi": "Μαλαχίας", "isaiah": "Ησαΐας",
    "jeremiah": "Ιερεμίας", "baruch": "Βαρούχ", "lamentations": "Θρήνοι Ιερεμίου",
    "letterofjeremiah": "Επιστολή Ιερεμίου", "ezekiel": "Ιεζεκιήλ", "daniel": "Δανιήλ (Θεοδοτίων)",
}

# Names accented by hand where Wikisource has none like them to go by: as it spells them in
# that verse (Σαδουχ: Σαδὼκ), or, where it hasn't the name, Rahlfs's spelling accented
# (Σελνα: Σελνὰ). {slug: {"div:ref": {as printed: as it should read}}}, each found once.
NAMES = {
    "1chronicles": {"6:42": {"Σελνα": "Σελνὰ"}, "6:44": {"Ατταν": "Ἀττὰν"}},
    "1esdras": {"9:27": {"Ωβαδιος": "Ἰωαβδίος"}, "9:34": {"Καραβασιων": "Ραβασίων"}},
    "1maccabees": {"11:39": {"Ιμαλκουε": "Εἰμαλκουαὶ"}},
    "2kingdoms": {"23:36": {"Ιγααλ": "Γάαλ"}},
    "3kingdoms": {"4:4": {"Σαδουχ": "Σαδὼκ"}, "4:10": {"Ρησφαρα": "Ρησφαρά"}},
    "3maccabees": {"3:8": {"Ελληνες": "Ἕλληνες"}, "5:1": {"Ερμωνα": "Ἕρμωνα"}, "5:18": {"Ερμωνα": "Ἕρμωνα"},
                   "5:37": {"Ερμωνα": "Ἕρμωνα"}},
    "4kingdoms": {"4:8": {"Σουμαν": "Σωμάν"}},
    "ezra": {"2:33": {"Λοδ, Αρωθ": "Λοδαδὶ"}, "2:42": {"Σαβαου": "Σωβαΐ"}, "2:50": {"Μαωνιμ": "Μοουνίμ"},
             "2:54": {"Νασουε": "Νασθιέ"}, "10:36": {"Ουιεχωα, Ιεραμωθ": "Οὐουανία, Μαριμώθ"}},
    "isaiah": {"9:11": {"Ελληνας": "Ἕλληνας"}},
    "jeremiah": {"47:8": {"Ωφε": "Ἰωφὲ", "Μοχατι": "Μωχαθί"}},
    "nehemiah": {"3:10": {"Ατους": "Ἀττοὺθ"}, "7:32": {"Βηθηλ": "Βαιθὴλ"}},
}


def wikitext(page):
    """A Wikisource page's wikitext (fetched once)."""
    path = CACHE / (page.replace("/", "~") + ".json")
    if not path.exists():
        url = API.format(page=urllib.parse.quote(page))
        for attempt in range(6):  # (too many requests: wait as asked, longer each time)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=60) as r:
                    body = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 5:
                    raise
                time.sleep(int(e.headers.get("Retry-After") or 0) or 10 * 2 ** attempt)
        if "error" in body:
            sys.exit(f"wikisource: {page}: {body['error'].get('info')}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body["parse"]["wikitext"]["*"], ensure_ascii=False))
        time.sleep(1.5)  # (gently)
    return json.loads(path.read_text())


def plain(wiki):
    """Wikitext as plain words: verse numbers, templates, links' targets, notes and markup
    left out."""
    t = re.sub(r"<ref[^>]*/>|<ref.*?</ref>|<!--.*?-->", " ", wiki, flags=re.S)
    t = re.sub(r"^'''[^'\n]*'''\s*$", " ", t, flags=re.M)  # (a heading in bold: Ψαλμός 1)
    t = re.sub(r"\{\{κ\|[^}]*\}\}", " ", t)  # (a verse's number)
    t = re.sub(r"\{\{[^{}]*\}\}", " ", t)
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", lambda m: "" if ":" in m.group(0)[:20] else m[1], t)
    t = re.sub(r"</?[a-zA-Z][^<>]*>|'{2,}", " ", t)  # (HTML: < > alone are quotation marks)
    t = re.sub(r"«\s+", "«", re.sub(r"\s+»", "»", t.replace("<", "«").replace(">", "»")))
    t = re.sub(r"\(\s*Ἐκ τοῦ κώδ\.\s*\w+\)", " ", t)  # (a passage's label: from codex A)
    t = re.sub(r"[\[\]†¨]", "", html.unescape(t))  # (stray marks: a passage bracketed, a slip)
    return [w for w in t.split() if not re.fullmatch(r"\d+[α-ω]?", w)]


def chapters_of(slug):
    """{chapter: [words]} of a book as Wikisource gives it: its sections in order (a section
    that isn't a numbered chapter, as Susanna or a prologue, keyed from 1000, to be matched
    by content)."""
    parts = re.split(r"^==+\s*([^=]+?)\s*==+\s*$", wikitext(BOOKS[slug]), flags=re.M)
    if len(parts) == 1:  # (a book of one chapter, without sections)
        return {1: plain(parts[0])}
    out, n, extra = {}, 0, 1000
    for head, body in zip(parts[1::2], parts[2::2]):
        words = plain(body)
        if not words:
            continue
        if head.startswith("Κεφάλαιο"):
            n += 1
            out[n] = words
        else:
            extra += 1
            out[extra] = words
    return out


def norm(w):
    """A word as said, near enough: no accents, breathings, case or punctuation."""
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^\w]", "", "".join(c for c in w if not unicodedata.combining(c))).replace("ς", "σ")


def ctc_ll(lp, labels):
    """log P(labels | lp), the CTC forward pass (lp: frames × vocabulary log-probabilities)."""
    ext = np.array([A.BLANK] + [x for label in labels for x in (label, A.BLANK)])
    alpha = np.full(len(ext), -np.inf)
    alpha[0] = lp[0, ext[0]]
    if len(ext) > 1:
        alpha[1] = lp[0, ext[1]]
    skip = np.zeros(len(ext), bool)
    skip[2:] = (ext[2:] != A.BLANK) & (ext[2:] != ext[:-2])
    none = np.array([-np.inf])
    for t in range(1, len(lp)):
        a1 = np.concatenate((none, alpha[:-1]))
        a2 = np.where(skip, np.concatenate((none, none, alpha[:-2])), -np.inf)
        alpha = np.logaddexp(np.logaddexp(alpha, a1), a2) + lp[t, ext]
    return float(np.logaddexp(alpha[-1], alpha[-2]))


# A breathing (and accent) printed before a capital, as the site has them (᾿Ιακὼβ, ῾Ησαῦ):
# combined with the letter (Ἰακὼβ). (After a letter, ᾿ is an elision mark, left as it is.)
SPACING = {"᾿": "\u0313", "῾": "\u0314", "῎": "\u0313\u0301", "῞": "\u0314\u0301", "῍": "\u0313\u0300",
           "῝": "\u0314\u0300", "῏": "\u0313\u0342", "῟": "\u0314\u0342"}


def tidy(word, ours=None):
    """A word of Wikisource's as the library prints it: in one Unicode form (NFC), its
    punctuation as ours (· for •, — for ~, ᾿ for an elision's apostrophe), breathings joined
    to their capital, and a word in capitals (a chapter's
    first) as ours has it, or in lower case."""
    word = unicodedata.normalize("NFC", word).replace("•", "·").replace("~", "—")
    word = re.sub(r"(?<=\w)['’]", "᾿", word)
    m = re.match(r"^([^\w]*)([᾿῾῎῞῍῝῏῟])(\w)", word)
    if m:
        word = m[1] + unicodedata.normalize("NFC", m[3] + SPACING[m[2]]) + word[m.end():]
    letters = [c for c in word if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        word = ours if ours and norm(ours) == norm(word) else word[0] + word[1:].lower()
    return word


ACUTE, GRAVE = "́", "̀"


@functools.cache
def name_forms():
    """{norm: spelling}: each word of Wikisource's Septuagint as it's accented there most
    often, with acutes for its graves (Ἀδάμ; and Ὅρασις for a slip, Ὃρασις); a word found
    only in lower case capitalised (Ὥσπερ, for a chapter's first word in capitals)."""
    counts = ({}, {})  # (capitalised, lower case)
    for slug in BOOKS:
        for words in chapters_of(slug).values():
            for w in words:
                core = re.sub(r"^[^\w]+|[^\w]+$", "", tidy(w))
                if len(core) > 1 and not core.isupper() and marked(core):
                    core = acute(core)
                    seen = counts[core[0].islower()].setdefault(norm(core), {})
                    seen[core] = seen.get(core, 0) + 1
    lower = {k: max(v, key=v.get) for k, v in counts[1].items()}
    return {k: v[0].upper() + v[1:] for k, v in lower.items()} | {k: max(v, key=v.get) for k, v in counts[0].items()}


def marked(word):
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", word))


def oxytone(word, old, new):
    """word with an accent old on its last syllable as new (a grave before the next word,
    an acute before a stop)."""
    d = unicodedata.normalize("NFD", word)
    at = d.rfind(old)
    if at < 0 or re.search(r"[αεηιουω]", d[at + 1:].lower()):
        return word
    return unicodedata.normalize("NFC", d[:at] + new + d[at + 1:])


def acute(word):
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", word).replace(GRAVE, ACUTE))


def spelled_like(word, chapter):
    """The name in their chapter (its words) that word (Rahlfs's, without accents) is,
    spelled a little otherwise (Ιωαχας: Ἰωάχαζ; Φαλλετια: Φαλεττία): the likest of its
    capitalised words, alike in four fifths of its letters, beginning with the same letter
    and no more than two longer or shorter. None if there's none."""
    key, best, score = norm(word), None, 0.8
    for w in chapter:
        core = re.sub(r"^[^\w]+|[^\w]+$", "", tidy(w))
        if len(core) > 1 and core[0].isupper() and not core.isupper() and marked(core):
            other = norm(core)
            if other[0] == key[0] and abs(len(other) - len(key)) <= 2:
                ratio = difflib.SequenceMatcher(None, key, other, autojunk=False).ratio()
                if ratio >= score:
                    best, score = acute(core), ratio
    return best


def accent_names(words, chapter=()):
    """A verse's words, each capitalised one printed without accent or breathing (Rahlfs's
    Αδαμ, Ελιφας, kept where the reader follows Rahlfs, or a capital Wikisource didn't
    accent) as Wikisource accents that name elsewhere (Ἀδάμ, Ἐλιφάς), or, where it has no
    name of those letters, as it spells the name in this chapter (chapter: their words), if
    a little otherwise (Ιωαχας: Ἰωάχαζ): grave on its last syllable before the next word,
    as in a sentence."""
    forms, out = name_forms(), []
    for k, w in enumerate(words):
        m = re.fullmatch(r"([^\w]*)(\w+)([^\w]*)", w)
        if m and len(m[2]) > 1 and m[2][0].isupper() and not m[2].isupper() and not marked(m[2]):
            core = forms.get(norm(m[2])) or spelled_like(m[2], chapter)
            if core:
                if not m[3] and k < len(words) - 1:
                    core = oxytone(core, ACUTE, GRAVE)
                w = m[1] + core + m[3]
        out.append(w)
    return out


def church_verse(theirs, ours, lp):
    """The Church's wording of a verse (theirs), but Rahlfs's (ours) at each place the audio
    (lp) clearly says the reader follows it (a misprint, a place the reading departs): (the
    words, [(theirs, ours, gain of going back to ours), ...])."""
    ops = difflib.SequenceMatcher(None, [norm(w) for w in theirs], [norm(w) for w in ours],
                                  autojunk=False).get_opcodes()
    back = [False] * len(ops)

    def words(b):
        return [w for (tag, i1, i2, j1, j2), revert in zip(ops, b)
                for w in (ours[j1:j2] if revert and tag != "equal" else theirs[i1:i2])]

    def score(b):
        return ctc_ll(lp, A.tokens_of(" ".join(words(b))))

    base, places = score(back), []
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            continue
        trial = back[:k] + [True] + back[k + 1:]
        s = score(trial)
        places.append((" ".join(theirs[i1:i2]), " ".join(ours[j1:j2]), round(s - base, 1)))
        if s > base + REVERT:
            back, base = trial, s
    return words(back), places


def similarity(a, b):
    """How much of a (our chapter's words) is found, in order, in b (theirs): their verses
    that ours lacks don't count against it."""
    if not a or not b:
        return 0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(n for _, _, n in sm.get_matching_blocks()) / len(a)


SENTENCE_END = re.compile(r"[.;·;:]$")


def spread(rows, words):
    """Their chapter's words (words) among our verses (rows: (seq, ref, content)), lined up by
    the words: {verse index: [words]}. Words of theirs standing for words of ours go with
    those words' verses, each with the one it's most like ("συμπαραληφθῇς. εἶπε" for
    "συμπαραλημφθῇς. | εἶπεν": the first with the verse before, the second with the next).
    Words of theirs ours lacks at a verse's start, up to a sentence's end, go with the verse
    before ("ἀπέθανε. | Καὶ ἔζησε")."""
    ours_all = [(k, w) for k, (_, _, content) in enumerate(rows) for w in content.split()]
    sm = difflib.SequenceMatcher(None, [norm(w) for _, w in ours_all], [norm(w) for w in words], autojunk=False)
    out = {k: [] for k in range(len(rows))}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                out[ours_all[i][0]].append(tidy(words[j], ours_all[i][1]))
            continue
        piece = [tidy(w) for w in words[j1:j2]]
        k = ours_all[min(i1, len(ours_all) - 1)][0]
        if i1 >= len(ours_all):  # (after our last word: with the last verse)
            out[k].extend(piece)
            continue
        if i2 > i1 and ours_all[i1][0] != ours_all[i2 - 1][0]:  # (across a verse's end)
            at = i1
            for w in piece:
                near = [(similarity(list(norm(w)), list(norm(ours_all[i][1]))), i) for i in range(at, i2)]
                score, i = max(near)
                if score >= 0.5:
                    at = i
                out[ours_all[at][0]].append(w)
            continue
        if i1 > 0 and ours_all[i1 - 1][0] == k - 1:  # at a verse's start
            ends = [n for n, w in enumerate(piece) if SENTENCE_END.search(w)]
            if ends:
                out[k - 1].extend(piece[:ends[-1] + 1])
                piece = piece[ends[-1] + 1:]
        out[k].extend(piece)
    return out


def edges(rows, words):
    """Their words before our chapter's first and after its last ([lead], [trail]): a verse
    the two editions put in different chapters (Genesis 31:55 is Rahlfs's 32:1)."""
    ours = [norm(w) for _, _, content in rows for w in content.split()]
    ops = difflib.SequenceMatcher(None, ours, [norm(w) for w in words], autojunk=False).get_opcodes()
    lead = words[:ops[0][4]] if ops[0][0] == "insert" else []
    trail = words[ops[-1][3]:] if ops[-1][0] == "insert" and len(ops) > 1 else []
    return lead, trail


def belongs(piece, ours, theirs):
    """Whether words left over at the edge of their chapter (piece) are the opening or close
    of ours (our neighbouring chapter's words there) that theirs (its own chapter's words
    there) lacks."""
    piece = [norm(w) for w in piece]
    return similarity(piece, [norm(w) for w in ours]) >= 0.6 and similarity(piece, [norm(w) for w in theirs]) < 0.5


def book(slug, library):
    """One book: ({"div:ref": verse} as the Church's edition has it, report)."""
    lib = sqlite3.connect(library)
    aud = sqlite3.connect(ROOT / "data" / "library_audio.sqlite")
    theirs_by_ch = chapters_of(slug)
    ours_by_ch = {}
    for div, seq, ref, content in lib.execute(
            "SELECT div, seq, ref, content FROM segments WHERE text = ? AND seq > 0 ORDER BY div, seq", (slug,)):
        ours_by_ch.setdefault(div, []).append((seq, ref, content))
    changed, report = {}, {"verses": 0, "differ": 0, "places": 0, "reverted": 0, "moved": 0, "chapters": []}
    chosen = {}
    for div, rows in ours_by_ch.items():
        key = [norm(w) for _, _, content in rows for w in content.split()]
        # Their chapter: the one numbered so, unless another matches far better (numbered otherwise).
        best = theirs_by_ch.get(div)
        score = similarity(key, [norm(w) for w in best]) if best else 0
        if score < 0.6:
            for c, words in theirs_by_ch.items():
                s = similarity(key, [norm(w) for w in words])
                if s > score:
                    best, score = words, s
        if score < 0.6:  # (divided otherwise: ours across two of theirs, as Jeremiah's oracles)
            for c, words in theirs_by_ch.items():
                if c + 1 in theirs_by_ch:
                    both = words + theirs_by_ch[c + 1]
                    s = similarity(key, [norm(w) for w in both])
                    if s > score:
                        best, score = both, s
        if not best or score < 0.5:
            report["chapters"].append(f"{div}: no match")
            continue
        chosen[div] = list(best)
    # A verse at the end of their chapter that opens our next (or at the start of theirs that
    # closes our last): moved to the chapter that has it, rather than left with this one's
    # last (first) verse, where the audio would only take it out again.
    for div in list(chosen):
        lead, trail = edges(ours_by_ch[div], chosen[div])
        after, before = chosen.get(div + 1), chosen.get(div - 1)
        if trail and after is not None:
            opening = [w for _, _, content in ours_by_ch[div + 1] for w in content.split()][:len(trail) + 10]
            if belongs(trail, opening, after[:len(trail) + 10]):
                chosen[div] = chosen[div][:-len(trail)]
                chosen[div + 1] = trail + after
                report["moved"] += 1
        if lead and before is not None:
            close = [w for _, _, content in ours_by_ch[div - 1] for w in content.split()][-len(lead) - 10:]
            if belongs(lead, close, before[-len(lead) - 10:]):
                chosen[div] = chosen[div][len(lead):]
                chosen[div - 1] = before + lead
                report["moved"] += 1
    lp_cache = {}
    for div, best in chosen.items():
        rows = ours_by_ch[div]
        theirs = spread(rows, best)
        rec = aud.execute("SELECT file FROM recordings WHERE text = ? AND div = ?", (slug, div)).fetchone()
        if rec and rec[0] not in lp_cache:
            lp_cache = {rec[0]: A.emissions_of(A.RECORDINGS / rec[0])}
        for k, (seq, ref, content) in enumerate(rows):
            report["verses"] += 1
            ours = content.split()
            words, places = theirs[k] or ours, []
            # (The verse's own speech: not the next one's first words, which a word misplaced
            # at its end would match.)
            span = aud.execute("SELECT min(start), max(speech_end) FROM units WHERE text = ? AND div = ? AND seq = ?",
                               (slug, div, seq)).fetchone() if rec else (None,)
            if [norm(w) for w in ours] != [norm(w) for w in words] and span[0] is not None:
                lp = lp_cache[rec[0]]
                window = lp[max(0, int((span[0] - 0.25) / A.FRAME_S)):int((span[1] + 0.25) / A.FRAME_S)]
                words, places = church_verse(words, ours, window)
                report["differ"] += 1
            report["places"] += len(places)
            report["reverted"] += sum(p[2] > REVERT for p in places)
            verse = " ".join(accent_names(words, best))
            for old, new in NAMES.get(slug, {}).get(f"{div}:{ref}", {}).items():
                if verse.count(old) != 1:
                    report["chapters"].append(f"{div}:{ref}: {old} found {verse.count(old)} times")
                verse = verse.replace(old, new, 1)
            if verse != " ".join(ours):
                changed[f"{div}:{ref}"] = verse
    return slug, changed, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*", help="only these books")
    ap.add_argument("--jobs", "-j", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--library", type=Path, default=ROOT / "data" / "library.sqlite",
                    help="a library built without library/church_text.json (Rahlfs's verses)")
    args = ap.parse_args()
    slugs = args.slugs or list(BOOKS)
    lib = sqlite3.connect(args.library)
    if not args.fetch_only and lib.execute("SELECT 1 FROM settings WHERE key = 'church_text'").fetchone():
        sys.exit(f"{args.library} was built with library/church_text.json: move it aside and run "
                 "scripts/library.py first (or give --library), so the verses compared are Rahlfs's")
    lib.close()
    for slug in slugs:  # (fetched one at a time, gently, before the work in parallel)
        n = len(chapters_of(slug))
        print(f"{slug}: {n} chapters", flush=True)
    if args.fetch_only:
        return
    out = json.loads(OUT.read_text()) if OUT.exists() and args.slugs else {}
    total = {"verses": 0, "differ": 0, "places": 0, "reverted": 0, "moved": 0}
    with ProcessPoolExecutor(args.jobs) as pool:
        for slug, changed, report in pool.map(functools.partial(book, library=args.library), slugs):
            out[slug] = changed
            for k in total:
                total[k] += report[k]
            print(f"{slug}: {report['differ']} of {report['verses']} verses differ in wording; at {report['reverted']} "
                  f"of {report['places']} places the reader follows Rahlfs; {report['moved']} verses moved across a chapter's "
                  f"end; {len(changed)} verses changed"
                  + (f"  ({'; '.join(report['chapters'])})" if report["chapters"] else ""), flush=True)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"all: {total['differ']} of {total['verses']} verses differ in wording; at {total['reverted']} of "
          f"{total['places']} places the reader follows Rahlfs; {total['moved']} verses moved across a chapter's end; "
          f"{sum(map(len, out.values()))} verses changed "
          f"-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
