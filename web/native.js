// In the Android app (mobile/): the recording is played natively (ReaderAudioPlugin), so it
// goes on with the screen off and from one page to the next, with the media notification;
// and downloaded recordings are kept as the app's files. In a browser, nothing here is used.
//
// nativeApp.audio() stands in for the <audio> element: the properties, methods and events
// app.js uses. Its time is the player's last report carried on by the clock (the player is
// asked over a bridge, and reports four times a second while playing, which is also what
// keeps the reader following in the background, where it gets no animation frames).
// navigator.mediaSession is replaced likewise: its titles go to the notification, whose
// track buttons call its handlers. nativeApp.files is the download store offline.js uses.
window.nativeApp = (() => {
  const cap = window.Capacitor;
  if (!cap?.isNativePlatform?.() || !cap.PluginHeaders?.some((p) => p.name === "ReaderAudio")) return null;
  const call = (method, options = {}) => cap.nativePromise("ReaderAudio", method, options);
  const listen = (event, fn) => cap.addListener("ReaderAudio", event, fn);

  function audio() {
    const target = new EventTarget();
    const fire = (type) => target.dispatchEvent(new Event(type));
    let src = "", duration = NaN, readyState = 0, paused = true, ended = false;
    let rate = 1, volume = 1, muted = false;
    let base = 0, baseAt = performance.now();  // the player's last reported position, and when
    let advancing = false;  // the player reported moving (playing, not waiting for data)
    let wantPlay = false;  // play() asked, not yet reported playing (a report meanwhile isn't a pause)
    let seeking = 0;  // seeks asked, not yet made
    const now = () => (advancing ? base + ((performance.now() - baseAt) / 1000) * rate : base);
    const clamp = (t) => (Number.isFinite(duration) ? Math.min(t, duration) : t);

    function report(s) {
      if (!s || s.url !== src) return;  // (another recording's, or before this one's load)
      if (s.duration != null && s.duration !== duration) {
        duration = s.duration;
        fire("durationchange");
      }
      if (s.ready && readyState < 1) {
        readyState = 4;
        fire("loadedmetadata");
        fire("canplay");
      }
      // (A report of where it was, before a seek just asked for was made: not taken.)
      if (!seeking) { base = s.position; baseAt = performance.now(); advancing = s.advancing; }
      if (s.playing) wantPlay = false;
      const nowPaused = !s.playing && !wantPlay;
      if (nowPaused !== paused) {
        paused = nowPaused;
        if (!paused) ended = false;
        fire(paused ? "pause" : "play");
      }
      if (s.ended && !ended) {
        ended = true;
        fire("ended");
      }
      if (!paused) fire("timeupdate");
    }
    listen("state", report);

    const el = {
      addEventListener: (...a) => target.addEventListener(...a),
      removeEventListener: (...a) => target.removeEventListener(...a),
      get src() { return src; },
      set src(value) {
        src = new URL(value, location.href).href;
        duration = NaN; readyState = 0; ended = false;
        const meta = navigator.mediaSession?.metadata;
        call("load", { url: src, ...(meta ? titles(meta) : {}) }).then(report).catch(() => fire("error"));
      },
      get currentTime() { return clamp(now()); },
      set currentTime(t) {
        base = Math.max(0, Number(t)); baseAt = performance.now();
        ended = false;
        seeking++;
        fire("seeking");
        call("seek", { position: base }).catch(() => {}).finally(() => {
          seeking--;
          fire("seeked");
          fire("timeupdate");
        });
      },
      get duration() { return duration; },
      get readyState() { return readyState; },
      get paused() { return paused; },
      get ended() { return ended; },
      play() {
        if (paused) {
          base = now(); baseAt = performance.now();
          paused = false; ended = false; wantPlay = true;
          fire("play");
        }
        return call("play").then(report, (err) => { wantPlay = false; paused = true; fire("pause"); throw err; });
      },
      pause() {
        wantPlay = false;
        if (!paused) {
          base = now(); baseAt = performance.now();
          paused = true; advancing = false;
          fire("pause");
        }
        call("pause").then(report).catch(() => {});
      },
      get playbackRate() { return rate; },
      set playbackRate(v) {
        base = now(); baseAt = performance.now();
        rate = Number(v);
        call("setRate", { rate });
        fire("ratechange");
      },
      defaultPlaybackRate: 1,
      get volume() { return volume; },
      set volume(v) { volume = Number(v); call("setVolume", { volume: muted ? 0 : volume }); fire("volumechange"); },
      get muted() { return muted; },
      set muted(m) { muted = !!m; call("setVolume", { volume: muted ? 0 : volume }); fire("volumechange"); },
    };
    // Leaving the reader stops the recording, as in a browser. (A chapter carried on in
    // another page sets it playing again there.)
    addEventListener("pagehide", () => { if (!paused) call("pause").catch(() => {}); });
    return el;
  }

  // The notification's titles and controls, as navigator.mediaSession gives them.
  const absolute = (u) => new URL(u, location.href).href;
  const titles = (m) => ({ title: m.title, artist: m.artist, album: m.album,
                           artwork: m.artwork?.[0]?.src ? absolute(m.artwork[0].src) : undefined });
  const handlers = {};
  let metadata = null;
  const session = {
    get metadata() { return metadata; },
    set metadata(m) { metadata = m; if (m) call("setMetadata", titles(m)).catch(() => {}); },
    setActionHandler(action, fn) { handlers[action] = fn; },
    setPositionState() {},  // (the notification shows the player's own)
    playbackState: "none",
  };
  try { Object.defineProperty(navigator, "mediaSession", { value: session, configurable: true }); } catch {}
  window.MediaMetadata ??= class { constructor(m = {}) { Object.assign(this, m); } };
  listen("action", ({ action }) => handlers[action]?.({ action }));

  // Downloaded recordings (offline.js): fetched a piece at a time into the app's files.
  const files = {
    list: () => call("listDownloads").then((r) => r.entries),
    fetchPiece: (url, size, piece, k) => call("fetchPiece", { url, size, piece, k }),
    finish: (url) => call("finishDownload", { url }),
    remove: (url) => call("removeDownload", { url }),
  };

  // The status and navigation bars: the page's colour behind them, icons to suit.
  const systemBars = (dark) => call("setSystemBars", { dark, color: dark ? "#1d1a16" : "#fdf6e3" }).catch(() => {});

  return { audio, files, systemBars };
})();
