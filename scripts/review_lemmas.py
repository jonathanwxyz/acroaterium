#!/usr/bin/env python3
"""A page for checking the lemmatiser by hand, on this computer only: each word it was in
doubt about (other readings listed: also_possible), in context, its reading and the others
to choose between, or a lemma typed in. The choices go to library/lemma_choices.json (local,
like the catalogue), and scripts/library.py takes them into the library: the card shows the
reading chosen, checked by hand.

    python3 scripts/review_lemmas.py [ion ...] [--port 8001]

then open http://localhost:8001/. It listens on 127.0.0.1 only and isn't part of the
reader's server (scripts/serve.py), so it is never on the site.

lemma_choices.json: {slug: {"div:seq:word": {"lemma", "headword", "definition", "morph",
"form", "picked": "lemmatiser" | "other" | "typed", "lemmatiser": its lemma, "when"}}}.
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "data" / "library.sqlite"
LEMMATISED = ROOT / "data" / "raw" / "lemmatised"
CHOICES = ROOT / "library" / "lemma_choices.json"
FONTS = ROOT / "web" / "fonts"
CONTEXT = 10  # words either side


def load_choices(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_choices(path, choices):
    """Written whole, through a temporary file: never half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".lemma_choices.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(choices, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def items(slug):
    """The words of a text the lemmatiser was in doubt about, in order, each with its
    context and readings (the lemmatiser's first)."""
    lem = json.loads((LEMMATISED / f"{slug}.json").read_text(encoding="utf-8"))
    con = sqlite3.connect(LIBRARY)
    title = (con.execute("SELECT title FROM texts WHERE slug = ?", (slug,)).fetchone() or [slug])[0]
    words = []
    for div, seq, ref, content in con.execute(
            "SELECT div, seq, ref, content FROM segments WHERE text = ? AND seq > 0 ORDER BY div, seq", (slug,)):
        for i, w in enumerate(content.split()):
            words.append((div, seq, ref, i, w, content))
    con.close()
    place = {(d, s, i): k for k, (d, s, r, i, *_) in enumerate(words)}
    out = []
    for key, entry in lem.items():
        div, seq = map(int, key.split(":"))
        if entry["text"] != next((c for d, s, r, i, w, c in words if d == div and s == seq), None):
            continue  # (the text has changed since it was lemmatised)
        for i, r in entry["words"].items():
            if not r.get("also_possible") or (div, seq, int(i)) not in place:
                continue
            k = place[(div, seq, int(i))]
            readings = [{"lemma": r["lemma"], "headword": r.get("headword"), "definition": r.get("definition"),
                         "morph": r.get("morph") or [], "probability": r.get("confidence"), "why": [r.get("source")],
                         "lemmatiser": True}]
            readings += [{"lemma": a["lemma"], "headword": a.get("headword"), "definition": a.get("definition"),
                          "morph": a.get("morph") or [], "probability": a.get("probability"), "why": a.get("why") or [],
                          "lemmatiser": False} for a in r["also_possible"]]
            out.append({"key": f"{div}:{seq}:{i}", "order": k, "ref": words[k][2], "form": words[k][4],
                        "before": " ".join(w[4] for w in words[max(0, k - CONTEXT):k]),
                        "after": " ".join(w[4] for w in words[k + 1:k + 1 + CONTEXT]), "readings": readings})
    out.sort(key=lambda x: x["order"])
    return title, out


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lemma review</title>
<style>
@font-face { font-family: Gentium; src: url(/fonts/gentium-book-plus-400-greek.woff2) format("woff2"); unicode-range: U+0370-03FF; }
@font-face { font-family: Gentium; src: url(/fonts/gentium-book-plus-400-greek-ext.woff2) format("woff2"); unicode-range: U+1F00-1FFF; }
@font-face { font-family: Gentium; src: url(/fonts/gentium-book-plus-400-latin.woff2) format("woff2"); unicode-range: U+0000-024F; }
:root { --bg: #fdf6e3; --card: #fffaf0; --text: #2b2620; --muted: #8b8174; --rule: #e6dfd2; --accent: #b07a1e; --done: #4f7a3a; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1d1a16; --card: #24211c; --text: #e9e2d6; --muted: #8f8577; --rule: #34302a; --accent: #d9a441; --done: #8fbf73; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, sans-serif; }
header { position: sticky; top: 0; z-index: 2; background: var(--bg); border-bottom: 1px solid var(--rule);
  padding: 0.7rem 1rem; display: flex; flex-wrap: wrap; gap: 0.6rem 1.2rem; align-items: center; }
header h1 { font-size: 1.05rem; margin: 0; font-weight: 600; }
header .count { color: var(--muted); font-size: 0.9rem; }
header label { font-size: 0.9rem; color: var(--muted); }
header select, header .keys { font-size: 0.85rem; }
header .keys { color: var(--muted); margin-left: auto; }
main { max-width: 52rem; margin: 0 auto; padding: 1rem; }
.item { background: var(--card); border: 1px solid var(--rule); border-radius: 8px; padding: 0.9rem 1rem; margin-bottom: 0.9rem; }
.item.focus { outline: 2px solid var(--accent); outline-offset: 2px; }
.item .head { display: flex; justify-content: space-between; color: var(--muted); font-size: 0.85rem; margin-bottom: 0.3rem; }
.item .status.done { color: var(--done); font-weight: 600; }
.context { font-family: Gentium, "Gentium Book Plus", serif; font-size: 1.15rem; line-height: 1.6; margin: 0 0 0.6rem; }
.context mark { background: none; color: var(--accent); font-weight: 700; border-bottom: 2px solid var(--accent); }
.readings { display: grid; gap: 0.4rem; }
.reading { display: grid; grid-template-columns: 1.6rem 1fr auto; gap: 0 0.6rem; align-items: baseline; text-align: left;
  width: 100%; padding: 0.5rem 0.7rem; border: 1px solid var(--rule); border-radius: 6px; background: transparent;
  color: inherit; font: inherit; cursor: pointer; }
.reading:hover { border-color: var(--accent); }
.reading.chosen { border-color: var(--done); box-shadow: inset 0 0 0 1px var(--done); }
.reading .n { color: var(--muted); font-size: 0.85rem; }
.reading .lemma { font-family: Gentium, "Gentium Book Plus", serif; font-size: 1.15rem; }
.reading .def { color: var(--text); }
.reading .meta { grid-column: 2 / 4; color: var(--muted); font-size: 0.85rem; }
.reading .p { color: var(--muted); font-size: 0.85rem; white-space: nowrap; }
.reading a { color: var(--muted); font-size: 0.8rem; margin-left: 0.5rem; text-decoration: none; }
.reading a:hover { text-decoration: underline; }
.typed { display: flex; gap: 0.5rem; margin-top: 0.2rem; }
.typed input { flex: 1; font: 1.05rem Gentium, "Gentium Book Plus", serif; padding: 0.35rem 0.5rem; border: 1px solid var(--rule);
  border-radius: 6px; background: var(--bg); color: var(--text); }
.typed button, .clear { font: inherit; font-size: 0.9rem; padding: 0.35rem 0.7rem; border: 1px solid var(--rule); border-radius: 6px;
  background: transparent; color: var(--text); cursor: pointer; }
.typed.chosen input { border-color: var(--done); box-shadow: inset 0 0 0 1px var(--done); }
.clear { margin-top: 0.5rem; color: var(--muted); }
.empty { color: var(--muted); text-align: center; margin: 3rem 0; }
</style>
</head>
<body>
<header>
  <h1 id="title">Lemma review</h1>
  <span class="count" id="count"></span>
  <label>Text <select id="text"></select></label>
  <label>Show <select id="show"><option value="todo">undecided</option><option value="done">decided</option><option value="all">all</option></select></label>
  <span class="keys">j/k next/previous · 1–9 choose · t type · u undo</span>
</header>
<main id="list"></main>
<script>
const TEXTS = __TEXTS__;
let data = { items: [], choices: {} }, focus = 0;
const $ = (s, el = document) => el.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const textSel = $("#text"), showSel = $("#show");
for (const t of TEXTS) textSel.append(new Option(t, t));
try { textSel.value = localStorage.getItem("review:text") || TEXTS[0]; showSel.value = localStorage.getItem("review:show") || "todo"; } catch {}

async function load() {
  const r = await fetch(`/api/items?text=${encodeURIComponent(textSel.value)}`);
  data = await r.json();
  $("#title").textContent = `Lemma review · ${data.title}`;
  focus = 0;
  render();
}
function visible() {
  return data.items.filter((it) => showSel.value === "all" || (showSel.value === "done") === Boolean(data.choices[it.key]));
}
function render() {
  const list = $("#list");
  list.replaceChildren();
  const done = data.items.filter((it) => data.choices[it.key]).length;
  $("#count").textContent = `${done} of ${data.items.length} decided`;
  const items = visible();
  if (!items.length) list.append(el("p", "empty", showSel.value === "todo" ? "All decided." : "Nothing here yet."));
  items.forEach((it, k) => list.append(itemEl(it, k)));
  setFocus(Math.min(focus, items.length - 1), false);
}
function itemEl(it, k) {
  const box = el("section", "item");
  box.dataset.key = it.key;
  box.dataset.k = k;
  const choice = data.choices[it.key];
  const head = el("div", "head");
  head.append(el("span", null, `${it.ref} · ${it.form}`), el("span", "status" + (choice ? " done" : ""), choice ? `✓ ${choice.headword || choice.lemma}` : "undecided"));
  const ctx = el("p", "context");
  ctx.append(`…${it.before} `, el("mark", null, it.form), ` ${it.after}…`);
  const rs = el("div", "readings");
  it.readings.forEach((r, n) => {
    const b = el("button", "reading");
    b.type = "button";
    if (choice && choice.picked !== "typed" && choice.lemma === r.lemma) b.classList.add("chosen");
    const name = el("span", "lemma", r.headword || r.lemma.replace(/\\d+$/, ""));
    const a = el("a", null, "Logeion ↗");
    a.href = `https://logeion.uchicago.edu/${encodeURIComponent(r.headword || r.lemma.replace(/\\d+$/, ""))}`;
    a.target = "_blank";
    a.rel = "noopener";
    a.addEventListener("click", (e) => e.stopPropagation());
    name.append(a);
    const p = el("span", "p", r.probability == null ? "" : `${Math.round(r.probability * 100)}%`);
    const why = r.lemmatiser ? `lemmatiser's choice (${r.why[0]})` : `also possible: ${r.why.join(", ")}`;
    const meta = el("span", "meta", [r.lemma !== (r.headword || r.lemma) ? r.lemma : null, r.morph.join(" | "), why].filter(Boolean).join(" · "));
    const top = el("span");
    top.append(name, r.definition ? el("span", "def", ` — ${r.definition}`) : "");
    b.append(el("span", "n", String(n + 1)), top, p, meta);
    b.addEventListener("click", () => choose(it, { ...r, picked: r.lemmatiser ? "lemmatiser" : "other" }));
    rs.append(b);
  });
  const typed = el("form", "typed" + (choice?.picked === "typed" ? " chosen" : ""));
  const input = el("input");
  input.placeholder = "another lemma…";
  input.lang = "grc";
  if (choice?.picked === "typed") input.value = choice.lemma;
  const ok = el("button", null, "Use");
  ok.type = "submit";
  typed.append(input, ok);
  typed.addEventListener("submit", (e) => {
    e.preventDefault();
    const lemma = input.value.trim().normalize("NFC");
    if (lemma) choose(it, { lemma, headword: null, definition: null, morph: [], picked: "typed" });
  });
  rs.append(typed);
  box.append(head, ctx, rs);
  if (choice) {
    const clear = el("button", "clear", "Undo choice");
    clear.type = "button";
    clear.addEventListener("click", () => choose(it, null));
    box.append(clear);
  }
  box.addEventListener("click", () => setFocus(k, false));
  return box;
}
async function choose(it, r) {
  const body = { text: textSel.value, key: it.key, choice: r && {
    lemma: r.lemma, headword: r.headword ?? null, definition: r.definition ?? null, morph: r.morph ?? [], picked: r.picked,
    form: it.form, lemmatiser: it.readings[0].lemma } };
  const res = await fetch("/api/choice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) { alert(`Not saved: ${await res.text()}`); return; }
  data.choices = (await res.json()).choices;
  const k = focus;
  render();
  // On to the next undecided one (the list, if it shows undecided only, has closed up).
  setFocus(showSel.value === "todo" ? k : Math.min(k + 1, visible().length - 1));
}
function setFocus(k, scroll = true) {
  const items = [...document.querySelectorAll(".item")];
  items.forEach((x) => x.classList.remove("focus"));
  if (k < 0 || !items[k]) return;
  focus = k;
  items[k].classList.add("focus");
  if (scroll) items[k].scrollIntoView({ block: "center", behavior: "smooth" });
}
document.addEventListener("keydown", (e) => {
  if (e.target.closest?.("input, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  const items = visible(), it = items[focus];
  if (e.key === "j") setFocus(Math.min(focus + 1, items.length - 1));
  else if (e.key === "k") setFocus(Math.max(focus - 1, 0));
  else if (it && /^[1-9]$/.test(e.key) && it.readings[e.key - 1]) {
    const r = it.readings[e.key - 1];
    choose(it, { ...r, picked: r.lemmatiser ? "lemmatiser" : "other" });
  } else if (it && e.key === "t") { e.preventDefault(); document.querySelectorAll(".item")[focus]?.querySelector("input")?.focus(); }
  else if (it && e.key === "u" && data.choices[it.key]) choose(it, null);
  else return;
  e.preventDefault();
});
textSel.addEventListener("change", () => { try { localStorage.setItem("review:text", textSel.value); } catch {} load(); });
showSel.addEventListener("change", () => { try { localStorage.setItem("review:show", showSel.value); } catch {} focus = 0; render(); });
load();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*", default=["ion"])
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--choices", type=Path, default=CHOICES, help="where the choices are kept")
    args = ap.parse_args()
    for slug in args.slugs:
        if not (LEMMATISED / f"{slug}.json").exists():
            sys.exit(f"no lemmatisation for {slug} (data/raw/lemmatised/{slug}.json): run scripts/lemmatise_library.py first")
    cache = {}

    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/":
                self.send(200, PAGE.replace("__TEXTS__", json.dumps(args.slugs, ensure_ascii=False)), "text/html; charset=utf-8")
            elif path == "/api/items":
                slug = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("text", args.slugs[0])
                if slug not in args.slugs:
                    return self.send(404, '{"error": "not a text under review"}')
                if slug not in cache:
                    cache[slug] = items(slug)
                title, its = cache[slug]
                self.send(200, json.dumps({"title": title, "items": its, "choices": load_choices(args.choices).get(slug, {})},
                                          ensure_ascii=False))
            elif path.startswith("/fonts/") and "/" not in path[7:] and (FONTS / path[7:]).is_file():
                self.send(200, (FONTS / path[7:]).read_bytes(), "font/woff2")
            else:
                self.send(404, '{"error": "not found"}')

        def do_POST(self):
            if self.path != "/api/choice":
                return self.send(404, '{"error": "not found"}')
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                slug, key, choice = body["text"], body["key"], body.get("choice")
            except (ValueError, KeyError):
                return self.send(400, '{"error": "bad request"}')
            if slug not in args.slugs or key not in {it["key"] for it in (cache.get(slug) or items(slug))[1]}:
                return self.send(400, '{"error": "no such word under review"}')
            choices = load_choices(args.choices)
            own = choices.setdefault(slug, {})
            if choice:
                own[key] = {**{k: choice.get(k) for k in ("lemma", "headword", "definition", "morph", "form", "picked", "lemmatiser")},
                            "when": datetime.datetime.now().isoformat(timespec="seconds")}
            else:
                own.pop(key, None)
            save_choices(args.choices, choices)
            self.send(200, json.dumps({"choices": own}, ensure_ascii=False))

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Lemma review on http://localhost:{args.port}/  ({', '.join(args.slugs)}; choices -> {args.choices})  Ctrl-C to stop")
    server.serve_forever()


if __name__ == "__main__":
    main()
