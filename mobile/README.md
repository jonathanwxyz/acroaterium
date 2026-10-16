# The reader on Android

A [Capacitor](https://capacitorjs.com) app: a native shell that opens the reader as a
server serves it (`scripts/serve.py`), and gives it what a browser on a phone can't:

- **Native audio** (`PlaybackService`, Media3): the recording goes on with the screen off,
  with the media notification and lock-screen controls (play, pause, a verse back or on,
  the position bar); it pauses for calls and when headphones are unplugged.
- **Downloads as the app's own files** (`AudioFiles`): recordings saved for offline use
  are kept as files the player plays, fetched in pieces as in the browser. Books and word
  cards stay in the service worker's caches, as on the web.

The web pages are the same ones the browser gets. `web/native.js` does the switching: in
the app, it stands in for the `<audio>` element and `navigator.mediaSession` (driving
`ReaderAudioPlugin`), and gives `offline.js` the app's file store. In a browser, it does
nothing.

Because the app opens the server's pages, a deploy updates the app too. Rebuild the APK
only when something under `mobile/` changes.

## Setting up

You need Node 22 or later, JDK 21 and the Android SDK (platform 36). Point Gradle at them:

```sh
export JAVA_HOME=~/.local/share/jdk/jdk-21…   # any JDK 21
export ANDROID_HOME=~/Android/Sdk
# (Some systems' JVMs print a cgroup warning on stdout, which breaks the build's jmod step.)
export JAVA_TOOL_OPTIONS="-Xlog:os+container=error"
```

Then, in `mobile/`:

```sh
npm install
echo https://your.server > server.url    # the server the app opens (kept out of git)
npm run build                            # cap sync + gradlew assembleDebug
npm run install-apk                      # adb install onto a connected phone or emulator
```

`READER_URL=… npm run build` overrides `server.url`.

To test against the server on this machine from the emulator, run `scripts/serve.py`,
then `adb reverse tcp:8000 tcp:8000`, and use `http://localhost:8000` as the server. It
has to be `localhost`, not 10.0.2.2: service workers need a secure origin, and plain
http counts as one only on localhost.

The debug APK installs by sideloading. A Play Store release would need a signing key
(`./gradlew bundleRelease`, configured in `android/app/build.gradle`).

## Debugging

Debug builds allow WebView debugging: open `chrome://inspect` in Chrome on the computer
with the phone connected. Native logs: `adb logcat | grep -i -E "capacitor|media3|reader"`.
