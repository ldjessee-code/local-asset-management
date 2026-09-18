# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite schema and connection helpers. Hash cache survives a rescan; file rows do not."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  layout TEXT NOT NULL,
  priority INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  ext TEXT NOT NULL,
  sha256 TEXT,
  prefix64 TEXT,
  crc32 INTEGER,
  group_name TEXT,
  bundle TEXT,
  pack TEXT,
  relpath TEXT NOT NULL,
  relpath_from_pack TEXT,
  year TEXT,
  month TEXT,
  stem TEXT,
  zip_name TEXT,
  dev INTEGER,
  inode INTEGER,
  is_archive INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS archives (
  id INTEGER PRIMARY KEY,
  file_id INTEGER REFERENCES files(id),
  path TEXT NOT NULL,
  parent_archive_id INTEGER REFERENCES archives(id),
  size INTEGER,
  sha256 TEXT
);

CREATE TABLE IF NOT EXISTS archive_entries (
  id INTEGER PRIMARY KEY,
  archive_id INTEGER NOT NULL REFERENCES archives(id),
  inner_path TEXT NOT NULL,
  size INTEGER,
  crc32 INTEGER,
  sha256 TEXT
);

CREATE TABLE IF NOT EXISTS sidecars (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  parent_file_id INTEGER REFERENCES files(id),
  bundle TEXT
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL,
  canonical_file_id INTEGER REFERENCES files(id),
  extra_count INTEGER NOT NULL,
  wasted_bytes INTEGER NOT NULL,
  policy TEXT
);

CREATE TABLE IF NOT EXISTS hash_cache (
  dev INTEGER NOT NULL,
  inode INTEGER NOT NULL,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  sha256 TEXT NOT NULL,
  prefix64 TEXT,
  PRIMARY KEY (dev, inode, size, mtime)
);

CREATE TABLE IF NOT EXISTS scan_errors (
  id INTEGER PRIMARY KEY,
  path TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS plan_rows (
  id INTEGER PRIMARY KEY,
  file_id INTEGER,
  src TEXT NOT NULL,
  dest TEXT,
  op TEXT NOT NULL,
  reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  command TEXT NOT NULL,
  dry_run INTEGER NOT NULL,
  src TEXT,
  dest TEXT,
  op TEXT,
  reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_source ON files(source_id);
CREATE INDEX IF NOT EXISTS idx_entries_crc ON archive_entries(size, crc32);
"""

SCAN_TABLES = (
    "plan_rows",
    "duplicate_groups",
    "sidecars",
    "archive_entries",
    "archives",
    "files",
    "scan_errors",
    "sources",
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def reset_scan_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    for table in SCAN_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("PRAGMA foreign_keys=ON")


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row["value"])
