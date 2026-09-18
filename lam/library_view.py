# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-side views for the UI: overview, browse, aligned duplicate grid."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lam.config import LibraryConfig, path_is_under
from lam.db import connect, get_meta
from lam.layouts import dest_path, tokens_for
from lam.util import posix, utc_now


def overview(cfg: LibraryConfig) -> dict:
    conn = connect(cfg.index_db)
    end = get_meta(conn, "last_scan_end")
    start = get_meta(conn, "last_scan_start")
    try:
        duration = float(get_meta(conn, "last_scan_duration_seconds") or 0)
    except ValueError:
        duration = 0.0
    warn_after = max(60.0, duration)
    stale = False
    age = None
    if end:
        try:
            finished = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - finished).total_seconds()
            stale = age > warn_after
        except ValueError:
            stale = False
    deep = get_meta(conn, "deep_scan") == "1"
    try:
        quick_groups = int(get_meta(conn, "quick_dup_groups") or 0)
        quick_save = int(get_meta(conn, "quick_space_to_save") or 0)
    except ValueError:
        quick_groups, quick_save = 0, 0
    hash_save = conn.execute(
        "SELECT COALESCE(SUM(wasted_bytes),0) b FROM duplicate_groups"
    ).fetchone()["b"]
    space = int(hash_save) if deep and hash_save else quick_save
    sources = []
    for row in conn.execute(
        """
        SELECT s.id, s.name, s.path, s.layout, s.priority,
               COUNT(f.id) AS files, COALESCE(SUM(f.size),0) AS bytes
        FROM sources s LEFT JOIN files f ON f.source_id = s.id
        GROUP BY s.id
        ORDER BY s.priority DESC, s.name
        """
    ):
        sid = int(row["id"])
        has_quick = _source_has_quick_dups(conn, sid)
        has_hash = conn.execute(
            """
            SELECT COUNT(*) n FROM files f
            JOIN duplicate_groups d ON d.sha256 = f.sha256
            WHERE f.source_id = ? AND f.sha256 IS NOT NULL
            """,
            (sid,),
        ).fetchone()["n"]
        sources.append(
            {
                "id": sid,
                "name": row["name"],
                "path": row["path"],
                "layout": row["layout"],
                "priority": row["priority"],
                "files": int(row["files"]),
                "bytes": int(row["bytes"]),
                "has_duplicates": bool(has_quick or has_hash),
                "has_quick_duplicates": bool(has_quick),
                "has_hash_duplicates": bool(has_hash),
            }
        )
    pairs = json.loads(get_meta(conn, "archive_pairs") or "[]")
    conn.close()
    if not sources:
        sources = [
            {
                "id": i,
                "name": s.name,
                "path": str(s.path),
                "layout": s.layout,
                "priority": s.priority,
                "files": 0,
                "bytes": 0,
                "has_duplicates": False,
                "has_quick_duplicates": False,
                "has_hash_duplicates": False,
                "scanned": False,
            }
            for i, s in enumerate(cfg.sources, start=1)
        ]
    else:
        for s in sources:
            s["scanned"] = True
    return {
        "generated": utc_now(),
        "last_scan_start": start,
        "last_scan_end": end,
        "last_scan_duration_seconds": duration,
        "warn_after_seconds": warn_after,
        "scan_age_seconds": age,
        "stale": stale,
        "deep": deep,
        "needs_deep": (not deep) and quick_groups > 0,
        "quick_dup_groups": quick_groups,
        "space_to_save": space,
        "space_to_save_kind": "exact" if deep and hash_save else "quick",
        "sources": sources,
        "library_root": str(cfg.library_root),
        "quarantine": str(cfg.quarantine),
        "zip_pairs": pairs,
        "zip_paired": sum(1 for p in pairs if p.get("status") == "pair_complete"),
        "zip_only": sum(1 for p in pairs if p.get("status") == "zip_only"),
    }


