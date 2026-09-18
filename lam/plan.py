# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn a scan into dest paths. Does not copy.

Canonical copy of each SHA-256 follows source ``priority``. Extras go to
quarantine when ``apply.quarantine_exact_dupes`` is true.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from lam.config import LibraryConfig, ensure_dirs
from lam.db import connect
from lam.layouts import confined_to, dest_path, tokens_for
from lam.util import posix

Progress = Callable[[str, dict], None]


def run_plan(cfg: LibraryConfig, progress: Progress | None = None) -> dict:
    """Write ``plan_rows`` in the index. Returns counts by op (copy, quarantine, …)."""
    ensure_dirs(cfg)
    conn = connect(cfg.index_db)
    conn.execute("DELETE FROM plan_rows")

    files = list(
        conn.execute(
            """
            SELECT f.*, s.name AS source_name, s.layout AS source_layout, s.priority AS priority
            FROM files f JOIN sources s ON s.id = f.source_id
            ORDER BY s.priority DESC, f.id
            """
        )
    )
    dupe_sha = {
        r["sha256"]: int(r["canonical_file_id"])
        for r in conn.execute("SELECT sha256, canonical_file_id FROM duplicate_groups")
    }

    sidecar_parent = {
        r["path"]: r["parent_file_id"]
        for r in conn.execute("SELECT path, parent_file_id FROM sidecars")
    }

    dest_for_id: dict[int, Path] = {}
    used_dests: dict[str, int] = {}
    rows: list[tuple] = []
    counts = {"copy": 0, "hardlink": 0, "quarantine": 0, "skip": 0}

    default_op = cfg.apply.default_op if cfg.apply.default_op in {"copy", "hardlink"} else "copy"

    def tokens(row, parent_dir: str = "") -> dict[str, str]:
        return tokens_for(
            cfg,
            source_name=row["source_name"],
            relpath=row["relpath"],
            stem=row["stem"],
            ext=row["ext"] or "",
            bundle=row["bundle"] or "_unknown",
            pack=row["pack"] or "_root",
            relpath_from_pack=row["relpath_from_pack"] or row["relpath"],
            year=row["year"] or "0000",
            month=row["month"] or "00",
            zip_name=row["zip_name"] or (row["stem"] + (row["ext"] or "")),
            parent_dir=parent_dir,
            group=row["group_name"] or "",
        )

    def assign(row, dest: Path, op: str, reason: str) -> None:
        dest = confined_to(dest, cfg.library_root, cfg.quarantine)
        key = posix(dest)
        if key in used_dests:
            dest = dest.with_name(f"{dest.stem}__{row['id']}{dest.suffix}")
            dest = confined_to(dest, cfg.library_root, cfg.quarantine)
            key = posix(dest)
            reason = reason + "; dest collision renamed"
        used_dests[key] = int(row["id"])
        dest_for_id[int(row["id"])] = dest
        rows.append((int(row["id"]), row["path"], posix(dest), op, reason))
        counts[op] = counts.get(op, 0) + 1

    # Pass 1: non-sidecars
    sidecar_exts = cfg.sidecar_exts()
    pending_sidecars = []
    for row in files:
        ext = (row["ext"] or "").lower()
        if ext in sidecar_exts:
            pending_sidecars.append(row)
            continue
        _plan_one(
            cfg,
            row,
            dupe_sha,
            default_op,
            tokens,
            assign,
            parent_dir="",
        )

    # Pass 2: sidecars, next to planned parent dest when possible
    for row in pending_sidecars:
        parent_id = sidecar_parent.get(row["path"])
        parent_dir = ""
        layout_name = "sidecar_with_parent"
        if parent_id and parent_id in dest_for_id:
            parent_dir = dest_for_id[parent_id].parent.as_posix()
        else:
            layout_name = "keep_relpath"
            # orphans live under _unsorted via keep_relpath but with a fake relpath
            row = dict(row)
            row["relpath"] = f"_unsorted/{row['relpath']}"
        tok = tokens(row, parent_dir=parent_dir)
        if layout_name == "keep_relpath":
            dest = dest_path(cfg, "keep_relpath", tok)
            reason = "orphan sidecar"
        else:
            dest = dest_path(cfg, "sidecar_with_parent", tok)
            reason = "sidecar with parent"
        sha = row["sha256"]
        if (
            cfg.apply.quarantine_exact_dupes
            and sha
            and sha in dupe_sha
            and int(row["id"]) != dupe_sha[sha]
        ):
            dest = cfg.quarantine / row["source_name"] / row["relpath"]
            assign(row, dest, "quarantine", "exact duplicate sidecar")
        else:
            assign(row, dest, default_op, reason)

    conn.executemany(
        "INSERT INTO plan_rows(file_id, src, dest, op, reason) VALUES(?,?,?,?,?)",
        rows,
    )
    conn.commit()
    summary = {
        "rows": len(rows),
        **counts,
        "default_op": default_op,
        "never_modify_sources": cfg.apply.never_modify_sources,
    }
    conn.close()
    if progress:
        progress("plan", summary)
    return summary


def _plan_one(cfg, row, dupe_sha, default_op, tokens, assign, parent_dir: str) -> None:
    sha = row["sha256"]
    if (
        cfg.apply.quarantine_exact_dupes
        and sha
        and sha in dupe_sha
        and int(row["id"]) != dupe_sha[sha]
    ):
        dest = cfg.quarantine / row["source_name"] / Path(row["relpath"])
        assign(row, dest, "quarantine", f"exact duplicate; canonical file_id={dupe_sha[sha]}")
        return

    tok = tokens(row, parent_dir=parent_dir)
    ext = (row["ext"] or "").lower()
    if ext in cfg.archive_exts():
        dest = dest_path(cfg, "zip_store", tok)
        assign(row, dest, default_op, "archive stored via zip_store")
        return

    dest = dest_path(cfg, row["source_layout"], tok)
    assign(row, dest, default_op, f"layout {row['source_layout']}")


def list_plan(cfg: LibraryConfig, limit: int = 500) -> list[dict]:
    conn = connect(cfg.index_db)
    rows = conn.execute(
        "SELECT file_id, src, dest, op, reason FROM plan_rows ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
