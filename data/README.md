# Data

The reader (`scripts/serve.py`) reads two databases: `library.sqlite`, the texts, and
`library_audio.sqlite`, their recordings. The Iliad is built first, on its own, in
`iliad.sqlite`, and `scripts/library.py` copies it into both.

`iliad.sqlite` holds the Iliad with Theodorus Gaza's paraphrase, line by line, and the
Perseus treebank (lemma, morphology, dependency parse) word by word, its translations,
scholia and commentaries, and its timings. `iliad.tsv` is a plain-text copy of the
`verses` table. Both are rebuilt by the scripts below and stay out of git; `iliad.tsv`
because its source states no licence. The reader doesn't read it: it's where the Iliad is
made.

`library.sqlite` holds the library's texts (tables `categories`, `texts`, `divisions`,
`segments`, …), built by `scripts/library.py` from the local catalogue
(`library/catalogue.json`); its downloads are cached in `raw/library/`. A catalogue entry
with `"reader": "iliad"` is the Iliad: `library.py` copies it in from `iliad.sqlite`
(`iliad_into_library`) as a text like the others, slug `iliad`, with Gaza's paraphrase
(`translations`, lang `paraphrase`, and `paraphrase_links`), its treebank (`treebank`, with
each word's `match`), notes and translations. It stays out of git, like the catalogue.

`library_audio.sqlite` holds the library texts' recordings: which file each book plays
and when each line (verse) or sentence (prose) is spoken, built by
`scripts/align_library.py` from the catalogue's `"audio"` entries and the emissions in
`recordings/`, and for the Iliad copied from `iliad.sqlite`'s `verse_timings` by
`library.py`. It is kept apart so that rebuilding `library.sqlite` leaves the other
texts' timings alone (realign after a rebuild that changes a text). It stays out of git.

`library_compact.sqlite` is both of them in one file, a fifth of the size, for an app to
keep on the device (the server reads the full ones): the same rows, a book's rows of each
kind in one compressed chunk, and what repeats from word to word (lemmas, morphology codes)
stored once. `scripts/compact_library.py` writes it from the full databases and checks it
(it restores them from it and compares every row, and the API's JSON for a sample);
`scripts/compact_read.py` reads it, or writes the full databases back from it. Its
docstring describes the format. It stays out of git. The server hands it out at
`/api/compact` (its version in `X-Version`: the start of the SHA-256 of what it holds,
so the same library built anywhere has the same); the reader keeps it, where asked, as
the whole library on the device (`web/local/`: the service worker then makes the API's
books, cards and notes from it), and the Android app comes with it (`mobile/README.md`).

## Rebuilding

Build the Iliad (`iliad.sqlite`) with the scripts below, in this order; each replaces
only its own tables. Pages are cached in `raw/`. Then run `scripts/library.py`, which
builds `library.sqlite` and copies the Iliad (and its timings, into
`library_audio.sqlite`) in: after changing anything in `iliad.sqlite`, run it again for
the reader to see it. Last, after any change to either, run `scripts/compact_library.py`
to write `library_compact.sqlite` again (about a minute, with its check).

1. `scripts/parse_iliad.py` → `verses` (+ `iliad.tsv`), from vasilestancu.ro, and `books`
   from `book_titles.tsv`
2. `scripts/import_treebank.py` → `sentences`, `tokens`, from Perseus AGDT 2.1
3. `scripts/align_words.py` → `word_links`
4. `scripts/import_paraphrase_alignment.py` → `paraphrase_links`, from the hand-made
   files in `paraphrase_alignment/` (`-v` lists Gaza's unaligned additions)

5. `scripts/import_shortdefs.py` → `shortdefs`, `lemma_defs` (English short definitions;
   after step 2)
6. `scripts/import_translation.py` → `translation_passages` (Murray's English), then
   `scripts/import_translation_lines.py` → `translation_lines` (split per line, from
   `translation_lines/murray_book01.txt`), and `scripts/import_polylas.py` →
   `translation_lines` (Polylas's modern Greek, from Wikisource)
7. `.venv/bin/python scripts/align_audio.py --book N --audio recordings/Iliad/iliadNN.m4a`
   → `verse_timings` (setup in `requirements-audio.txt`), or `scripts/align_all.sh` for
   all 24 books (~4–5 h on CPU). `scripts/check_timings.py` checks the result against
   the pauses in each recording. The slow part (the model's output, cached in
   `recordings/Iliad/cache/`) depends only on the audio and uses a GPU when there is one: to
   make it on another machine, run `scripts/make_emissions.py recordings/` there (any
   number of recordings or folders; no database or text needed) and copy the `.npy`
   files back. Then `align_audio.py --book N --emissions <file>.mms.npy` aligns in
   seconds, without the audio.

The recordings (Project Eustathios, modern pronunciation, Homer only) stay local, see
`.gitignore`. Fetch them as AAC with
`yt-dlp -f "bestaudio[ext=m4a]" -o "recordings/Iliad/%(id)s.%(ext)s" <playlist>` and name
them `recordings/Iliad/iliadNN.m4a` by the book number in each title (the playlist has
Books 16–20 out of order; `recordings/Iliad/playlist.txt` lists it).

8. `scripts/import_scholia.py` → `scholia`, from First1KGreek (`-v` counts per book and
   manuscript)
9. `scripts/import_commentaries.py` → `commentaries`, from Perseus's XML (downloads a
   125 MB archive once and keeps the four files needed in `raw/perseus/`)

`scripts/compare_alignments.py REF CANDIDATE [-v]` scores one alignment file against
another (link precision/recall, per-word agreement).

## Tables

- **verses** `(book, line)`: `homer` (site text), `paraphrase` (Gaza; NULL where he has
  none), `site_line` (the number the site shows).
- **books** `book`: `title`, the spoken intro read before line 1 of that book's
  recording (Ὁμήρου Ἰλιάς. Ῥαψῳδία Α., Β., …; heard for Book 1, assumed for the others).
  Edit `book_titles.tsv` if a recording opens differently.
- **tokens** `seq`: one row per treebank word in reading order, with `book, line`,
  `form, lemma, postag` and the tag decoded (`pos, person, number, tense, mood, voice,
  gender, gram_case, degree`), plus `head, relation` (parse) and `artificial`.
- **sentences** `sentence_id`: treebank sentences.
- **word_links**: joins site words to tokens. `word_index` is the 0-based
  whitespace-separated chunk of `verses.homer`, so split the displayed line on whitespace
  and look each chunk up. A word can have several rows (`split`) and a token can serve
  several words (`joined`). `match` is one of:

  | match | meaning | count |
  |---|---|---|
  | exact | same word | 110,830 |
  | split | one site word = several tokens (μηδέ = μη + δέ) | 816 |
  | orthographic | accents, breathings, case or elision mark differ | 181 |
  | none | no counterpart in the treebank | 56 |
  | joined | several site words = one token (ἦ τοι = ἤτοι) | 14 |
  | variant | different reading in the same slot (ἐς / ἐν) | 11 |
  | movable_nu | ἔτελλεν / ἔτελλε | 5 |

- **paraphrase_links**: Homeric word → Gaza's paraphrase words, one row per pair
  (`book, line, word_index` as in `word_links`; `para_line, para_index` index the
  whitespace chunks of `verses.paraphrase`). Hand-aligned; all 24 books.
  Conventions: an article goes with the word after it, ὦ with its vocative; Gaza's additions (λέγων etc.) stay
  unaligned; split verbs (ἐπὶ … ἔτελλεν) link both parts to the one Attic verb; when
  Gaza moves words across a line break, `para_line` differs from `line`. The source
  format is described at the top of `paraphrase_alignment/book01.txt`, the judgement calls in
  `paraphrase_alignment/CONVENTIONS.md`.

- **shortdefs** `lemma`: the Perseus/Logeion short English definitions
  ([helmadik/shortdefs](https://github.com/helmadik/shortdefs), ~100k Greek lemmas; no
  licence stated, its README asks for credit to Perseus and Logeion).
- **lemma_defs** `lemma`: a definition for each treebank lemma (`tokens.lemma`), with the
  list `entry` it came from and how it was matched: `exact`, `alias`
  (`lemma_aliases.tsv`: Homeric/variant spellings, plus an own definition for ἕ),
  `no_diacritics` (Πηλείδης ~ Πηλεΐδης) or `accents` (ποτέ ~ ποτε). Covers 99.4% of
  Book 1's words and 99.0% of the Iliad's; unmatched are mostly names (Μηριόνης) and
  Homeric forms (ἱρός, εἷος) — add them to `lemma_aliases.tsv`.
- **translation_passages** `(translation, book, line_from)`: A. T. Murray's prose
  translation (Loeb, 1924; public domain) from Perseus (`perseus-eng3`, encoding CC BY-SA
  4.0). Perseus marks the Greek line numbers every 5 lines, so each row covers ~5 lines
  and is not split per verse. In Book 1 its markers stand at the end of their line
  (passages 1–5, 6–10, …), in Books 2–24 at the start (1–4, 5–9, …); the importer detects
  this per book from where sentences end (see `import_translation.py`). Its markers have three slips, merged into the
  preceding passage: 2.720 repeated, 13.825 after 13.830, 20.1 repeated.
- **translation_lines** `(translation, book, line)`: Murray split into single Greek
  lines (Book 1 so far). The split points are hand-made: `translation_lines/murray_book01.txt`
  gives the first words of each line's English; the importer checks the pieces rejoin
  to the passage exactly. Where Murray reorders clauses across lines the cut is the
  best fit, so a line's English can carry a word or two belonging to its neighbour.
- **translation_lines**, `translation = 'polylas'`: Iakovos Polylas's modern Greek verse
  translation (published 1923, public domain) from Greek Wikisource. He keeps Homer's
  line count, and Wikisource marks every 5th line, so lines are taken one for one. A
  book is loaded only if its line count matches the Greek and every marker sits on its
  line: Books 1, 3, 4, 9, 10, 12, 15–17, 20–23 pass; the rest are skipped (a line more or
  less, or a misplaced marker). Within a 5-line block Polylas sometimes drifts a line or
  swaps two; `translation_lines/polylas_book01.txt` corrects Book 1 (22 lines: 1.16–20,
  345–347, 607–610, and swapped pairs 526/7, 537/8, 571/2, 580/1, 597/8). Lines whose
  sense he folds into a neighbour (1.16, 345, 607) have no text. Ordinary enjambment is
  left as is; other books are uncorrected.
- **verse_timings** `(recording, model, book, line)`: where each verse is in a
  recording, in seconds. `line` 0 is the spoken title (`books.title`). `start` = first
  sound, `speech_end` = last sound, `end` = next verse's start (so `end - speech_end` is
  the pause after the verse; up to ~20 s at paragraph breaks). Made by CTC forced
  alignment with Meta's MMS model on romanized text; the Greek wav2vec2 model lost its
  place on Book 1. Check for Book 1: 95.9% of verse starts fall on a pause (median
  0.03 s from its end); the rest are run-on lines read without a pause. Known slip:
  1.312 starts ~1.4 s early (just before the pause that precedes it).

- **scholia** `seq`: the ancient scholia from Dindorf & Maass, one scholion per row:
  `book, line` (and `line_to` when it covers several lines), `source` (the manuscript),
  `text` (opening with its lemma, the words commented on, up to "]"). 31,018 scholia on
  11,286 lines (563 of Book 1's 611). Sources: `A` Venetus A (Dindorf vols 1–2), `A-int`
  its interlinear glosses, `B` Venetus B (vols 3–4), `B-rec` B's later hand, `T`
  Townleianus (Maass, vols 5–6), `T-rec` T's second hand. The same scholion often stands
  in A, B and T alike. The editors' Latin footnotes and the marginal sigla (A=, B+: how far
  another manuscript agrees) are dropped. Editorial brackets (⌈ ⌋ for letters read from
  other manuscripts, 〈 〉 for additions) and the asterisks marking later hands are kept as
  printed. About 50 scholia keep a stray number at the start where the edition's line
  number disagrees with the section's.

- **commentaries** `seq`: English commentaries, one paragraph per row, shown with the
  scholia: `book, line, line_to, source, text`, and `parts` (JSON `[[kind, text], …]`;
  `l` = lemma, the words commented on, `i` = italic, `t` = plain). 17,660 notes on 9,108
  lines. `Leaf`: Walter Leaf, *The Iliad* (1900–02), all 24 books. `Seymour`: T. D.
  Seymour, Books I–III and IV–VI (1891). `Benner`: A. R. Benner, *Selections* (1903),
  Books 1–3, 5, 6, 9, 15, 16, 18, 19, 22, 24. Greek converted from Beta Code. A paragraph
  opening "Vs. 1-7." covers those lines. Benner's "§ 41" and Seymour's "§ 40 c" refer to
  their grammar appendices, which are not imported. Seymour cites books by letter
  (α 1 = Od. 1.1, Π 842 = Il. 16.842).

## Known mismatches (expect these)

**Line numbering.** `line` is the standard (OCT) number. The site numbers straight
through 11.543 and 14.269, which the standard numbering skips, so from those lines to
the end of the book `site_line` = `line` − 1.

**Lines missing from the treebank.** 8.548, 8.550–552 and 9.458–461 are plus-verses
(quoted by ancient authors, not in the medieval manuscripts). The site prints them in
brackets; the treebank and Gaza do not have them. Their words are `match = 'none'`.

**Lines Gaza does not paraphrase** (`paraphrase IS NULL`, 31 lines): the plus-verses above,
plus 1.265, 8.224–226, 8.277, 8.466–468, 12.325, 13.749, 16.381, 16.614–615, 17.585, 18.201,
19.177, 20.312, 21.434, 21.480, 21.510, 23.867, 24.312, 24.693. Most of these repeat lines
found elsewhere in the poem and were evidently absent from his manuscript. At 23.866–867
his text ran the two lines together, so his paraphrase of 866 corresponds to the end of 867.

**Different wording.** The texts differ in 17 lines, 14 of them in Book 1, where the
treebank follows another edition: 1.20, 1.25, 1.48, 1.59, 1.65, 1.105, 1.117, 1.119, 1.277,
1.281, 1.297, 1.309, 1.404, 1.464. In several (1.65, 1.309, 1.464, 1.281) the site's
reading is the one Gaza paraphrases, so the parse may not fit the paraphrase there.

**Editorial notes in the site text.** 23.866 and 23.867 contain notes ("(Perseus: …)",
"(La Gaza, lispește)"); 18.604–605 carry bracketed variants; 14.070's paraphrase is a note
by Gaza. `align_words.py` ignores notes and trailing bracketed variants; the six treebank
words of 23.866 appear on the site only inside its note and have no link.

**Other.** 74 treebank words have no lemma (e.g. Ἄϊδι, 1.3). The treebank's elision
marks are normalised to ’ and all Greek is NFC.

## Sources and licences

The old texts themselves are public domain; the licences below cover the digital
editions they come from. Nothing here restricts private use. They matter once the app or
its data is published: then credit each source and release anything derived from the
CC BY-SA sources (the tables marked so, or a database built from them) under the same
licence. Share-alike reaches the data, not the code that displays it.

| Table(s) | Source | Licence |
|---|---|---|
| `verses` | Homeric text and Theodorus Gaza's paraphrase, [vasilestancu.ro](https://vasilestancu.ro/) | none stated: ask before publishing a copy |
| `tokens`, `sentences`, `word_links` | Perseus [Ancient Greek Dependency Treebank](https://perseusdl.github.io/treebank_data/) 2.1 | CC BY-SA 3.0 US |
| `shortdefs`, `lemma_defs` | Perseus/Logeion short definitions via [helmadik/shortdefs](https://github.com/helmadik/shortdefs) | none stated; credit Perseus and Logeion |
| `translation_passages`, `translation_lines` (murray) | A. T. Murray (Loeb, 1924), [Perseus](https://github.com/PerseusDL/canonical-greekLit) `perseus-eng3` | text public domain; encoding CC BY-SA 4.0 |
| `translation_lines` (polylas) | Iakovos Polylas (1923), [Greek Wikisource](https://el.wikisource.org/) | public domain (Wikisource's own edits CC BY-SA 4.0) |
| `commentaries` | Leaf (1900), Seymour (1891), Benner (1903): Perseus's XML, [bulk download](https://www.perseus.tufts.edu/hopper/opensource/download) | texts public domain; encoding CC BY-SA 3.0 |
| `scholia` | Dindorf & Maass, *Scholia Graeca in Homeri Iliadem* (1875–88), [First1KGreek](https://github.com/OpenGreekAndLatin/First1KGreek) tlg5026.tlg001 | CC BY-SA 4.0 |
| `verse_timings` | made here with Meta's MMS forced aligner ([MahmoudAshraf/mms-300m-1130-forced-aligner](https://huggingface.co/MahmoudAshraf/mms-300m-1130-forced-aligner)) | the model is CC BY-NC 4.0: keep uses non-commercial |
| recordings (not in git) | Project Eustathios readings on YouTube | the readers' copyright; hosted on the site with Project Eustathios's permission, credited "Recording: Project Eustathios"; kept out of git |

`library.sqlite`'s sources are described, with their licences, where `scripts/library.py`
fetches them. One is non-commercial: the Septuagint's (`rahlfs`) transcription and parsing,
CATSS's via Eliran Wong's LXX-Rahlfs-1935 (CC BY-NC-SA 4.0). Rahlfs's 1935 text itself is
public domain in the UK and EU, though not in the US until 2031. The Old Testament's
wording, where the recordings read it so, comes from Greek Wikisource's Septuagint
(`scripts/church_text.py`, kept in `library/church_text.json`), which states no source or
licence for it: it stays out of git.

The fonts (EB Garamond, and Gentium Book Plus as fallback, via Google Fonts) are under the
SIL Open Font License. The code is under the MIT licence (see `LICENSE`).