def _source_has_quick_dups(conn, source_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM files a
        JOIN files b ON LOWER(a.stem || a.ext) = LOWER(b.stem || b.ext)
          AND a.size = b.size
          AND CAST(a.mtime AS INTEGER) = CAST(b.mtime AS INTEGER)
          AND a.id != b.id
        WHERE a.source_id = ?
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    return row is not None


def browse(cfg: LibraryConfig, *, kind: str, source_id: int | None = None, rel: str = "") -> dict:
    """One directory listing. ``kind`` is source, dest, or quarantine."""
    if kind == "dest":
        root = cfg.library_root
        label = "destination"
    elif kind == "quarantine":
        root = cfg.quarantine
        label = "quarantine"
    elif kind == "source":
        if source_id is None:
            raise ValueError("source_id required")
        conn = connect(cfg.index_db)
        row = conn.execute("SELECT path, name FROM sources WHERE id=?", (source_id,)).fetchone()
        conn.close()
        if not row:
            raise ValueError("unknown source")
        root = Path(row["path"])
        label = row["name"]
    else:
        raise ValueError("kind must be source, dest, or quarantine")

    rel = rel.replace("\\", "/").lstrip("/")
    if ".." in Path(rel).parts:
        raise ValueError("invalid path")
    target = (root / rel).resolve() if rel else root.resolve()
    if not path_is_under(target, root.resolve()):
        raise ValueError("path escapes root")
    dirs: list[dict] = []
    files: list[dict] = []
    if not target.exists():
        return {"kind": kind, "label": label, "root": str(root), "rel": rel, "dirs": [], "files": [], "missing": True}
    if target.is_file():
        st = target.stat()
        return {
            "kind": kind,
            "label": label,
            "root": str(root),
            "rel": rel,
            "dirs": [],
            "files": [{"name": target.name, "size": st.st_size, "mtime": st.st_mtime}],
            "missing": False,
        }
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return {"kind": kind, "label": label, "root": str(root), "rel": rel, "error": str(exc), "dirs": [], "files": []}
    for entry in entries[:800]:
        if entry.name in cfg.exclude_dir_names:
            continue
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                dirs.append({"name": entry.name, "rel": posix(Path(rel) / entry.name) if rel else entry.name})
            elif entry.is_file():
                st = entry.stat()
                files.append({"name": entry.name, "size": int(st.st_size), "mtime": st.st_mtime, "zip": entry.suffix.lower() == ".zip"})
        except OSError:
            continue
    parent = str(Path(rel).parent).replace("\\", "/") if rel else ""
    if parent == ".":
        parent = ""
    return {
        "kind": kind,
        "label": label,
        "root": str(root),
        "rel": rel,
        "parent": parent,
        "dirs": dirs,
        "files": files,
        "missing": False,
    }


def zip_contents(cfg: LibraryConfig, file_id: int) -> dict:
    conn = connect(cfg.index_db)
    f = conn.execute("SELECT id, path, stem, ext FROM files WHERE id=?", (file_id,)).fetchone()
    if not f:
        conn.close()
        raise ValueError("unknown file")
    arch = conn.execute("SELECT id FROM archives WHERE file_id=?", (file_id,)).fetchone()
    entries = []
    if arch:
        entries = [
            {"inner_path": r["inner_path"], "size": r["size"]}
            for r in conn.execute(
                "SELECT inner_path, size FROM archive_entries WHERE archive_id=? ORDER BY inner_path LIMIT 400",
                (int(arch["id"]),),
            )
        ]
    conn.close()
    return {"file_id": file_id, "path": f["path"], "entries": entries}


def compare_grid(cfg: LibraryConfig, source_ids: list[int] | None = None) -> dict:
    conn = connect(cfg.index_db)
    sources = list(conn.execute("SELECT id, name, path, layout, priority FROM sources ORDER BY priority DESC, name"))
    if not sources:
        conn.close()
        return {"sources": [], "rows": [], "space_to_save": 0}
    if not source_ids:
        source_ids = [int(s["id"]) for s in sources]
    wanted = {int(i) for i in source_ids}
    src_by_id = {int(s["id"]): s for s in sources}

    files = list(
        conn.execute(
            """
            SELECT f.id, f.source_id, f.relpath, f.stem, f.ext, f.size, f.mtime, f.sha256,
                   f.path, f.is_archive, s.name AS source_name, s.layout AS source_layout,
                   s.priority AS priority, f.bundle, f.pack, f.relpath_from_pack, f.year, f.month,
                   f.zip_name, f.group_name
            FROM files f JOIN sources s ON s.id = f.source_id
            """
        )
    )
    meta_keys: dict[tuple, list] = {}
    hash_keys: dict[str, list] = {}
    by_source_rel: dict[tuple[int, str], dict] = {}
    for row in files:
        rel = row["relpath"] or row["stem"] + (row["ext"] or "")
        by_source_rel[(int(row["source_id"]), rel)] = dict(row)
        mk = (str(row["stem"] or "").lower() + str(row["ext"] or "").lower(), int(row["size"]), int(row["mtime"]))
        meta_keys.setdefault(mk, []).append(row)
        if row["sha256"]:
            hash_keys.setdefault(row["sha256"], []).append(row)

    meta_dup_ids = {int(r["id"]) for g in meta_keys.values() if len(g) > 1 for r in g}
    hash_dup_ids = {int(r["id"]) for g in hash_keys.values() if len(g) > 1 for r in g}
    meta_mismatch_ids: set[int] = set()
    for group in meta_keys.values():
        if len(group) < 2:
            continue
        hashes = {r["sha256"] for r in group if r["sha256"]}
        if len(hashes) > 1:
            meta_mismatch_ids.update(int(r["id"]) for r in group)

    rels = sorted({rel for (sid, rel) in by_source_rel if sid in wanted})
    rows = []
    for rel in rels[:2000]:
        cells = []
        for sid in source_ids:
            rec = by_source_rel.get((sid, rel))
            if not rec:
                cells.append(None)
                continue
            in_lib = _in_library(cfg, rec)
            flags = []
            if rec["id"] in meta_mismatch_ids:
                flags.append("meta_mismatch")
            if rec["id"] in hash_dup_ids:
                flags.append("dup_hash")
            elif rec["id"] in meta_dup_ids:
                flags.append("dup_meta")
            else:
                flags.append("unique")
            if in_lib:
                flags.append("in_library")
            if rec["is_archive"] and (rec["ext"] or "").lower() == ".zip":
                flags.append("zip")
            cells.append(
                {
                    "file_id": rec["id"],
                    "relpath": rel,
                    "size": rec["size"],
                    "mtime": rec["mtime"],
                    "flags": flags,
                    "zip": (rec["ext"] or "").lower() == ".zip",
                }
            )
        rows.append({"relpath": rel, "cells": cells})

    has_dups = {}
    for sid in source_ids:
        has_dups[sid] = any(
            cell and ("dup_meta" in cell["flags"] or "dup_hash" in cell["flags"])
            for row in rows
            for i, cell in enumerate(row["cells"])
            if source_ids[i] == sid
        )

    space = int(get_meta(conn, "quick_space_to_save") or 0)
    if get_meta(conn, "deep_scan") == "1":
        space = int(
            conn.execute("SELECT COALESCE(SUM(wasted_bytes),0) b FROM duplicate_groups").fetchone()["b"]
            or space
        )
    conn.close()
    return {
        "source_ids": source_ids,
        "sources": [
            {
                "id": sid,
                "name": src_by_id[sid]["name"],
                "has_duplicates": bool(has_dups.get(sid)),
            }
            for sid in source_ids
            if sid in src_by_id
        ],
        "all_sources": [
            {"id": int(s["id"]), "name": s["name"]} for s in sources
        ],
        "rows": rows,
        "space_to_save": space,
    }


def _in_library(cfg: LibraryConfig, rec: dict) -> bool:
    tok = tokens_for(
        cfg,
        source_name=rec["source_name"],
        relpath=rec["relpath"] or "",
        stem=rec["stem"] or "",
        ext=rec["ext"] or "",
        bundle=rec["bundle"] or "_unknown",
        pack=rec["pack"] or "_root",
        relpath_from_pack=rec["relpath_from_pack"] or rec["relpath"] or "",
        year=rec["year"] or "0000",
        month=rec["month"] or "00",
        zip_name=rec["zip_name"] or ((rec["stem"] or "") + (rec["ext"] or "")),
        parent_dir="",
        group=rec["group_name"] or "",
    )
    ext = (rec["ext"] or "").lower()
    try:
        if ext in cfg.archive_exts():
            dest = dest_path(cfg, "zip_store", tok)
        else:
            dest = dest_path(cfg, rec["source_layout"], tok)
    except (KeyError, ValueError):
        return False
    try:
        return dest.is_file() and dest.stat().st_size == int(rec["size"])
    except OSError:
        return False
