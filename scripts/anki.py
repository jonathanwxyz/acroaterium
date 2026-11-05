"""An Anki deck package (.apkg) of the library's lemmas, one note per lemma, for
serve.py's /api/anki (standard library only). The front shows a noun's or an adjective's
dictionary form where there is one (its Form: ὁ λόγος, -ου; headwords.py), else the lemma;
where asked (its Reverse field filled), a second card goes from the meaning to the Greek.

The package is Anki's older format (collection.anki2, schema 11), which every Anki imports,
the desktop's, AnkiDroid and AnkiMobile alike. What makes decks made from different texts
add up, rather than repeat, is that a note's id for Anki (its guid) is made from its lemma
alone, and the note type and the deck have ids of their own that never change: importing a
package whose lemma is already in the collection finds that note and adds none. A note's
modification time is fixed too, and old, so Anki's default on import ("update notes if
newer") leaves a note already there as it is (its sentence from the text it came from first,
and anything changed in Anki since); "Always" in Anki's import options refreshes them.
"""
import hashlib
import io
import json
import sqlite3
import time
import zipfile

# Fixed ids: the same note type and deck in every package (never change them, or Anki
# would take a package's notes for a new kind, and add them all again).
MODEL_ID = 1730000000001
DECK_ID = 1730000000002
DECK_NAME = "Acroaterium"
FIXED_MOD = 1730000000  # (seconds: 2024-10-27; notes and the note type, so none is "newer")
MAX_POSITION = 1_000_000
FIELDS = ["Lemma", "Definition", "Picture", "Sentence", "Reference", "Form", "Reverse"]

# The front: the dictionary form where there is one (ὁ λόγος, -ου), else the lemma.
FRONT = '<div class="lemma">{{#Form}}{{Form}}{{/Form}}{{^Form}}{{Lemma}}{{/Form}}</div>'
BACK = """{{FrontSide}}
<hr id="answer">
<div class="definition">{{Definition}}</div>
{{#Picture}}<div class="picture">{{Picture}}</div>{{/Picture}}
{{#Sentence}}<div class="sentence">{{Sentence}}</div>{{/Sentence}}
{{#Reference}}<div class="reference">{{Reference}}</div>{{/Reference}}"""
# The reverse card, from the meaning (and the picture) to the Greek: made only for a note
# whose Reverse field has something in it, as Anki's "Basic (optional reversed card)".
REVERSE_FRONT = """{{#Reverse}}<div class="definition">{{Definition}}</div>
{{#Picture}}<div class="picture">{{Picture}}</div>{{/Picture}}{{/Reverse}}"""
REVERSE_BACK = """{{FrontSide}}
<hr id="answer">
<div class="lemma">{{#Form}}{{Form}}{{/Form}}{{^Form}}{{Lemma}}{{/Form}}</div>
{{#Sentence}}<div class="sentence">{{Sentence}}</div>{{/Sentence}}
{{#Reference}}<div class="reference">{{Reference}}</div>{{/Reference}}"""
CSS = """.card { font-family: "Gentium Book Plus", "GFS Didot", "Times New Roman", serif; font-size: 22px;
  text-align: center; color: #222; background: #fdf6e3; }
.nightMode.card, .night_mode .card { color: #ddd; background: #1f1d1a; }
.lemma { font-size: 1.8em; }
.definition { font-family: system-ui, sans-serif; font-size: 0.85em; margin: 0.4em 0; }
.picture img { max-width: 14rem; max-height: 14rem; border-radius: 6px; margin: 0.4em 0; }
.sentence { margin: 0.8em auto 0.2em; max-width: 34em; line-height: 1.5; }
.sentence b { color: #b5651d; }
.reference { font-family: system-ui, sans-serif; font-size: 0.65em; color: #888; }"""

SCHEMA = """
CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null, scm integer not null,
    ver integer not null, dty integer not null, usn integer not null, ls integer not null, conf text not null,
    models text not null, decks text not null, dconf text not null, tags text not null);
CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null, mod integer not null,
    usn integer not null, tags text not null, flds text not null, sfld integer not null, csum integer not null,
    flags integer not null, data text not null);
CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null, ord integer not null,
    mod integer not null, usn integer not null, type integer not null, queue integer not null, due integer not null,
    ivl integer not null, factor integer not null, reps integer not null, lapses integer not null,
    left integer not null, odue integer not null, odid integer not null, flags integer not null, data text not null);
CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null, ease integer not null,
    ivl integer not null, lastIvl integer not null, factor real not null, time integer not null, type integer not null);
CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
CREATE INDEX ix_notes_usn on notes (usn);
CREATE INDEX ix_cards_usn on cards (usn);
CREATE INDEX ix_revlog_usn on revlog (usn);
CREATE INDEX ix_cards_nid on cards (nid);
CREATE INDEX ix_cards_sched on cards (did, queue, due);
CREATE INDEX ix_revlog_cid on revlog (cid);
CREATE INDEX ix_notes_csum on notes (csum);
"""

