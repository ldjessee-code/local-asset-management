# SPDX-License-Identifier: AGPL-3.0-or-later
"""A scan with no --record and no --apply writes nothing."""

from __future__ import annotations

from pathlib import Path

from lam.oversize.engine import run_oversize_scan


def _snapshot(root: Path) -> list[tuple[str, int]]:
    rows = []
    for path in root.rglob("*"):
        if path.is_file():
            rows.append((str(path.resolve()), path.stat().st_size))
    return sorted(rows)


def test_dry_run_writes_nothing(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Combined" / "Big_Day.jpg", 80, 40)
    write_image(root / "Combined" / "Big_A_Day.jpg", 8, 8)
    register = root / "oversize-register.json"
    before = _snapshot(root)
    recycled: list[Path] = []
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        register=register,
        recycle=recycled.append,
    )
    assert code == 0
    assert payload["mode"] == "dry-run"
    assert len(payload["files"]) == 1
    assert _snapshot(root) == before
    assert not register.exists()
    assert recycled == []
