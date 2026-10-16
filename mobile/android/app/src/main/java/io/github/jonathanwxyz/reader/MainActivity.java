package io.github.jonathanwxyz.reader;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // (Before the bridge starts, so the pages see it.)
        registerPlugin(ReaderAudioPlugin.class);
        super.onCreate(savedInstanceState);
        // (The pages draw their own, between the reader's bars: web/native.js.)
        getBridge().getWebView().setVerticalScrollBarEnabled(false);
    }
}
