package io.github.jonathanwxyz.reader;

import android.app.PendingIntent;
import android.content.Intent;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.media3.common.AudioAttributes;
import androidx.media3.common.C;
import androidx.media3.common.ForwardingSimpleBasePlayer;
import androidx.media3.common.Player;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.session.MediaSession;
import androidx.media3.session.MediaSessionService;
import com.google.common.util.concurrent.Futures;
import com.google.common.util.concurrent.ListenableFuture;

/**
 * The recording being read, played here rather than in the page: so it goes on with the
 * screen off and from one page of the reader to the next, with the media notification and
 * lock-screen controls. The reader drives it through ReaderAudioPlugin.
 */
@UnstableApi
public class PlaybackService extends MediaSessionService {

    /** Told of the notification's (and a headset's) track buttons: the reader steps a verse. */
    interface ActionListener {
        void onAction(String action);
    }

    static volatile ActionListener actions;

    private MediaSession session;

    @Override
    public void onCreate() {
        super.onCreate();
        ExoPlayer exo = new ExoPlayer.Builder(this)
            .setAudioAttributes(
                new AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_SPEECH).build(),
                true // (pauses for a call, or another app's audio)
            )
            .setHandleAudioBecomingNoisy(true) // (headphones unplugged: pause)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .build();
        Intent open = new Intent(this, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent tap = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        session = new MediaSession.Builder(this, new ReaderPlayer(exo)).setSessionActivity(tap).build();
    }

    @Nullable
    @Override
    public MediaSession onGetSession(@NonNull MediaSession.ControllerInfo controllerInfo) {
        return session;
    }

    // The app swiped away: the reader that was driving the recording is gone too.
    @Override
    public void onTaskRemoved(@Nullable Intent rootIntent) {
        pauseAllPlayersAndStopSelf();
    }

    @Override
    public void onDestroy() {
        if (session != null) {
            session.getPlayer().release();
            session.release();
            session = null;
        }
        super.onDestroy();
    }

    /**
     * The player as the notification sees it: its track buttons step a verse (the reader
     * does it), rather than move between recordings (there is one).
     */
    private static class ReaderPlayer extends ForwardingSimpleBasePlayer {

        ReaderPlayer(Player player) {
            super(player);
        }

        @Override
        protected State getState() {
            State state = super.getState();
            Player.Commands commands = state.availableCommands
                .buildUpon()
                .addAll(Player.COMMAND_SEEK_TO_NEXT, Player.COMMAND_SEEK_TO_PREVIOUS)
                .build();
            return state.buildUpon().setAvailableCommands(commands).build();
        }

        @Override
        protected ListenableFuture<?> handleSeek(int mediaItemIndex, long positionMs, @Player.Command int seekCommand) {
            String action = switch (seekCommand) {
                case Player.COMMAND_SEEK_TO_NEXT, Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM -> "nexttrack";
                case Player.COMMAND_SEEK_TO_PREVIOUS, Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM -> "previoustrack";
                default -> null;
            };
            if (action == null) return super.handleSeek(mediaItemIndex, positionMs, seekCommand);
            ActionListener listener = actions;
            if (listener != null) listener.onAction(action);
            return Futures.immediateVoidFuture();
        }
    }
}
