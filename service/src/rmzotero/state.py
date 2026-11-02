"""The service's own durable state (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS zobj (key TEXT PRIMARY KEY, kind TEXT, version INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS docs (
  att_key TEXT PRIMARY KEY,
  doc_id TEXT UNIQUE,
  item_key TEXT,
  kind TEXT,
  state TEXT,
  folder_key TEXT,
  name TEXT,
  zotero_md5 TEXT,
  zotero_file_hash TEXT,
  tablet_pdf_hash TEXT,
  tablet_doc_hash TEXT,
  pages TEXT,
  page_hashes TEXT,
  size INTEGER DEFAULT 0,
  error TEXT,
  zotero_changed_at REAL DEFAULT 0,
  tablet_changed_at REAL DEFAULT 0,
  last_synced REAL DEFAULT 0,
  last_opened TEXT,
  pinned INTEGER DEFAULT 0,
  zlib_version INTEGER DEFAULT 0,
  tablet_seen_hash TEXT,
  zotero_seen_sig INTEGER DEFAULT 0,
  tablet_content_hash TEXT,
  placeholder_doc_id TEXT,
  restore_requested INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS links (
  zkey TEXT PRIMARY KEY, att_key TEXT, origin TEXT, page_id TEXT, tablet_ids TEXT,
  zversion INTEGER, zfp TEXT, tfp TEXT, unsynced INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS links_att ON links(att_key);
CREATE TABLE IF NOT EXISTS pointers (att_key TEXT, collection_key TEXT, doc_id TEXT, suppressed INTEGER DEFAULT 0, location TEXT, name TEXT, PRIMARY KEY (att_key, collection_key));
CREATE TABLE IF NOT EXISTS folders (fkey TEXT PRIMARY KEY, doc_id TEXT);
CREATE TABLE IF NOT EXISTS home_overrides (item_key TEXT PRIMARY KEY, collection_key TEXT);
CREATE TABLE IF NOT EXISTS derived (source_key TEXT, kind TEXT, derived_key TEXT, params TEXT, PRIMARY KEY (source_key, kind));
CREATE TABLE IF NOT EXISTS typography (att_key TEXT PRIMARY KEY, params TEXT);
CREATE TABLE IF NOT EXISTS cycles (id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, finished REAL, report TEXT);
CREATE TABLE IF NOT EXISTS failures (
  key TEXT, kind TEXT, fingerprint TEXT, attempts INTEGER, first_ts REAL, last_ts REAL, next_retry REAL,
  message TEXT, traceback TEXT, PRIMARY KEY (key, kind)
);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, att_key TEXT, message TEXT);
"""


class State:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    # -- kv ------------------------------------------------------------------------

    def get(self, k: str, default: Any = None) -> Any:
        with self.lock:
            row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set(self, k: str, v: Any) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, json.dumps(v)))

    # -- generic helpers -------------------------------------------------------------

    def all(self, sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.db.execute(sql, tuple(args)).fetchone()

    def exec(self, sql: str, args: Iterable = ()) -> None:
        with self.lock, self.db:
            self.db.execute(sql, tuple(args))

    def upsert(self, table: str, row: dict, key: str | tuple[str, ...]) -> None:
        cols = list(row)
        keys = (key,) if isinstance(key, str) else key
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) ON CONFLICT({', '.join(keys)}) DO "
        sql += f"UPDATE SET {updates}" if updates else "NOTHING"
        with self.lock, self.db:
            self.db.execute(sql, [row[c] for c in cols])

    # -- Zotero mirror -------------------------------------------------------------------

    def zobjects(self) -> list[sqlite3.Row]:
        return self.all("SELECT key, kind, version, data FROM zobj")

    def save_zobjects(self, objects: list[tuple[str, str, int, dict]], deleted: list[str]) -> None:
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO zobj(key, kind, version, data) VALUES (?, ?, ?, ?)",
                                [(k, kind, v, json.dumps(d)) for k, kind, v, d in objects])
            self.db.executemany("DELETE FROM zobj WHERE key=?", [(k,) for k in deleted])

    # -- documents ------------------------------------------------------------------------

    def doc(self, att_key: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM docs WHERE att_key=?", (att_key,))

    def doc_by_id(self, doc_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM docs WHERE doc_id=?", (doc_id,))

    def docs(self, state: str | None = None) -> list[sqlite3.Row]:
        if state:
            return self.all("SELECT * FROM docs WHERE state=?", (state,))
        return self.all("SELECT * FROM docs")

    def update_doc(self, att_key: str, **fields) -> None:
        fields["att_key"] = att_key
        for k in ("pages", "page_hashes"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k])
        self.upsert("docs", fields, "att_key")

    # -- links -------------------------------------------------------------------------------

    def links_for(self, att_key: str) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM links WHERE att_key=?", (att_key,))

    def replace_links(self, att_key: str, rows: list[dict]) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM links WHERE att_key=?", (att_key,))
            for r in rows:
                r = {**r, "att_key": att_key}
                cols = list(r)
                self.db.execute(f"INSERT OR REPLACE INTO links ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [r[c] for c in cols])

    # -- events (warnings shown in the plugin) --------------------------------------------------

    def event(self, level: str, message: str, att_key: str | None = None) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(ts, level, att_key, message) VALUES (?, ?, ?, ?)", (time.time(), level, att_key, message))
            self.db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)")

    def events(self, att_keys: list[str] | None = None, limit: int = 50) -> list[dict]:
        if att_keys:
            q = ",".join("?" for _ in att_keys)
            rows = self.all(f"SELECT * FROM events WHERE att_key IN ({q}) ORDER BY id DESC LIMIT ?", [*att_keys, limit])
        else:
            rows = self.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
