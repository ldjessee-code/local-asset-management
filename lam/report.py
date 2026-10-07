# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build JSON + markdown reports from the SQLite index."""

from __future__ import annotations

import json
from collections import defaultdict

from lam.config import LibraryConfig, ensure_dirs
from lam.db import connect, get_meta
from lam.log import finish_run, start_run
from lam.util import utc_now


def _bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def collect_report(cfg: LibraryConfig) -> dict:
    """Read the index and return a JSON-serializable summary (no file write)."""
    conn = connect(cfg.index_db)
    sources = []
    for row in conn.execute(
        """
        SELECT s.name, s.path, s.layout, s.priority,
               COUNT(f.id) AS files, COALESCE(SUM(f.size),0) AS bytes
        FROM sources s LEFT JOIN files f ON f.source_id = s.id
        GROUP BY s.id
        ORDER BY s.priority DESC, s.name
        """
    ):
        sources.append(
            {
                "name": row["name"],
                "path": row["path"],
                "layout": row["layout"],
                "priority": row["priority"],
                "files": int(row["files"]),
                "bytes": int(row["bytes"]),
            }
        )

    dupes = []
    for row in conn.execute(
        """
        SELECT d.sha256, d.extra_count, d.wasted_bytes, d.canonical_file_id, f.path AS canonical
        FROM duplicate_groups d
        JOIN files f ON f.id = d.canonical_file_id
        ORDER BY d.wasted_bytes DESC
        """
    ):
        members = [
            r["path"]
            for r in conn.execute(
                "SELECT path FROM files WHERE sha256=? ORDER BY id", (row["sha256"],)
            )
        ]
        dupes.append(
            {
                "sha256": row["sha256"],
                "canonical": row["canonical"],
                "extra_count": int(row["extra_count"]),
                "wasted_bytes": int(row["wasted_bytes"]),
                "members": members,
            }
        )

    pairs = json.loads(get_meta(conn, "archive_pairs") or "[]")
    zip_only = sum(1 for p in pairs if p.get("status") == "zip_only")
    paired = sum(1 for p in pairs if p.get("status") == "pair_complete")

    sidecars = conn.execute("SELECT COUNT(*) n FROM sidecars").fetchone()["n"]
    attached = conn.execute(
        "SELECT COUNT(*) n FROM sidecars WHERE parent_file_id IS NOT NULL"
    ).fetchone()["n"]
    orphans = int(sidecars) - int(attached)

    ungrouped_rows = list(
        conn.execute(
            "SELECT path, ext FROM files WHERE group_name IS NULL ORDER BY path LIMIT 200"
        )
    )
    errors = list(conn.execute("SELECT path, error FROM scan_errors LIMIT 200"))

    # Loose file whose size matches a zip entry (moved-not-copied hint).
    moved_hints = []
    for row in conn.execute(
        """
        SELECT f.path AS loose, e.inner_path, a.path AS zip_path, f.size
        FROM files f
        JOIN archive_entries e ON e.size = f.size
        JOIN archives a ON a.id = e.archive_id
        WHERE f.is_archive = 0
        LIMIT 100
        """
    ):
        moved_hints.append(
            {
                "loose": row["loose"],
                "inner_path": row["inner_path"],
                "zip": row["zip_path"],
                "size": int(row["size"]),
            }
        )

    totals = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM files"
    ).fetchone()
    data = {
        "generated": utc_now(),
        "files": int(totals["n"]),
        "bytes": int(totals["b"]),
        "sources": sources,
        "duplicate_groups": dupes,
        "wasted_bytes": sum(d["wasted_bytes"] for d in dupes),
        "archive_pairs": pairs,
        "zip_only": zip_only,
        "pair_complete": paired,
        "sidecars_attached": int(attached),
        "sidecars_orphan": orphans,
        "ungrouped": [{"path": r["path"], "ext": r["ext"]} for r in ungrouped_rows],
        "errors": [{"path": r["path"], "error": r["error"]} for r in errors],
        "moved_hints": moved_hints,
        "last_scan_end": get_meta(conn, "last_scan_end"),
    }
    conn.close()
    return data


