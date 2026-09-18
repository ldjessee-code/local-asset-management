# SPDX-License-Identifier: AGPL-3.0-or-later
"""Execute a plan: copy or hardlink into the library. Never deletes in v0.1."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

from lam.config import LibraryConfig, ensure_dirs, path_is_under
from lam.db import connect
from lam.layouts import confined_to
from lam.util import posix, utc_now

Progress = Callable[[str, dict], None]


def run_apply(
    cfg: LibraryConfig,
    *,
    yes: bool,
    progress: Progress | None = None,
) -> dict:
    """Copy/hardlink each plan row. ``yes`` must be true. Sources stay put by default."""
    if not yes:
        raise RuntimeError("apply requires confirmation (--yes or confirm=true)")
    ensure_dirs(cfg)
    conn = connect(cfg.index_db)
    plan = list(conn.execute("SELECT file_id, src, dest, op, reason FROM plan_rows ORDER BY id"))
    if not plan:
        conn.close()
        raise RuntimeError("no plan rows; run plan first")

    stats = {"copied": 0, "hardlinked": 0, "skipped": 0, "failed": 0, "quarantined": 0}
    total = len(plan)
    for i, row in enumerate(plan, start=1):
        src = Path(row["src"])
        dest = Path(row["dest"]) if row["dest"] else None
        op = row["op"]
        reason = row["reason"]
        try:
            result_op = _apply_row(cfg, src, dest, op)
        except Exception as exc:  # noqa: BLE001 — log and continue
            stats["failed"] += 1
            conn.execute(
                "INSERT INTO actions_log(ts, command, dry_run, src, dest, op, reason) VALUES(?,?,?,?,?,?,?)",
                (utc_now(), "apply", 0, posix(src), posix(dest) if dest else None, "error", str(exc)),
            )
            continue
        stats[result_op] = stats.get(result_op, 0) + 1
        conn.execute(
            "INSERT INTO actions_log(ts, command, dry_run, src, dest, op, reason) VALUES(?,?,?,?,?,?,?)",
            (
                utc_now(),
                "apply",
                0,
                posix(src),
                posix(dest) if dest else None,
                result_op,
                reason,
            ),
        )
        if i % 50 == 0:
            conn.commit()
            if progress:
                progress("apply", {"done": i, "total": total, **stats})

    conn.commit()
    conn.close()
    if progress:
        progress("apply", {"done": total, "total": total, **stats})
    return {"rows": total, **stats}


def _apply_row(cfg: LibraryConfig, src: Path, dest: Path | None, op: str) -> str:
    if dest is None or op == "skip":
        return "skipped"
    dest = confined_to(dest, cfg.library_root, cfg.quarantine)
    if not _write_allowed(cfg, dest):
        raise PermissionError(f"protected path, write refused: {dest}")
    if not src.is_file():
        raise FileNotFoundError(f"source missing: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.stat().st_size == src.stat().st_size:
            return "skipped"
        raise FileExistsError(f"destination exists with different size: {dest}")

    method = op
    if op == "quarantine":
        method = cfg.apply.default_op if cfg.apply.default_op in {"copy", "hardlink"} else "copy"
        result_label = "quarantined"
    else:
        result_label = None

    if method == "hardlink":
        try:
            os.link(src, dest)
            return result_label or "hardlinked"
        except OSError:
            shutil.copy2(src, dest)
            return result_label or "copied"

    if method == "move":
        if cfg.apply.never_modify_sources:
            shutil.copy2(src, dest)
            return result_label or "copied"
        shutil.move(str(src), str(dest))
        return result_label or "copied"

    shutil.copy2(src, dest)
    return result_label or "copied"


def _write_allowed(cfg: LibraryConfig, dest: Path) -> bool:
    """Library and quarantine may be written. Other protected roots may not."""
    if path_is_under(dest, cfg.library_root) or path_is_under(dest, cfg.quarantine):
        return True
    for root in cfg.protect_paths:
        if path_is_under(dest, root):
            return False
    return True
