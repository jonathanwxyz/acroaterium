# Iliad Reader

A reader for Greek texts that follows along with a recording: the verse being recited
is highlighted as it plays. It began with Homer's *Iliad*, where each line comes with
help for reading it:

- **Gaza's paraphrase**: Theodorus Gaza's Byzantine prose paraphrase under each line;
  hovering a Homeric word shows its equivalent in the paraphrase (all 24 books).
- **Word cards**: click a word for its lemma, a short definition, its morphology and its
  role in the sentence (from the Perseus treebank).
- **Translations**: A. T. Murray's English (line by line in Book 1, in short passages
  elsewhere) and Iakovos Polylas's modern Greek (13 books so far).
- **σχόλια**: the ancient scholia (Venetus A and B, Townleianus) and the English
  commentaries of Leaf, Seymour and Benner on each line.
- **Focused mode**: just the current verse and its neighbours, large.
- **Listening aids**: long pauses are shortened, "pause on hover" stops at the end of the
  verse under the pointer, and the place in each book is remembered.

Keys: Space plays or pauses, ← ↑ and → ↓ step a verse, F toggles focused mode, S shows
the scholia, B bookmarks the verse.

Other texts live in a **library**, the start page: texts grouped by category, read a
book at a time with the same reader, which shows whatever each text has (prose is set
in paragraphs, and the position box jumps to a line, or to a Stephanus section such as
`172a`). Which texts and categories make up the library is local: list them in
`library/catalogue.json` (git-ignored; `library/catalogue.example.json` shows the
format, and `scripts/library.py` documents the sources it can fetch: Wikisource poems
and Perseus prose) and build it with `python3 scripts/library.py`.

A work in several books opens from the library at the book last read, with a menu of the
others; texts given the same `collection` in the catalogue (the books of a Testament) are
listed together, with a menu of their books and, under each, its chapters.

**The day's readings**: with `"lectionary"` in the catalogue, a category starts with the
day's readings in the Orthodox lectionary (the Ecumenical Patriarchate's, as the Greek
Orthodox Archdiocese of America's online chapel publishes them), each a link to its
passage: `&v=` also takes a reading in pieces, across chapters (`&v=33-40,12.1-2`), and
the recording skips what lies between them.

**Bookmarks**: the bookmark button in the reader's top bar (or B) marks the verse being
read; the library lists the bookmarks, and deletes them. They are kept by the server in
`data/bookmarks.json` (git-ignored), so every device using it shares them.

**Links to a verse**: `&v=` opens a book at a verse, or shows a range of verses alone:
`read.html?text=iliad&book=1&v=40`, `&v=40-60`, or for Plato a Stephanus section,
`&v=17a` or `&v=17a-18c` (`17` is the whole of section 17).

## Running it

Needs Python 3 (standard library only) and, for the audio, ffmpeg.

1. Build the database, `data/iliad.sqlite`. The scripts download their sources (cached
   in `data/raw/`); run them in the order given in [data/README.md](data/README.md#rebuilding).
2. Recordings: the reader expects one per book, `recordings/Iliad/iliadNN.m4a`. The readings
   used are Project Eustathios's on YouTube; data/README.md gives the `yt-dlp` command.
   They stay local, out of git.
3. Align the recordings with the text (`scripts/align_audio.py`, or
   `scripts/align_all.sh` for all 24 books). This needs PyTorch and a speech model; see
   `requirements-audio.txt`. It takes a few hours on a laptop CPU, much less on a GPU.
4. Start the server and open the page:

   ```
   python3 scripts/serve.py      # then http://localhost:8000/ (the Iliad: read.html?book=1)
   ```

### As an app on a phone

The reader is an installable web app: "Install" or "Add to Home Screen" in the browser
gives it its own icon and window, and books already opened stay readable offline (the
audio still needs the server). Run the server with `--host 0.0.0.0` so the phone can
reach it. Phones only install, and only work offline, over HTTPS (plain `localhost` is
the exception), so put it behind an HTTPS proxy, e.g. `tailscale serve 8000` or Caddy.

## Licence

The code is under the [MIT licence](LICENSE). The texts and data the scripts download
keep their own licences, several of them CC BY-SA; [data/README.md](data/README.md#sources-and-licences)
lists each source and its terms.
