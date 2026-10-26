# Acroaterium

*Ἀκροατήριον*, a hall for listening: a reader for Greek texts read aloud. The text
scrolls with its recording, the line, verse or sentence being spoken highlighted as it
plays, with whatever help for reading it a text has. Poetry, prose and scripture are all
read with the same reader.

**Reading along**

- **Text and recording in step**: recordings are aligned with their text line by line
  (verse) or sentence by sentence (prose), even where they hold more than the text:
  spoken titles, introductions, announcements, a translation read after each passage.
- **Follow and focus**: the page keeps what is being read in view; focused mode shows
  just it and its neighbours, large. Verse is set in lines, prose in paragraphs, each
  with its own references: line numbers, chapter and verse, or sections such as
  Stephanus pages.
- **Word cards**: tap a word for its lemma, a short definition and its morphology, and
  its role in the sentence, from a treebank where the text has one, else from automatic
  parsing.
- **Paraphrases and translations**: a paraphrase under each line, linked word by word
  (hovering a word shows its equivalent); translations line by line or passage by
  passage, in more than one language.
- **Notes**: commentaries and scholia on a line, from several sources side by side.
- **Listening aids**: speed and volume, long pauses shortened, "pause on hover" (a tap on
  a phone) stops at the end of the verse under the pointer, a chapter's end stops, goes
  on to the next or repeats, and the place in each book is remembered.

Keys: Space plays or pauses, ← ↑ and → ↓ step a verse, F toggles focused mode, S shows
the notes, B bookmarks the verse.

**The library**

- **Reading plans**: a text, a collection or a category read in so many days, a reading
  a day, each ending where a chapter ends if one is near.
- **Bookmarks**: the bookmark button (or B) marks the verse being read. Bookmarks and
  plans are kept by each device, in its browser.
- **Downloads**: a text with a recording can be kept on the device, books and recording,
  to read and hear offline; the whole library's text (without recordings) can be kept too.
- **Interface** in English or modern Greek.

Which texts and categories make up the library is local: list them in
`library/catalogue.json` (git-ignored; `library/catalogue.example.json` shows the
format, and `scripts/library.py` documents the sources it can fetch, such as Wikisource
and Perseus, or local text files) and build it with `python3 scripts/library.py`. A text
can be hidden (`"hidden": true`): kept, and readable by a link, but not listed.

## Running it

Needs Python 3 (standard library only) and, for the audio, ffmpeg.

1. Build the library: `data/library.sqlite` from the catalogue with `scripts/library.py`
   (a text with its own import scripts, for a paraphrase or commentaries, is prepared
   first, in the order [data/README.md](data/README.md#rebuilding) gives). The scripts
   download their sources, cached in `data/raw/`.
2. Recordings go under `recordings/`, as the catalogue's `"audio"` entries name them;
   they stay local, out of git. Align them with the text, `scripts/align_library.py`
   (into `data/library_audio.sqlite`). This needs PyTorch and a speech model; see
   `requirements-audio.txt`. It takes hours on a laptop CPU, much less on a GPU.
3. `scripts/compact_library.py` writes the compact library, the whole library in one file
   for offline use and the Android app.
4. Start the server and open the page:

   ```
   python3 scripts/serve.py                 # then http://localhost:8000/
   python3 scripts/serve.py --show-hidden   # also list the hidden texts, greyed
   ```

### On a phone

The reader is an installable web app: "Install" or "Add to Home Screen" in the browser
gives it its own icon and window, and books already opened stay readable offline. On an
iPhone or iPad, open it from the Home Screen to keep downloads: Safari clears a site's
data after a week unused. Run the server with `--host 0.0.0.0` so the phone can reach it.
Phones only install, and only work offline, over HTTPS (plain `localhost` is the
exception), so put it behind an HTTPS proxy, e.g. `tailscale serve 8000` or Caddy.

There is also an Android app, a native shell around the same pages with native audio
(the screen off, the lock screen) and the library built in: [mobile/README.md](mobile/README.md).

## Licence

The code is under the [MIT licence](LICENSE). The texts and data the scripts download
keep their own licences, several of them CC BY-SA; [data/README.md](data/README.md#sources-and-licences)
lists each source and its terms.
