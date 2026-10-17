package io.github.jonathanwxyz.reader;

import android.content.Context;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

/**
 * The compact library the app comes with (assets/library/, put there by the build from
 * data/library_compact.sqlite: app/build.gradle), served to the pages at two addresses of
 * their own server's, which the app answers itself:
 *
 *   /__app/library.json            {version, size}, or 404 where the app has no copy
 *   /__app/library-compact.sqlite  the file
 *
 * web/local/manager.js keeps it as the library on the device (in the browser's caches,
 * where the service worker reads it), so books open offline from the first launch.
 */
class BundledLibrary {

    private static final String DIR = "library/";

    static WebResourceResponse intercept(Context context, WebResourceRequest request) {
        String path = request.getUrl().getPath();
        if (path == null || !path.startsWith("/__app/")) return null;
        String name;
        String type;
        if (path.equals("/__app/library.json")) {
            name = "library.json";
            type = "application/json";
        } else if (path.equals("/__app/library-compact.sqlite")) {
            name = "library-compact.sqlite";
            type = "application/vnd.sqlite3";
        } else {
            return notFound();
        }
        try {
            InputStream in = context.getAssets().open(DIR + name);
            Map<String, String> headers = new HashMap<>();
            headers.put("Cache-Control", "no-store");
            return new WebResourceResponse(type, null, 200, "OK", headers, in);
        } catch (IOException e) {
            return notFound();  // (built without the library)
        }
    }

    private static WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found", new HashMap<>(),
                                       new ByteArrayInputStream(new byte[0]));
    }
}