BASE91 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&()*+,-./:;<=>?@[]^_`{|}~"


def guid_of(lemma):
    """A lemma's note's guid: the same in every package (so Anki knows the note again)."""
    n = int.from_bytes(hashlib.sha256(f"acroaterium:{lemma}".encode()).digest()[:8], "big")
    out = ""
    while n:
        n, r = divmod(n, len(BASE91))
        out = BASE91[r] + out
    return out


def note_id(lemma):
    """A lemma's note's id (and its card's, +1): stable too, and in Anki's millisecond range."""
    return 1_500_000_000_000 + int.from_bytes(hashlib.sha256(f"id:{lemma}".encode()).digest()[:5], "big") * 2


def checksum(text):
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)


def model():
    return {
        "id": MODEL_ID, "name": "Acroaterium lemma", "type": 0, "mod": FIXED_MOD, "usn": -1, "sortf": 0,
        "did": DECK_ID, "tags": [], "vers": [], "latexPre": "", "latexPost": "", "latexsvg": False, "req": [[0, "any", [0]], [1, "all", [FIELDS.index("Reverse")]]],
        "flds": [{"name": name, "ord": i, "sticky": False, "rtl": False, "font": "Gentium Book Plus", "size": 20,
                  "media": [], "description": ""} for i, name in enumerate(FIELDS)],
        "tmpls": [{"name": "Greek → meaning", "ord": 0, "qfmt": FRONT, "afmt": BACK, "did": None, "bqfmt": "", "bafmt": ""},
                  {"name": "Meaning → Greek", "ord": 1, "qfmt": REVERSE_FRONT, "afmt": REVERSE_BACK, "did": None,
                   "bqfmt": "", "bafmt": ""}],
        "css": CSS,
    }


def deck():
    return {"id": DECK_ID, "name": DECK_NAME, "desc": "Lemmas from the texts of Acroaterium, one note each.",
            "mod": FIXED_MOD, "usn": -1, "collapsed": False, "browserCollapsed": False, "dyn": 0, "conf": 1,
            "extendNew": 0, "extendRev": 0, "newToday": [0, 0], "revToday": [0, 0], "lrnToday": [0, 0], "timeToday": [0, 0]}


DECK_CONF = {"1": {"id": 1, "name": "Default", "mod": 0, "usn": 0, "maxTaken": 60, "autoplay": True, "timer": 0,
                   "replayq": True, "dyn": False,
                   "new": {"delays": [1, 10], "ints": [1, 4, 7], "initialFactor": 2500, "order": 1, "perDay": 20, "bury": False},
                   "rev": {"perDay": 200, "ease4": 1.3, "ivlFct": 1, "maxIvl": 36500, "fuzz": 0.05, "bury": False},
                   "lapse": {"delays": [10], "mult": 0, "minInt": 1, "leechFails": 8, "leechAction": 1}}}


def package(notes, media, start=0):
    """The .apkg's bytes. notes: [{"lemma", "fields": {name: html}, "tags": [..]}], in the
    order to learn them; media: {name in Anki: path}, the pictures the notes' fields name.
    Each card's place among the new ones is start + its place here: Anki keeps a package's
    places as they are, so decks made one after another (start: the cards made before, as
    the device counts them) come in that order, not mixed. (Under MAX_POSITION: Anki's
    Check Database renumbers the new cards beyond.)"""
    start = max(0, min(start, MAX_POSITION - len(notes)))
    now = int(time.time())
    buf = io.BytesIO()
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO col VALUES (1, ?, ?, ?, 11, 0, 0, 0, ?, ?, ?, ?, '{}')", (
        now - now % 86400, now * 1000, now * 1000,
        json.dumps({"nextPos": start + len(notes) + 1, "curDeck": DECK_ID, "curModel": MODEL_ID, "activeDecks": [DECK_ID]}),
        json.dumps({str(MODEL_ID): model()}, ensure_ascii=False),
        json.dumps({"1": {**deck(), "id": 1, "name": "Default", "desc": ""}, str(DECK_ID): deck()}, ensure_ascii=False),
        json.dumps(DECK_CONF)))
    for pos, note in enumerate(notes, 1):
        values = [note["fields"].get(name, "") for name in FIELDS]
        nid = note_id(note["lemma"])
        tags = " ".join(note.get("tags", []))
        con.execute("INSERT INTO notes VALUES (?, ?, ?, ?, -1, ?, ?, ?, ?, 0, '')",
                    (nid, guid_of(note["lemma"]), MODEL_ID, FIXED_MOD, f" {tags} " if tags else "",
                     "\x1f".join(values), values[0], checksum(values[0])))
        con.execute("INSERT INTO cards VALUES (?, ?, ?, 0, ?, -1, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                    (nid + 1, nid, DECK_ID, FIXED_MOD, start + pos))
        if note["fields"].get("Reverse") and note["fields"].get("Definition"):  # (its sibling, at the same place)
            con.execute("INSERT INTO cards VALUES (?, ?, ?, 1, ?, -1, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                        (nid, nid, DECK_ID, FIXED_MOD, start + pos))  # (even: no other card's id, all odd)
    con.commit()
    collection = con.serialize()
    con.close()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("collection.anki2", collection)
        names = {}
        for i, (name, path) in enumerate(sorted(media.items())):
            z.write(path, str(i), compress_type=zipfile.ZIP_STORED)  # (WebP: already compressed)
            names[str(i)] = name
        z.writestr("media", json.dumps(names, ensure_ascii=False))
    return buf.getvalue()
