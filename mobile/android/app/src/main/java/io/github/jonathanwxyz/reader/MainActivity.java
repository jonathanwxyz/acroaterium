package io.github.jonathanwxyz.reader;

import android.os.Bundle;
import android.view.View;
import android.webkit.ServiceWorkerClient;
import android.webkit.ServiceWorkerController;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // (Before the bridge starts, so the pages see it.)
        registerPlugin(ReaderAudioPlugin.class);
        super.onCreate(savedInstanceState);
        // (The pages draw their own, between the reader's bars: web/native.js.)
        getBridge().getWebView().setVerticalScrollBarEnabled(false);
        // (No stretch at the ends of a page.)
        getBridge().getWebView().setOverScrollMode(View.OVER_SCROLL_NEVER);
        serveBundledLibrary();
    }

    /**
     * The library the app comes with (BundledLibrary), at /__app/… on the pages' own
     * server: answered here, for the pages and for their service worker alike; anything
     * else goes on as Capacitor has it.
     */
    private void serveBundledLibrary() {
        Bridge bridge = getBridge();
        bridge.setWebViewClient(
            new BridgeWebViewClient(bridge) {
                @Override
                public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                    WebResourceResponse r = BundledLibrary.intercept(MainActivity.this, request);
                    return r != null ? r : super.shouldInterceptRequest(view, request);
                }
            }
        );
        boolean resolve = bridge.getConfig().isResolveServiceWorkerRequests();
        ServiceWorkerController.getInstance()
            .setServiceWorkerClient(
                new ServiceWorkerClient() {
                    @Override
                    public WebResourceResponse shouldInterceptRequest(WebResourceRequest request) {
                        WebResourceResponse r = BundledLibrary.intercept(MainActivity.this, request);
                        if (r != null) return r;
                        return resolve ? bridge.getLocalServer().shouldInterceptRequest(request) : null;
                    }
                }
            );
    }
}
