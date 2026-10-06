# SPDX-License-Identifier: AGPL-3.0-or-later
"""Execute a plan: copy or hardlink into the library. Never deletes in v0.1."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from lam.config import LibraryConfig, ensure_dirs, path_is_under
from lam.db import connect
from lam.hashing import sha256_full
from lam.layouts import confined_to
from lam.log import finish_run, log_file_event, start_run
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
    run = start_run("apply", cfg.log_dir)
    stats = {"copied": 0, "hardlinked": 0, "skipped": 0, "failed": 0, "quarantined": 0}
    try:
        conn = connect(cfg.index_db)
        plan = list(conn.execute("SELECT file_id, src, dest, op, reason FROM plan_rows ORDER BY id"))
        if not plan:
            conn.close()
            raise RuntimeError("no plan rows; run plan first")

        total = len(plan)
        for i, row in enumerate(plan, start=1):
            src = Path(row["src"])
            dest = Path(row["dest"]) if row["dest"] else None
            op = row["op"]
            reason = row["reason"]
            try:
                result_op, note = _apply_row(cfg, src, dest, op)
            except Exception as exc:  # noqa: BLE001 — log and continue
                stats["failed"] += 1
                log_file_event(
                    run,
                    "error",
                    "copy",
                    src,
                    ok=False,
                    error=exc,
                    func="lam.apply._apply_row",
                )
                conn.execute(
                    "INSERT INTO actions_log(ts, command, dry_run, src, dest, op, reason) VALUES(?,?,?,?,?,?,?)",
                    (utc_now(), "apply", 0, posix(src), posix(dest) if dest else None, "error", str(exc)),
                )
                conn.commit()
                continue
            stats[result_op] = stats.get(result_op, 0) + 1
            logged = note or reason
            log_file_event(
                run,
                "info",
                "skip" if result_op == "skipped" else "copy",
                src,
                ok=True,
                func="lam.apply._apply_row",
            )
            conn.execute(
                "INSERT INTO actions_log(ts, command, dry_run, src, dest, op, reason) VALUES(?,?,?,?,?,?,?)",
                (
                    utc_now(),
                    "apply",
                    0,
                    posix(src),
                    posix(dest) if dest else None,
                    result_op,
                    logged,
                ),
            )
            conn.commit()
            if i % 50 == 0 and progress:
                progress("apply", {"done": i, "total": total, **stats})

        conn.close()
        if progress:
            progress("apply", {"done": total, "total": total, **stats})
        return {"rows": total, **stats}
    finally:
        finish_run(
            run,
            ok=stats["copied"] + stats["hardlinked"] + stats["quarantined"],
            skipped=stats["skipped"],
            failed=stats["failed"],
        )


def _copy_via_temp(src: Path, dest: Path) -> None:
    """Copy *src* to a sibling temp, then publish it only when *dest* is absent."""
    if dest.exists():
        raise FileExistsError(f"destination exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".lam-tmp-{uuid.uuid4().hex}")
    created = False
    try:
        with src.open("rb") as inf, tmp.open("xb") as outf:
            created = True
            while True:
                chunk = inf.read(1024 * 1024)
                if not chunk:
                    break
                outf.write(chunk)
        shutil.copystat(src, tmp)
        if sha256_full(tmp) != sha256_full(src):
            raise OSError("copy sha256 mismatch after write")
        if dest.exists():
            raise FileExistsError(f"destination exists: {dest}")
        os.replace(tmp, dest)
    except Exception:
        if created and tmp.is_file():
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _apply_row(cfg: LibraryConfig, src: Path, dest: Path | None, op: str) -> tuple[str, str | None]:
    """Return ``(result_op, reason_note)``. The note records a hardlink fallback."""
    if dest is None or op == "skip":
        return "skipped", None
    dest = confined_to(dest, cfg.library_root, cfg.quarantine)
    if not _write_allowed(cfg, dest):
        raise PermissionError(f"protected path, write refused: {dest}")
    if not src.is_file():
        raise FileNotFoundError(f"source missing: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.stat().st_size == src.stat().st_size:
            return "skipped", None
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
            return result_label or "hardlinked", None
        except OSError as exc:
            note = f"{type(exc).__name__}: {exc}"
            _copy_via_temp(src, dest)
            return result_label or "copied", note

    if method == "move":
        if cfg.apply.never_modify_sources:
            _copy_via_temp(src, dest)
            return result_label or "copied", None
        shutil.move(str(src), str(dest))
        return result_label or "copied", None

    _copy_via_temp(src, dest)
    return result_label or "copied", None


def _write_allowed(cfg: LibraryConfig, dest: Path) -> bool:
    """Library and quarantine may be written. Other protected roots may not."""
    if path_is_under(dest, cfg.library_root) or path_is_under(dest, cfg.quarantine):
        return True
    for root in cfg.protect_paths:
        if path_is_under(dest, root):
            return False
    return True
