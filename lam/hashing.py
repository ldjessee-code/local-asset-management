# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact-duplicate hashing: size bucket → 64 KB prefix → full SHA-256.

Unique sizes are never read. Results cache in SQLite by (dev, inode, size, mtime).
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PREFIX = 64 * 1024
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
EMPTY_PREFIX = EMPTY_SHA256


def sha256_prefix(path: Path, n: int = PREFIX) -> str:
    with path.open("rb") as fh:
        return hashlib.sha256(fh.read(n)).hexdigest()


def sha256_full(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _cache_key(dev: int | None, inode: int | None, size: int, mtime: float) -> tuple:
    return (int(dev or 0), int(inode or 0), int(size), float(mtime))


def load_cache(conn: sqlite3.Connection) -> dict[tuple, tuple[str, str | None]]:
    out: dict[tuple, tuple[str, str | None]] = {}
    for row in conn.execute(
        "SELECT dev, inode, size, mtime, sha256, prefix64 FROM hash_cache"
    ):
        out[_cache_key(row["dev"], row["inode"], row["size"], row["mtime"])] = (
            row["sha256"],
            row["prefix64"],
        )
    return out


def store_cache(
    conn: sqlite3.Connection,
    dev: int | None,
    inode: int | None,
    size: int,
    mtime: float,
    sha256: str,
    prefix64: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO hash_cache(dev, inode, size, mtime, sha256, prefix64)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(dev, inode, size, mtime) DO UPDATE SET
          sha256=excluded.sha256, prefix64=excluded.prefix64
        """,
        (int(dev or 0), int(inode or 0), int(size), float(mtime), sha256, prefix64),
    )


def hash_size_collisions(
    conn: sqlite3.Connection,
    *,
    workers: int = 4,
    progress: Callable[[str, dict], None] | None = None,
) -> None:
    """Size → 64 KB prefix → full SHA-256, only for colliding buckets."""
    cache = load_cache(conn)
    rows = list(
        conn.execute(
            "SELECT id, path, size, mtime, dev, inode FROM files"
        )
    )
    by_size: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_size[int(row["size"])].append(row)

    empty_ids = [int(r["id"]) for r in by_size.get(0, [])]
    if empty_ids:
        conn.executemany(
            "UPDATE files SET sha256=?, prefix64=? WHERE id=?",
            [(EMPTY_SHA256, EMPTY_PREFIX, i) for i in empty_ids],
        )

    to_prefix: list[sqlite3.Row] = []
    for size, group in by_size.items():
        if size == 0:
            continue
        if len(group) < 2:
            continue
        to_prefix.extend(group)

    if progress:
        progress("hash", {"phase": "prefix", "candidates": len(to_prefix)})

    prefix_of: dict[int, str] = {}
    need_prefix_read: list[sqlite3.Row] = []
    inode_prefix: dict[tuple[int, int], str] = {}

    for row in to_prefix:
        key = _cache_key(row["dev"], row["inode"], row["size"], row["mtime"])
        cached = cache.get(key)
        inode_k = (int(row["dev"] or 0), int(row["inode"] or 0))
        if cached:
            sha, pref = cached
            prefix_of[int(row["id"])] = pref or sha
            conn.execute(
                "UPDATE files SET sha256=?, prefix64=? WHERE id=?",
                (sha, pref or sha, int(row["id"])),
            )
            continue
        if inode_k in inode_prefix:
            prefix_of[int(row["id"])] = inode_prefix[inode_k]
            continue
        need_prefix_read.append(row)

    def _prefix_job(row: sqlite3.Row) -> tuple[int, str | None, str | None]:
        try:
            return int(row["id"]), sha256_prefix(Path(row["path"])), None
        except OSError as exc:
            return int(row["id"]), None, str(exc)

    if need_prefix_read:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for file_id, digest, err in pool.map(_prefix_job, need_prefix_read, chunksize=1):
                if err or not digest:
                    conn.execute(
                        "INSERT INTO scan_errors(path, error) VALUES(?, ?)",
                        ("id:" + str(file_id), err or "prefix hash failed"),
                    )
                    continue
                prefix_of[file_id] = digest

        by_id = {int(r["id"]): r for r in need_prefix_read}
        for file_id, digest in prefix_of.items():
            row = by_id.get(file_id)
            if row is None:
                continue
            conn.execute(
                "UPDATE files SET prefix64=? WHERE id=?",
                (digest, file_id),
            )
            inode_prefix[(int(row["dev"] or 0), int(row["inode"] or 0))] = digest

    by_prefix: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
    row_by_id = {int(r["id"]): r for r in to_prefix}
    for file_id, digest in prefix_of.items():
        row = row_by_id[file_id]
        # skip rows already fully hashed from cache
        existing = conn.execute(
            "SELECT sha256 FROM files WHERE id=?", (file_id,)
        ).fetchone()
        if existing and existing["sha256"]:
            continue
        by_prefix[(int(row["size"]), digest)].append(row)

    need_full: list[sqlite3.Row] = []
    for group in by_prefix.values():
        if len(group) < 2:
            continue
        need_full.extend(group)

    if progress:
        progress("hash", {"phase": "full", "candidates": len(need_full)})

    def _full_job(row: sqlite3.Row) -> tuple[int, str | None, str | None]:
        try:
            return int(row["id"]), sha256_full(Path(row["path"])), None
        except OSError as exc:
            return int(row["id"]), None, str(exc)

    if need_full:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for file_id, digest, err in pool.map(_full_job, need_full, chunksize=1):
                row = row_by_id[file_id]
                if err or not digest:
                    conn.execute(
                        "INSERT INTO scan_errors(path, error) VALUES(?, ?)",
                        (row["path"], err or "full hash failed"),
                    )
                    continue
                pref = prefix_of.get(file_id)
                conn.execute(
                    "UPDATE files SET sha256=?, prefix64=COALESCE(prefix64, ?) WHERE id=?",
                    (digest, pref, file_id),
                )
                store_cache(
                    conn,
                    row["dev"],
                    row["inode"],
                    row["size"],
                    row["mtime"],
                    digest,
                    pref,
                )

    conn.commit()