def write_reports(cfg: LibraryConfig, data: dict | None = None) -> dict:
    """Write ``summary.md`` and ``exceptions.md`` under ``cfg.reports_dir``."""
    ensure_dirs(cfg)
    run = start_run("report", cfg.log_dir)
    written = data
    completed = False
    try:
        written = written or collect_report(cfg)
        summary = cfg.reports_dir / "summary.md"
        exceptions = cfg.reports_dir / "exceptions.md"
        summary.write_text(_summary_md(written), encoding="utf-8")
        exceptions.write_text(_exceptions_md(written), encoding="utf-8")
        written["summary_path"] = str(summary)
        written["exceptions_path"] = str(exceptions)
        completed = True
        return written
    finally:
        files = int((written or {}).get("files") or 0)
        errors = (written or {}).get("errors") or []
        failed = len(errors) if isinstance(errors, list) else 0
        if not completed:
            failed = max(failed, 1)
        finish_run(run, ok=files, skipped=0, failed=failed)


def _summary_md(data: dict) -> str:
    lines = [
        "# Library scan summary",
        "",
        f"- Generated: {data['generated']}",
        f"- Last scan: {data.get('last_scan_end') or '(unknown)'}",
        f"- Files: {data['files']} ({_bytes(data['bytes'])})",
        f"- Exact duplicate groups: {len(data['duplicate_groups'])}",
        f"- Wasted bytes (extras): {_bytes(data['wasted_bytes'])}",
        f"- Zip archives: {data.get('zip_only', 0) + data.get('pair_complete', 0)} "
        f"(paired {data.get('pair_complete', 0)}, zip-only {data.get('zip_only', 0)})",
        f"- Sidecars attached / orphan: {data['sidecars_attached']} / {data['sidecars_orphan']}",
        "",
        "## Sources",
        "",
        "| Name | Priority | Layout | Files | Bytes | Path |",
        "|---|---:|---|---:|---:|---|",
    ]
    for s in data["sources"]:
        lines.append(
            f"| {s['name']} | {s['priority']} | {s['layout']} | {s['files']} | {_bytes(s['bytes'])} | `{s['path']}` |"
        )
    lines += ["", "## Duplicate groups (largest first)", ""]
    if not data["duplicate_groups"]:
        lines.append("None.")
    else:
        for d in data["duplicate_groups"][:50]:
            lines.append(
                f"- `{d['sha256'][:12]}…` extra {d['extra_count']} ({_bytes(d['wasted_bytes'])}); "
                f"canonical `{d['canonical']}`"
            )
            for m in d["members"]:
                mark = "keep" if m == d["canonical"] else "extra"
                lines.append(f"  - ({mark}) `{m}`")
    lines.append("")
    return "\n".join(lines)


def _exceptions_md(data: dict) -> str:
    lines = [
        "# Library scan exceptions",
        "",
        f"- Generated: {data['generated']}",
        "",
        "## Ungrouped extensions",
        "",
    ]
    if not data["ungrouped"]:
        lines.append("None.")
    else:
        by_ext: dict[str, int] = defaultdict(int)
        for row in data["ungrouped"]:
            by_ext[row["ext"] or "(none)"] += 1
        for ext, n in sorted(by_ext.items()):
            lines.append(f"- `{ext}` × {n}")
        lines += ["", "Sample paths:", ""]
        for row in data["ungrouped"][:50]:
            lines.append(f"- `{row['path']}`")

    lines += ["", "## Scan errors", ""]
    if not data["errors"]:
        lines.append("None.")
    else:
        for row in data["errors"]:
            lines.append(f"- `{row['path']}` — {row['error']}")

    lines += ["", "## Zip pairing", ""]
    if not data["archive_pairs"]:
        lines.append("No zip archives.")
    else:
        for p in data["archive_pairs"]:
            lines.append(f"- {p['status']}: `{p['zip']}`")

    lines += ["", "## Loose files matching a zip entry by size", ""]
    if not data["moved_hints"]:
        lines.append("None.")
    else:
        lines.append("Size match only (not a content hash). Treat as a hint.")
        for h in data["moved_hints"][:50]:
            lines.append(f"- `{h['loose']}` ~ `{h['zip']}` `{h['inner_path']}` ({_bytes(h['size'])})")
    lines.append("")
    return "\n".join(lines)


def read_report_file(cfg: LibraryConfig, name: str) -> str:
    if name not in {"summary.md", "exceptions.md"}:
        raise ValueError("unknown report")
    path = cfg.reports_dir / name
    if not path.is_file():
        return "(run report first)\n"
    return path.read_text(encoding="utf-8")
