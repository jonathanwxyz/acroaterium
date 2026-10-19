// The app is a native shell around the reader as a server serves it: it opens the server's
// pages (so it is always the server's current version, and its service worker keeps what
// was read for offline use), and gives them native audio and downloads (android/…/reader).
//
// Which server: READER_URL, else the first line of server.url beside this file (kept
// local, out of git), e.g. https://reader.example.org, or for the emulator against
// scripts/serve.py on this machine, http://localhost:8000 after `adb reverse tcp:8000 tcp:8000`.
const fs = require("fs");
const path = require("path");

let url = process.env.READER_URL;
if (!url) {
  try { url = fs.readFileSync(path.join(__dirname, "server.url"), "utf8").split("\n")[0].trim(); } catch {}
}
if (!url) throw new Error("Set READER_URL, or put the server's address in mobile/server.url");

module.exports = {
  appId: "io.github.jonathanwxyz.reader",
  appName: "Acroaterium",
  webDir: "www",
  server: {
    url,
    cleartext: url.startsWith("http:"),  // (only for a local server)
    errorPath: "offline.html",
  },
  android: {
    // (the pages' own colours show behind the status bar: they have viewport-fit=cover)
    adjustMarginsForEdgeToEdge: "auto",
  },
};
