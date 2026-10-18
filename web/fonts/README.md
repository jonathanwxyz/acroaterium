# Fonts

The fonts for the Greek text, served from this site rather than from Google Fonts, so the
page doesn't wait on another server before it can draw. The files are Google Fonts' WOFF2
builds, split by script (Greek, Greek Extended, Latin, Latin Extended); the top of `../style.css`
declares them, and a browser downloads only the parts a page uses.

| Font | Files | Licence |
| --- | --- | --- |
| Gentium Book Plus (SIL International) | `gentium-book-plus-*` | [OFL-gentiumbookplus.txt](OFL-gentiumbookplus.txt) |
| EB Garamond (The EB Garamond Project Authors) | `eb-garamond-*` | [OFL-ebgaramond.txt](OFL-ebgaramond.txt) |
| GFS Didot (Greek Font Society) | `gfs-didot-*` | [OFL-gfsdidot.txt](OFL-gfsdidot.txt) |
| GFS Neohellenic (Greek Font Society) | `gfs-neohellenic-*` | [OFL-gfsneohellenic.txt](OFL-gfsneohellenic.txt) |
| GFS Porson (Greek Font Society) | `gfs-porson-*` | [OFL-gfsporson.txt](OFL-gfsporson.txt) |
| GFS Decker (Greek Font Society) | `gfs-decker2-*` | [OFL-gfsdecker.txt](OFL-gfsdecker.txt) |
| GFS Complutum (Greek Font Society) | `gfs-complutum-*` | [OFL-gfscomplutum.txt](OFL-gfscomplutum.txt) |
| Alegreya (The Alegreya Project Authors) | `alegreya-*` | [OFL-alegreya.txt](OFL-alegreya.txt) |

GFS Didot's bold and italic, GFS Porson, GFS Decker and GFS Complutum aren't on Google
Fonts: they're the Greek Font Society's own (`GFSDidotBold.otf`, `GFSDidotItalic.otf`,
`GFSPorson.otf` and Complutum's `GFSPolyglot.otf` from CTAN's `fonts/greek/gfs/`;
`GFSDecker.otf` from the Society's `GFS_Decker.zip`), split the same way with fontTools'
`pyftsubset --layout-features='*' --flavor=woff2`.
GFS Decker gives its figures and some signs (" # % & * § « » …) and the Greek question mark
blank glyphs: they're left out of its files (`gfs-decker2-*`), so the browser takes them
from the next font, Gentium. A font file is never changed under the same name (the
service worker and the app keep them for good): a corrected one gets a new name.
GFS Complutum draws ) ] > to the left of their place: its files leave out the brackets and
< >, for Gentium's.

All eight are under the SIL Open Font License 1.1, which allows them to be bundled and
redistributed with software.
