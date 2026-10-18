package io.github.jonathanwxyz.reader;

import android.content.Context;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

/**
 * The reader's fonts, every one of them, come with the app (assets/fonts/, copied there by
 * the build from web/fonts/: app/build.gradle): the pages' /fonts/… are answered from
 * there, so a font is there at once, offline too, whichever is chosen. A font the app
 * doesn't have (one added to the site since) goes on to the server, as in a browser.
 * (Fonts are never changed under the same name: web/sw.js.)
 */
class BundledFonts {

    private static final String DIR = "fonts/";

    static WebResourceResponse intercept(Context context, WebResourceRequest request) {
        String path = request.getUrl().getPath();
        if (path == null || !path.startsWith("/fonts/")) return null;
        String name = path.substring("/fonts/".length());
        if (!name.matches("[A-Za-z0-9._-]+\\.woff2")) return null;
        try {
            InputStream in = context.getAssets().open(DIR + name);
            Map<String, String> headers = new HashMap<>();
            headers.put("Cache-Control", "public, max-age=31536000, immutable");
            headers.put("Access-Control-Allow-Origin", "*");  // (fetched as CORS: the pages' preloads)
            return new WebResourceResponse("font/woff2", null, 200, "OK", headers, in);
        } catch (IOException e) {
            return null;  // (not among the app's)
        }
    }
}
