# SPDX-License-Identifier: AGPL-3.0-or-later
"""List zip (and nested zip) members without writing extracted files to disk."""

from __future__ import annotations

import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

from lam.config import LibraryConfig
from lam.util import posix

NESTED_READ_LIMIT = 50 * 1024 * 1024


def index_zip_file(
    conn: sqlite3.Connection,
    cfg: LibraryConfig,
    *,
    file_id: int,
    path: Path,
    size: int,
    sha256: str | None,
) -> None:
    cur = conn.execute(
        "INSERT INTO archives(file_id, path, parent_archive_id, size, sha256) VALUES(?,?,?,?,?)",
        (file_id, posix(path), None, size, sha256),
    )
    archive_id = int(cur.lastrowid)
    try:
        with zipfile.ZipFile(path) as zf:
            _index_zipobj(
                conn,
                cfg,
                zf,
                archive_id=archive_id,
                depth=0,
            )
    except zipfile.BadZipFile as exc:
        conn.execute(
            "INSERT INTO scan_errors(path, error) VALUES(?, ?)",
            (posix(path), f"bad zip: {exc}"),
        )


def _index_zipobj(
    conn: sqlite3.Connection,
    cfg: LibraryConfig,
    zf: zipfile.ZipFile,
    *,
    archive_id: int,
    depth: int,
) -> None:
    for info in zf.infolist():
        if info.is_dir():
            continue
        inner = info.filename.replace("\\", "/")
        crc = int(info.CRC) if info.CRC is not None else None
        conn.execute(
            "INSERT INTO archive_entries(archive_id, inner_path, size, crc32, sha256) VALUES(?,?,?,?,?)",
            (archive_id, inner, int(info.file_size), crc, None),
        )
        if (
            cfg.zip.index_nested
            and depth < cfg.zip.max_depth
            and inner.lower().endswith(".zip")
            and int(info.file_size) <= NESTED_READ_LIMIT
        ):
            try:
                data = zf.read(info)
            except Exception as exc:  # noqa: BLE001 — listing must continue
                conn.execute(
                    "INSERT INTO scan_errors(path, error) VALUES(?, ?)",
                    (inner, f"nested zip read: {exc}"),
                )
                continue
            try:
                nested = zipfile.ZipFile(BytesIO(data))
            except zipfile.BadZipFile as exc:
                conn.execute(
                    "INSERT INTO scan_errors(path, error) VALUES(?, ?)",
                    (inner, f"bad nested zip: {exc}"),
                )
                continue
            with nested:
                cur = conn.execute(
                    "INSERT INTO archives(file_id, path, parent_archive_id, size, sha256) VALUES(?,?,?,?,?)",
                    (None, inner, archive_id, int(info.file_size), None),
                )
                _index_zipobj(
                    conn,
                    cfg,
                    nested,
                    archive_id=int(cur.lastrowid),
                    depth=depth + 1,
                )
