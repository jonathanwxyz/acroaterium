#!/usr/bin/env python3
"""The texts' titles, copied from data/library.sqlite into data/usage.sqlite (table titles).

    python3 scripts/usage_titles.py

The app's usage (web/shared.js -> POST /api/usage -> data/usage.sqlite) is shown by Grafana
(https://acroaterium.com/stats/, /etc/grafana/dashboards/acroaterium.json), whose SQLite
source reads that one file: its tables name the texts by these titles. Run from cron every
10 minutes, so a text added to the library is soon named there too.
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USAGE = ROOT / "data" / "usage.sqlite"
LIBRARY = ROOT / "data" / "library.sqlite"


def main():
    if not USAGE.exists() or not LIBRARY.exists():
        return
    lib = sqlite3.connect(f"file:{LIBRARY}?mode=ro", uri=True)
    try:
        names = lib.execute("SELECT slug, title FROM texts").fetchall()
    finally:
        lib.close()
    con = sqlite3.connect(USAGE, timeout=10)
    try:
        with con:
            con.execute("CREATE TABLE IF NOT EXISTS titles (slug TEXT PRIMARY KEY, title TEXT NOT NULL)")
            con.execute("DELETE FROM titles")
            con.executemany("INSERT INTO titles VALUES (?, ?)", names)
    finally:
        con.close()


if __name__ == "__main__":
    main()
