package io.github.jonathanwxyz.reader;

import android.net.Uri;
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
import java.util.Locale;

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
        forgetOtherServers();
    }

    /**
     * The recordings another server's pages downloaded, one the app no longer opens (as
     * after the site moved to a new address, or a build for another server): their records
     * are in that server's pages' storage, so the pages of the server now opened can't see
     * them, nor remove them. (That storage itself, its caches of books and the library,
     * stays: WebView's WebStorage.deleteOrigin leaves the Cache API alone.)
     */
    private void forgetOtherServers() {
        String own = origin(getBridge().getServerUrl());
        if (own == null) return;
        new Thread(() -> new AudioFiles(this).removeNotFrom(own)).start();
    }

    /** A URL's origin, scheme://host[:port], lower case; null if it hasn't one. */
    static String origin(String url) {
        if (url == null) return null;
        Uri u = Uri.parse(url);
        if (u.getScheme() == null || u.getHost() == null) return null;
        String origin = u.getScheme() + "://" + u.getHost() + (u.getPort() != -1 ? ":" + u.getPort() : "");
        return origin.toLowerCase(Locale.ROOT);
    }

    /**
     * The library the app comes with (BundledLibrary), at /__app/… on the pages' own
     * server, and its fonts (BundledFonts), at /fonts/…: answered here, for the pages and
     * for their service worker alike; anything else goes on as Capacitor has it.
     */
    private void serveBundledLibrary() {
        Bridge bridge = getBridge();
        bridge.setWebViewClient(
            new BridgeWebViewClient(bridge) {
                @Override
                public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                    WebResourceResponse r = bundled(request);
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
                        WebResourceResponse r = bundled(request);
                        if (r != null) return r;
                        return resolve ? bridge.getLocalServer().shouldInterceptRequest(request) : null;
                    }
                }
            );
    }

    private WebResourceResponse bundled(WebResourceRequest request) {
        WebResourceResponse r = BundledLibrary.intercept(this, request);
        return r != null ? r : BundledFonts.intercept(this, request);
    }
}
