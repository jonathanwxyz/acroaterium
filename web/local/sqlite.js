// A small reader for SQLite files, read-only: as much of the file format
// (https://www.sqlite.org/fileformat.html) as looking things up in the compact library
// takes (data/library_compact.sqlite: tables, their indexes, WITHOUT ROWID tables, records
// that run over onto overflow pages). It reads the file a block at a time, as a lookup
// needs it, from wherever the file is kept (readAt(offset, length) -> Uint8Array), so the
// file is never loaded whole. A few blocks are kept, the last used.
//
// Used by the service worker (sw.js, the file kept in the browser's cache) and, to check
// it against the server, by Node.
(function (root) {
  const BLOCK = 64 << 10;  // bytes read at a time (a whole number of pages)
  const KEEP = 96;  // blocks kept (6 MB)
  const utf8 = new TextDecoder();

  function varint(b, p) {
    let v = 0;
    for (let i = 0; i < 8; i++) {
      const c = b[p++];
      v = v * 128 + (c & 0x7f);
      if (c < 0x80) return [v, p];
    }
    return [v * 256 + b[p++], p];
  }
  const u16 = (b, p) => (b[p] << 8) | b[p + 1];
  const u32 = (b, p) => ((b[p] << 24) >>> 0) + (b[p + 1] << 16) + (b[p + 2] << 8) + b[p + 3];

  // A record's values (its header, then each value by its serial type).
  function record(b) {
    const [size, start] = varint(b, 0);
    const types = [];
    for (let p = start; p < size;) {
      let t;
      [t, p] = varint(b, p);
      types.push(t);
    }
    const out = [];
    const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
    let q = size;
    for (const t of types) {
      if (t === 0) out.push(null);
      else if (t === 8) out.push(0);
      else if (t === 9) out.push(1);
      else if (t === 7) { out.push(view.getFloat64(q)); q += 8; }
      else if (t <= 6) {
        const n = [0, 1, 2, 3, 4, 6, 8][t];
        if (t === 6) out.push(Number(view.getBigInt64(q)));
        else {
          let v = 0;
          for (let i = 0; i < n; i++) v = v * 256 + b[q + i];
          if (b[q] & 0x80) v -= 2 ** (8 * n);  // (two's complement)
          out.push(v);
        }
        q += n;
      } else if (t >= 12) {
        const n = (t - (t & 1 ? 13 : 12)) / 2;
        out.push(t & 1 ? utf8.decode(b.subarray(q, q + n)) : b.subarray(q, q + n));
        q += n;
      } else throw new Error(`serial type ${t}`);
    }
    return out;
  }

  // SQLite's order for an index's keys: NULL, then numbers, then text (by its bytes: for
  // UTF-8, its code points), then blobs.
  const rank = (v) => (v == null ? 0 : typeof v === "number" ? 1 : typeof v === "string" ? 2 : 3);
  function compareValue(a, b) {
    const ra = rank(a), rb = rank(b);
    if (ra !== rb) return ra - rb;
    if (ra === 1) return a - b;
    if (ra === 2) {
      for (let i = 0, j = 0; i < a.length && j < b.length;) {
        const ca = a.codePointAt(i), cb = b.codePointAt(j);
        if (ca !== cb) return ca - cb;
        i += ca > 0xffff ? 2 : 1;
        j += cb > 0xffff ? 2 : 1;
      }
      return a.length - b.length;  // (one a prefix of the other: the shorter first)
    }
    return 0;
  }
  // A record against a key of its first columns.
  function comparePrefix(rec, key) {
    for (let i = 0; i < key.length; i++) {
      const c = compareValue(rec[i], key[i]);
      if (c) return c;
    }
    return 0;
  }

  class SqliteFile {
    constructor(readAt, size) {
      this.readAt = readAt;
      this.size = size;
      this.blocks = new Map();  // block number -> Promise<Uint8Array>
    }

    static async open(readAt, size) {
      const f = new SqliteFile(readAt, size);
      const h = await f.readAt(0, 100);
      if (utf8.decode(h.subarray(0, 15)) !== "SQLite format 3") throw new Error("not an SQLite file");
      f.pageSize = u16(h, 16) === 1 ? 65536 : u16(h, 16);
      f.usable = f.pageSize - h[20];
      if (u32(h, 56) > 1) throw new Error("not UTF-8");
      f.tables = {};
      for await (const [, r] of f.scan(1)) {
        const [type, name, , rootpage, sql] = r;
        f.tables[name] = { type, root: rootpage, sql };
      }
      return f;
    }

    block(k) {
      let b = this.blocks.get(k);
      if (b) {
        this.blocks.delete(k);  // (to the end: the last used)
      } else {
        const at = k * BLOCK;
        b = this.readAt(at, Math.min(BLOCK, this.size - at));
        b.catch(() => this.blocks.delete(k));
      }
      this.blocks.set(k, b);
      if (this.blocks.size > KEEP) this.blocks.delete(this.blocks.keys().next().value);
      return b;
    }

    async page(n) {
      const at = (n - 1) * this.pageSize, k = Math.floor(at / BLOCK);
      const b = await this.block(k);
      return b.subarray(at - k * BLOCK, at - k * BLOCK + this.pageSize);
    }

    // A cell's payload, with what ran over onto overflow pages.
    async payload(pg, p, total, index) {
      const U = this.usable;
      const X = index ? Math.floor(((U - 12) * 64) / 255) - 23 : U - 35;
      let local = total;
      if (total > X) {
        const M = Math.floor(((U - 12) * 32) / 255) - 23;
        const K = M + ((total - M) % (U - 4));
        local = K <= X ? K : M;
      }
      if (local === total) return pg.subarray(p, p + total);
      const out = new Uint8Array(total);
      out.set(pg.subarray(p, p + local));
      let got = local, next = u32(pg, p + local);
      while (got < total) {
        const op = await this.page(next);
        const n = Math.min(U - 4, total - got);
        out.set(op.subarray(4, 4 + n), got);
        got += n;
        next = u32(op, 0);
      }
      return out;
    }

    // A b-tree page's header and its cells' offsets.
    async node(n) {
      const pg = await this.page(n);
      const h = n === 1 ? 100 : 0;
      const type = pg[h], count = u16(pg, h + 3), leaf = type === 10 || type === 13;
      const cells = [];
      const first = h + (leaf ? 8 : 12);
      for (let i = 0; i < count; i++) cells.push(u16(pg, first + 2 * i));
      return { pg, type, leaf, cells, right: leaf ? 0 : u32(pg, h + 8) };
    }

    // A table's rows in rowid order: [rowid, values].
    async *scan(root) {
      const { pg, type, leaf, cells, right } = await this.node(root);
      if (type !== 13 && type !== 5) throw new Error(`page ${root} is not a table's`);
      for (const c of cells) {
        if (leaf) {
          const [total, p] = varint(pg, c);
          const [rowid, q] = varint(pg, p);
          yield [rowid, record(await this.payload(pg, q, total, false))];
        } else {
          yield* this.scan(u32(pg, c));
        }
      }
      if (!leaf) yield* this.scan(right);
    }

    // A table's row by its rowid (null: none).
    async row(root, rowid) {
      for (let n = root; ;) {
        const { pg, leaf, cells, right } = await this.node(n);
        if (leaf) {
          let lo = 0, hi = cells.length - 1;
          while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const [total, p] = varint(pg, cells[mid]);
            const [id, q] = varint(pg, p);
            if (id === rowid) return record(await this.payload(pg, q, total, false));
            if (id < rowid) lo = mid + 1; else hi = mid - 1;
          }
          return null;
        }
        // The first cell whose key is at least the rowid: its left child (else the rightmost).
        let lo = 0, hi = cells.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (varint(pg, cells[mid] + 4)[0] < rowid) lo = mid + 1; else hi = mid;
        }
        n = lo < cells.length ? u32(pg, cells[lo]) : right;
      }
    }

    async indexKey(pg, c, leaf) {
      const [total, p] = varint(pg, leaf ? c : c + 4);
      return record(await this.payload(pg, p, total, true));
    }

    // In an index (or a WITHOUT ROWID table), the first entry at or after a key of its
    // first columns (null: none).
    async seek(root, key) {
      const { pg, leaf, cells, right } = await this.node(root);
      let lo = 0, hi = cells.length;
      const keys = new Map();
      const at = async (i) => {
        if (!keys.has(i)) keys.set(i, await this.indexKey(pg, cells[i], leaf));
        return keys.get(i);
      };
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (comparePrefix(await at(mid), key) < 0) lo = mid + 1; else hi = mid;
      }
      if (leaf) return lo < cells.length ? at(lo) : null;
      // (An interior cell is an entry too: anything in the subtree before it comes first.)
      const below = await this.seek(lo < cells.length ? u32(pg, cells[lo]) : right, key);
      return below ?? (lo < cells.length ? at(lo) : null);
    }

    // The entry whose first columns are the key exactly (null: none).
    async find(root, key) {
      const r = await this.seek(root, key);
      return r && comparePrefix(r, key) === 0 ? r : null;
    }

    // A table's columns, by name, from its CREATE statement; and which one, if any, is
    // the rowid itself (INTEGER PRIMARY KEY).
    columns(table) {
      const sql = this.tables[table].sql.replace(/--[^\n]*/g, "");
      const body = sql.slice(sql.indexOf("(") + 1, sql.lastIndexOf(")"));
      const defs = [];
      let depth = 0, cur = "";
      for (const ch of body) {
        if (ch === "(") depth++;
        if (ch === ")") depth--;
        if (ch === "," && depth === 0) { defs.push(cur); cur = ""; } else cur += ch;
      }
      defs.push(cur);
      const names = [];
      let alias = -1;
      for (const d of defs.map((s) => s.trim())) {
        if (/^(PRIMARY|UNIQUE|CHECK|FOREIGN|CONSTRAINT)\b/i.test(d)) continue;
        const [name, type] = d.split(/\s+/);
        if (/^INTEGER$/i.test(type ?? "") && /PRIMARY\s+KEY/i.test(d)) alias = names.length;
        names.push(name.replace(/^["`[]|["`\]]$/g, ""));
      }
      return { names, alias };
    }

    // A table's rows as objects (the rowid in its alias column, where it has one).
    async all(table) {
      const { names, alias } = this.columns(table);
      const out = [];
      for await (const [rowid, values] of this.scan(this.tables[table].root)) {
        const o = {};
        names.forEach((n, i) => { o[n] = i === alias ? rowid : values[i] ?? null; });
        out.push(o);
      }
      return out;
    }

    // A table's row by its primary key: through its index (sqlite_autoindex_<table>_1), or
    // the rowid itself (INTEGER PRIMARY KEY).
    async byKey(table, key) {
      const { names, alias } = this.columns(table);
      let rowid, values;
      if (alias >= 0) {
        rowid = key[0];
        values = await this.row(this.tables[table].root, rowid);
      } else {
        const e = await this.find(this.tables[`sqlite_autoindex_${table}_1`].root, key);
        if (!e) return null;
        rowid = e[e.length - 1];
        values = await this.row(this.tables[table].root, rowid);
      }
      if (!values) return null;
      const o = {};
      names.forEach((n, i) => { o[n] = i === alias ? rowid : values[i] ?? null; });
      return o;
    }
  }

  root.SqliteFile = SqliteFile;
  if (typeof module !== "undefined") module.exports = { SqliteFile };
})(typeof self !== "undefined" ? self : globalThis);
