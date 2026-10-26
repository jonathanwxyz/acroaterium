package io.github.jonathanwxyz.reader;

import android.content.Context;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Downloaded recordings, kept as the app's own files (the web reader keeps them in the
 * browser's caches, which the native player can't read). As there, a recording is fetched
 * in pieces by byte range, so a download cut short goes on where it stopped: the pieces
 * are written into place in one file, <name>.data, and <name>.json records which are
 * there. Once all are, the file is the recording, and the player plays it from here.
 */
class AudioFiles {

    /** What is kept of one recording. */
    static class Entry {
        String url;
        long size;
        long piece; // bytes per piece
        String type = "";
        TreeSet<Integer> pieces = new TreeSet<>();
        boolean done;

        int count() {
            return (int) ((size + piece - 1) / piece);
        }

        JSONObject toJson() throws JSONException {
            JSONArray have = new JSONArray();
            for (int k : pieces) have.put(k);
            return new JSONObject()
                .put("url", url)
                .put("size", size)
                .put("piece", piece)
                .put("type", type)
                .put("have", have)
                .put("done", done);
        }

        static Entry fromJson(JSONObject o) throws JSONException {
            Entry e = new Entry();
            e.url = o.getString("url");
            e.size = o.getLong("size");
            e.piece = o.getLong("piece");
            e.type = o.optString("type", "");
            JSONArray have = o.getJSONArray("have");
            for (int i = 0; i < have.length(); i++) e.pieces.add(have.getInt(i));
            e.done = o.optBoolean("done");
            return e;
        }
    }

    private final File dir;

    AudioFiles(Context context) {
        dir = new File(context.getFilesDir(), "audio");
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
    }

    private static String name(String url) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-1").digest(url.getBytes(StandardCharsets.UTF_8));
            StringBuilder s = new StringBuilder();
            for (byte b : hash) s.append(String.format("%02x", b));
            return s.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private File data(String url) {
        return new File(dir, name(url) + ".data");
    }

    private File record(String url) {
        return new File(dir, name(url) + ".json");
    }

    synchronized Entry get(String url) {
        File f = record(url);
        if (!f.exists()) return null;
        try {
            return Entry.fromJson(new JSONObject(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8)));
        } catch (IOException | JSONException e) {
            return null;
        }
    }

    private synchronized void save(Entry e) throws IOException {
        File tmp = new File(dir, name(e.url) + ".json.tmp");
        try (FileOutputStream out = new FileOutputStream(tmp)) {
            out.write(e.toJson().toString().getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        } catch (JSONException ex) {
            throw new IOException(ex);
        }
        if (!tmp.renameTo(record(e.url))) throw new IOException("could not save the download's record");
    }

    synchronized List<Entry> list() {
        List<Entry> out = new ArrayList<>();
        File[] files = dir.listFiles((d, n) -> n.endsWith(".json"));
        if (files == null) return out;
        for (File f : files) {
            try {
                out.add(Entry.fromJson(new JSONObject(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8))));
            } catch (IOException | JSONException ignored) {}
        }
        return out;
    }

    /** The recording's file, if it has all been downloaded. */
    File complete(String url) {
        Entry e = get(url);
        File f = data(url);
        return e != null && e.done && f.length() == e.size ? f : null;
    }

    /**
     * Fetch piece k of the recording at url (size bytes, in pieces of piece bytes) and write
     * it into place. A recording that has changed size since it was begun starts again.
     */
    Entry fetchPiece(String url, long size, long piece, int k) throws IOException {
        Entry e = get(url);
        if (e == null || e.size != size || e.piece != piece) {
            remove(url);
            e = new Entry();
            e.url = url;
            e.size = size;
            e.piece = piece;
            save(e);
        }
        if (e.pieces.contains(k)) return e;
        long from = k * piece, to = Math.min(size, from + piece) - 1;
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(30_000);
        conn.setReadTimeout(60_000);
        conn.setRequestProperty("Range", "bytes=" + from + "-" + to);
        try {
            int status = conn.getResponseCode();
            if (status != 206 && !(status == 200 && from == 0 && to == size - 1)) throw new IOException("HTTP " + status);
            String type = conn.getContentType();
            try (InputStream in = conn.getInputStream(); RandomAccessFile out = new RandomAccessFile(data(url), "rw")) {
                out.seek(from);
                byte[] buf = new byte[64 * 1024];
                long want = to - from + 1, got = 0;
                for (int n; got < want && (n = in.read(buf, 0, (int) Math.min(buf.length, want - got))) > 0; got += n) {
                    out.write(buf, 0, n);
                }
                if (got != want) throw new IOException("the server sent the wrong part of the recording");
                out.getFD().sync();
            }
            if (e.type.isEmpty() && type != null) e.type = type.split(";")[0].trim();
        } finally {
            conn.disconnect();
        }
        e.pieces.add(k);
        save(e);
        return e;
    }

    /** Mark the recording whole, if every piece is there. */
    Entry finish(String url) throws IOException {
        Entry e = get(url);
        if (e == null) throw new IOException("no such download");
        if (e.pieces.size() < e.count() || data(url).length() != e.size) throw new IOException("a piece of the recording is missing");
        e.done = true;
        save(e);
        return e;
    }

    synchronized void remove(String url) {
        //noinspection ResultOfMethodCallIgnored
        data(url).delete();
        //noinspection ResultOfMethodCallIgnored
        record(url).delete();
    }

    /** Remove every recording not from the given origin (scheme://host[:port]). */
    synchronized void removeNotFrom(String origin) {
        for (Entry e : list()) {
            if (!origin.equals(MainActivity.origin(e.url))) remove(e.url);
        }
    }
}
