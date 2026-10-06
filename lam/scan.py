# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only inventory of source trees.

``run_scan`` walks every source, lists zip members without extracting to disk,
hashes only size-colliding files (prefix then SHA-256), and writes SQLite.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from lam.config import LibraryConfig, ensure_dirs
from lam.db import connect, reset_scan_tables, set_meta
from lam.hashing import hash_size_collisions
from lam.log import finish_run, log_file_event, start_run
from lam.util import posix, split_bundle_pack, utc_now
from lam.walk import walk_files
from lam.zips import index_zip_file

Progress = Callable[[str, dict], None]


def _log_jsonl(cfg: LibraryConfig, event: dict) -> None:
    path = cfg.log_dir / "scan.jsonl"
    event = {"ts": utc_now(), **event}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, default=str) + "\n")


def run_scan(cfg: LibraryConfig, progress: Progress | None = None, *, deep: bool = True) -> dict:
    """Index sources into ``cfg.index_db``.

    ``deep=False`` walks and lists zips only (name/date/size). ``deep=True``
    also hashes size collisions. Returns counts (files, dupes, zips, …).
    """
    ensure_dirs(cfg)
    run = start_run("scan", cfg.log_dir)
    outcome: dict = {"files": 0, "errors": 0}
    completed = False
    try:
        outcome = _scan_impl(cfg, progress, run=run, deep=deep)
        completed = True
        return outcome
    finally:
        failed = int(outcome.get("errors") or 0)
        if not completed:
            failed = max(failed, 1)
        finish_run(run, ok=int(outcome.get("files") or 0), skipped=0, failed=failed)


