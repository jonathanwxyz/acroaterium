package io.github.jonathanwxyz.reader;

import android.content.ComponentName;
import android.graphics.Color;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.view.Window;
import androidx.annotation.NonNull;
import androidx.core.content.ContextCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.media3.common.C;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MediaMetadata;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.PlaybackParameters;
import androidx.media3.common.Player;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.session.MediaController;
import androidx.media3.session.SessionToken;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.common.util.concurrent.ListenableFuture;
import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;
import org.json.JSONException;

/**
 * The reader's recording and downloads, for web/native.js: it plays the recording through
 * PlaybackService (so it goes on with the screen off and between pages, with the media
 * notification) and keeps downloaded recordings as files (AudioFiles).
 *
 * Events: "state" ({playing, advancing, ended, ready, position, duration, rate, volume, url,
 * error}) when any of it changes, and every quarter second while playing (the page may be
 * in the background, where it gets no animation frames: this is what keeps it following); and
 * "action" ({action}) for the notification's track buttons.
 */
@UnstableApi
@CapacitorPlugin(name = "ReaderAudio")
public class ReaderAudioPlugin extends Plugin {

    private static final long TICK_MS = 250;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService downloads = Executors.newSingleThreadExecutor();
    private AudioFiles files;
    private ListenableFuture<MediaController> controllerFuture;
    private MediaController controller;
    private final List<Consumer<MediaController>> waiting = new ArrayList<>();
    private String url; // the recording loaded, as the page asked for it
    private String error;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (controller == null || !controller.isPlaying()) return;
            sendState();
            main.postDelayed(this, TICK_MS);
        }
    };

    @Override
    public void load() {
        files = new AudioFiles(getContext());
        PlaybackService.actions = (action) -> notifyListeners("action", new JSObject().put("action", action));
    }

    @Override
    protected void handleOnDestroy() {
        PlaybackService.actions = null;
        main.removeCallbacks(tick);
        if (controllerFuture != null) MediaController.releaseFuture(controllerFuture);
        downloads.shutdownNow();
    }

    // The controller is had asynchronously, on the main thread: what's asked before then waits.
    private void withController(Consumer<MediaController> fn) {
        main.post(() -> {
            if (controller != null) {
                fn.accept(controller);
                return;
            }
            waiting.add(fn);
            if (controllerFuture != null) return;
            SessionToken token = new SessionToken(getContext(), new ComponentName(getContext(), PlaybackService.class));
            controllerFuture = new MediaController.Builder(getContext(), token).buildAsync();
            controllerFuture.addListener(
                () -> {
                    try {
                        controller = controllerFuture.get();
                    } catch (Exception e) {
                        controllerFuture = null;
                        for (Consumer<MediaController> w : waiting) w.accept(null);
                        waiting.clear();
                        return;
                    }
                    controller.addListener(listener);
                    for (Consumer<MediaController> w : waiting) w.accept(controller);
                    waiting.clear();
                },
                ContextCompat.getMainExecutor(getContext())
            );
        });
    }

    private final Player.Listener listener = new Player.Listener() {
        @Override
        public void onEvents(@NonNull Player player, @NonNull Player.Events events) {
            if (
                events.containsAny(
                    Player.EVENT_IS_PLAYING_CHANGED,
                    Player.EVENT_PLAYBACK_STATE_CHANGED,
                    Player.EVENT_POSITION_DISCONTINUITY,
                    Player.EVENT_PLAYBACK_PARAMETERS_CHANGED,
                    Player.EVENT_TIMELINE_CHANGED,
                    Player.EVENT_VOLUME_CHANGED,
                    Player.EVENT_PLAYER_ERROR
                )
            ) {
                sendState();
            }
            if (events.contains(Player.EVENT_IS_PLAYING_CHANGED) && player.isPlaying()) {
                main.removeCallbacks(tick);
                main.postDelayed(tick, TICK_MS);
            }
        }

        @Override
        public void onPlayerError(@NonNull PlaybackException e) {
            error = e.getErrorCodeName();
        }
    };

    private JSObject state(MediaController c) {
        JSObject s = new JSObject();
        if (c == null) return s.put("error", "no player");
        long duration = c.getDuration();
        s.put("url", url);
        s.put("playing", c.getPlayWhenReady() && c.getPlaybackState() != Player.STATE_ENDED);
        s.put("ended", c.getPlaybackState() == Player.STATE_ENDED);
        s.put("advancing", c.isPlaying());
        s.put("ready", c.getPlaybackState() == Player.STATE_READY || c.getPlaybackState() == Player.STATE_ENDED);
        s.put("position", c.getCurrentPosition() / 1000.0);
        s.put("duration", duration == C.TIME_UNSET ? null : duration / 1000.0);
        s.put("rate", c.getPlaybackParameters().speed);
        s.put("volume", c.getVolume());
        s.put("error", error);
        return s;
    }

    private void sendState() {
        if (controller != null) notifyListeners("state", state(controller));
    }

    private static MediaMetadata metadata(PluginCall call) {
        MediaMetadata.Builder m = new MediaMetadata.Builder()
            .setTitle(call.getString("title"))
            .setArtist(call.getString("artist"))
            .setAlbumTitle(call.getString("album"))
            .setMediaType(MediaMetadata.MEDIA_TYPE_AUDIO_BOOK_CHAPTER);
        String artwork = call.getString("artwork");
        if (artwork != null) m.setArtworkUri(Uri.parse(artwork));
        return m.build();
    }

    // Play the downloaded file where there is one.
    private MediaItem item(String url, MediaMetadata meta) {
        File local = files.complete(url);
        return new MediaItem.Builder()
            .setMediaId(url)
            .setUri(local != null ? Uri.fromFile(local) : Uri.parse(url))
            .setMediaMetadata(meta)
            .build();
    }

    /**
     * Load a recording ({url}, and its title, artist, album, artwork for the notification).
     * The one already loaded (the page opened again, a chapter read on) is left as it is,
     * playing or not, where it is: only its titles change.
     */
    @PluginMethod
    public void load(PluginCall call) {
        String asked = call.getString("url");
        if (asked == null) {
            call.reject("no url");
            return;
        }
        withController((c) -> {
            if (c == null) {
                call.reject("no player");
                return;
            }
            MediaItem now = c.getCurrentMediaItem();
            if (asked.equals(url) && now != null && c.getPlaybackState() != Player.STATE_IDLE) {
                c.replaceMediaItem(0, now.buildUpon().setMediaMetadata(metadata(call)).build());
            } else {
                url = asked;
                error = null;
                c.setMediaItem(item(asked, metadata(call)));
                c.prepare();
            }
            call.resolve(state(c));
        });
    }

    /** The titles the notification shows, for the recording loaded. */
    @PluginMethod
    public void setMetadata(PluginCall call) {
        withController((c) -> {
            MediaItem now = c == null ? null : c.getCurrentMediaItem();
            if (now != null) c.replaceMediaItem(0, now.buildUpon().setMediaMetadata(metadata(call)).build());
            call.resolve();
        });
    }

    @PluginMethod
    public void play(PluginCall call) {
        withController((c) -> {
            if (c == null) {
                call.reject("no player");
                return;
            }
            if (c.getPlaybackState() == Player.STATE_ENDED) c.seekTo(0);
            if (c.getPlaybackState() == Player.STATE_IDLE) c.prepare(); // (after an error: try again)
            c.play();
            call.resolve(state(c));
        });
    }

    @PluginMethod
    public void pause(PluginCall call) {
        withController((c) -> {
            if (c != null) c.pause();
            call.resolve(state(c));
        });
    }

    @PluginMethod
    public void seek(PluginCall call) {
        double position = call.getDouble("position", 0.0);
        withController((c) -> {
            if (c != null) c.seekTo(Math.max(0, Math.round(position * 1000)));
            call.resolve(state(c));
        });
    }

    @PluginMethod
    public void setRate(PluginCall call) {
        float rate = call.getFloat("rate", 1f);
        withController((c) -> {
            if (c != null) c.setPlaybackParameters(new PlaybackParameters(rate));
            call.resolve(state(c));
        });
    }

    @PluginMethod
    public void setVolume(PluginCall call) {
        float volume = call.getFloat("volume", 1f);
        withController((c) -> {
            if (c != null) c.setVolume(Math.max(0f, Math.min(1f, volume)));
            call.resolve(state(c));
        });
    }

    @PluginMethod
    public void getState(PluginCall call) {
        withController((c) -> call.resolve(state(c)));
    }

    /**
     * The status and navigation bars as the page's theme has them ({dark, color}): light
     * or dark icons, and its colour behind them where the page doesn't draw under them.
     */
    @PluginMethod
    public void setSystemBars(PluginCall call) {
        boolean dark = call.getBoolean("dark", false);
        String color = call.getString("color");
        main.post(() -> {
            Window window = getActivity().getWindow();
            WindowInsetsControllerCompat bars = WindowCompat.getInsetsController(window, window.getDecorView());
            bars.setAppearanceLightStatusBars(!dark);
            bars.setAppearanceLightNavigationBars(!dark);
            if (color != null) {
                try {
                    window.getDecorView().setBackgroundColor(Color.parseColor(color));
                } catch (IllegalArgumentException ignored) {}
            }
            call.resolve();
        });
    }

    // Downloads (offline.js): a piece at a time, so a pause takes effect between pieces.

    private static JSObject entry(AudioFiles.Entry e) throws JSONException {
        return JSObject.fromJSONObject(e.toJson());
    }

    /** Fetch piece k ({url, size, piece, k}) of a recording into its file. */
    @PluginMethod
    public void fetchPiece(PluginCall call) {
        // (Numbers come as ints or longs, as they're small or not: getLong takes only longs.)
        String url = call.getString("url");
        long size = call.getData().optLong("size", -1), piece = call.getData().optLong("piece", -1);
        int k = call.getData().optInt("k", -1);
        if (url == null || size <= 0 || piece <= 0 || k < 0) {
            call.reject("url, size, piece and k are wanted");
            return;
        }
        downloads.execute(() -> {
            try {
                call.resolve(entry(files.fetchPiece(url, size, piece, k)));
            } catch (Exception e) {
                call.reject(e.getMessage() != null ? e.getMessage() : e.toString());
            }
        });
    }

    /** Mark a recording ({url}) downloaded, once all its pieces are there. */
    @PluginMethod
    public void finishDownload(PluginCall call) {
        String url = call.getString("url");
        downloads.execute(() -> {
            try {
                call.resolve(entry(files.finish(url)));
            } catch (Exception e) {
                call.reject(e.getMessage() != null ? e.getMessage() : e.toString());
            }
        });
    }

    /** The recordings kept, whole or in part: {entries: [{url, size, piece, type, have, done}]}. */
    @PluginMethod
    public void listDownloads(PluginCall call) {
        downloads.execute(() -> {
            try {
                JSArray out = new JSArray();
                for (AudioFiles.Entry e : files.list()) out.put(entry(e));
                call.resolve(new JSObject().put("entries", out));
            } catch (Exception e) {
                call.reject(e.toString());
            }
        });
    }

    @PluginMethod
    public void removeDownload(PluginCall call) {
        String url = call.getString("url");
        downloads.execute(() -> {
            files.remove(url);
            call.resolve();
        });
    }
}