def _scan_impl(
    cfg: LibraryConfig,
    progress: Progress | None,
    *,
    run,
    deep: bool,
) -> dict:
    t0 = time.monotonic()
    conn = connect(cfg.index_db)
    reset_scan_tables(conn)
    set_meta(conn, "last_scan_start", utc_now())
    set_meta(conn, "deep_scan", "1" if deep else "0")
    set_meta(conn, "config_path", str(cfg.config_path))

    source_ids: dict[str, int] = {}
    for src in cfg.sources:
        cur = conn.execute(
            "INSERT INTO sources(path, name, layout, priority) VALUES(?,?,?,?)",
            (posix(src.path), src.name, src.layout, src.priority),
        )
        source_ids[posix(src.path)] = int(cur.lastrowid)

    errors: list[tuple[str, str]] = []

    def on_error(path: str, message: str) -> None:
        errors.append((path, message))
        conn.execute("INSERT INTO scan_errors(path, error) VALUES(?,?)", (path, message))
        log_file_event(
            run,
            "warning",
            "stat",
            path,
            ok=False,
            func="lam.scan.on_error",
        )

    seen = 0
    batch: list[tuple] = []

    insert_sql = """
        INSERT INTO files(
          path, source_id, size, mtime, ext, group_name, bundle, pack, relpath,
          relpath_from_pack, year, month, stem, zip_name, dev, inode, is_archive
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    def flush() -> None:
        if not batch:
            return
        conn.executemany(insert_sql, batch)
        batch.clear()

    for src in cfg.sources:
        sid = source_ids[posix(src.path)]
        for path, st in walk_files(src.path, cfg.exclude_dir_names, on_error=on_error):
            try:
                rel = path.relative_to(src.path).as_posix()
            except ValueError:
                rel = path.name
            ext = path.suffix.lower()
            group = cfg.group_for(ext)
            bundle, pack, from_pack = split_bundle_pack(rel)
            try:
                dt = datetime.fromtimestamp(st.st_mtime)
                year, month = f"{dt.year:04d}", f"{dt.month:02d}"
            except (OSError, OverflowError, ValueError):
                year, month = "0000", "00"
            is_archive = 1 if ext in cfg.archive_exts() else 0
            rec = (
                posix(path),
                sid,
                int(st.st_size),
                float(st.st_mtime),
                ext,
                group,
                bundle,
                pack,
                rel,
                from_pack,
                year,
                month,
                path.stem,
                path.name if is_archive else "",
                getattr(st, "st_dev", 0) or 0,
                getattr(st, "st_ino", 0) or 0,
                is_archive,
            )
            batch.append(rec)
            seen += 1
            if seen % 200 == 0:
                flush()
                conn.commit()
                payload = {"seen": seen, "source": src.name}
                _log_jsonl(cfg, {"event": "progress", **payload})
                if progress:
                    progress("scan", payload)

    flush()
    conn.commit()

    # Resolve file ids for zips now that rows exist.
    zip_rows = conn.execute(
        "SELECT id, path, size, sha256 FROM files WHERE is_archive=1 AND ext='.zip'"
    ).fetchall()
    if progress:
        progress("scan", {"phase": "zip", "archives": len(zip_rows)})
    for row in zip_rows:
        index_zip_file(
            conn,
            cfg,
            file_id=int(row["id"]),
            path=Path(row["path"]),
            size=int(row["size"]),
            sha256=row["sha256"],
        )
    conn.commit()

    optional = {e.lower() for e in cfg.groups.get("archives_optional", [])}
    for row in conn.execute("SELECT path, ext FROM files WHERE is_archive=1"):
        if row["ext"] in optional and row["ext"] != ".zip":
            conn.execute(
                "INSERT INTO scan_errors(path, error) VALUES(?,?)",
                (row["path"], f"optional archive codec not enabled for {row['ext']}"),
            )

    _attach_sidecars(conn, cfg)
    _record_archive_pairs(conn)
    quick = _quick_collision_stats(conn)
    set_meta(conn, "quick_dup_groups", str(quick["groups"]))
    set_meta(conn, "quick_space_to_save", str(quick["space_to_save"]))

    if deep:
        if progress:
            progress("scan", {"phase": "hash"})
        hash_size_collisions(conn, workers=cfg.hash_workers, progress=progress)
        _build_duplicate_groups(conn, cfg)
        set_meta(conn, "deep_scan", "1")
    else:
        set_meta(conn, "deep_scan", "0")

    stats = _scan_stats(conn)
    stats["space_to_save"] = int(quick["space_to_save"] or stats.get("wasted_bytes") or 0)
    stats["quick_dup_groups"] = quick["groups"]
    stats["needs_deep"] = (not deep) and quick["groups"] > 0
    stats["deep"] = deep
    elapsed = time.monotonic() - t0
    set_meta(conn, "last_scan_end", utc_now())
    set_meta(conn, "last_scan_duration_seconds", f"{elapsed:.3f}")
    set_meta(conn, "last_scan_stats", json.dumps(stats))
    conn.commit()
    conn.close()
    _log_jsonl(cfg, {"event": "done", **stats})
    if progress:
        progress("done", stats)
    return stats


def run_deep_scan(cfg: LibraryConfig, progress: Progress | None = None) -> dict:
    """Hash size-colliding files from the last walk. Does not re-walk disks."""
    ensure_dirs(cfg)
    t0 = time.monotonic()
    conn = connect(cfg.index_db)
    if progress:
        progress("scan", {"phase": "hash"})
    conn.execute("DELETE FROM duplicate_groups")
    hash_size_collisions(conn, workers=cfg.hash_workers, progress=progress)
    _build_duplicate_groups(conn, cfg)
    set_meta(conn, "deep_scan", "1")
    stats = _scan_stats(conn)
    stats["deep"] = True
    stats["needs_deep"] = False
    elapsed = time.monotonic() - t0
    set_meta(conn, "last_deep_end", utc_now())
    set_meta(conn, "last_deep_duration_seconds", f"{elapsed:.3f}")
    conn.commit()
    conn.close()
    if progress:
        progress("done", stats)
    return stats


def _quick_collision_stats(conn) -> dict:
    rows = conn.execute(
        """
        SELECT LOWER(stem || ext) AS name, size, CAST(mtime AS INTEGER) AS mt,
               COUNT(*) AS n, SUM(size) AS bytes
        FROM files
        GROUP BY 1, 2, 3
        HAVING n > 1
        """
    ).fetchall()
    groups = 0
    extra = 0
    for row in rows:
        groups += 1
        extra += int(row["bytes"]) - int(row["size"])
    return {"groups": groups, "space_to_save": extra}


def _attach_sidecars(conn, cfg: LibraryConfig) -> None:
    sidecar_exts = cfg.sidecar_exts()
    files = list(conn.execute("SELECT id, path, source_id, stem, ext, relpath, bundle FROM files"))
    by_dir_stem: dict[tuple[int, str, str], list] = {}
    for row in files:
        parent = str(Path(row["path"]).parent.as_posix())
        key = (int(row["source_id"]), parent, row["stem"])
        by_dir_stem.setdefault(key, []).append(row)

    for row in files:
        if (row["ext"] or "").lower() not in sidecar_exts:
            continue
        parent = str(Path(row["path"]).parent.as_posix())
        key = (int(row["source_id"]), parent, row["stem"])
        siblings = [
            s
            for s in by_dir_stem.get(key, [])
            if s["id"] != row["id"] and (s["ext"] or "").lower() not in sidecar_exts
        ]
        parent_id = int(siblings[0]["id"]) if siblings else None
        conn.execute(
            "INSERT INTO sidecars(path, parent_file_id, bundle) VALUES(?,?,?)",
            (row["path"], parent_id, row["bundle"]),
        )


def _build_duplicate_groups(conn, cfg: LibraryConfig) -> None:
    rows = conn.execute(
        """
        SELECT f.id, f.path, f.size, f.sha256, f.relpath, s.priority
        FROM files f JOIN sources s ON s.id = f.source_id
        WHERE f.sha256 IS NOT NULL
        """
    ).fetchall()
    by_hash: dict[str, list] = {}
    for row in rows:
        by_hash.setdefault(row["sha256"], []).append(row)

    for sha, group in by_hash.items():
        if len(group) < 2:
            continue

        def rank(item) -> tuple:
            rel_depth = len(Path(item["relpath"]).parts)
            return (-int(item["priority"]), -rel_depth, len(item["path"]), int(item["id"]))

        canonical = min(group, key=rank)
        extra = [g for g in group if g["id"] != canonical["id"]]
        wasted = sum(int(g["size"]) for g in extra)
        conn.execute(
            """
            INSERT INTO duplicate_groups(sha256, canonical_file_id, extra_count, wasted_bytes, policy)
            VALUES(?,?,?,?,?)
            """,
            (
                sha,
                int(canonical["id"]),
                len(extra),
                wasted,
                "priority then deeper relpath then shorter path",
            ),
        )


def _record_archive_pairs(conn) -> None:
    """Mark zip/extract pairing in meta as JSON for reports."""
    pairs = []
    zips = conn.execute(
        "SELECT id, path FROM files WHERE is_archive=1 AND ext='.zip'"
    ).fetchall()
    for z in zips:
        zpath = Path(z["path"])
        extract_dir = zpath.with_suffix("")
        status = "zip_only"
        if extract_dir.is_dir():
            status = "pair_complete"
        pairs.append({"zip": z["path"], "extract_dir": posix(extract_dir), "status": status})
    set_meta(conn, "archive_pairs", json.dumps(pairs))


def _scan_stats(conn) -> dict:
    files = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM files").fetchone()
    dup = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(wasted_bytes),0) b FROM duplicate_groups"
    ).fetchone()
    zips = conn.execute("SELECT COUNT(*) n FROM files WHERE ext='.zip'").fetchone()
    entries = conn.execute("SELECT COUNT(*) n FROM archive_entries").fetchone()
    errors = conn.execute("SELECT COUNT(*) n FROM scan_errors").fetchone()
    ungrouped = conn.execute(
        "SELECT COUNT(*) n FROM files WHERE group_name IS NULL"
    ).fetchone()
    return {
        "files": int(files["n"]),
        "bytes": int(files["b"]),
        "duplicate_groups": int(dup["n"]),
        "wasted_bytes": int(dup["b"]),
        "zips": int(zips["n"]),
        "archive_entries": int(entries["n"]),
        "errors": int(errors["n"]),
        "ungrouped": int(ungrouped["n"]),
    }
