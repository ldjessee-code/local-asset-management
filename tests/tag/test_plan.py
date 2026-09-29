# SPDX-License-Identifier: AGPL-3.0-or-later
"""Level-1 plans: confidence, batches, copies, conflicts, dry-run."""

from __future__ import annotations

import json
import os
from pathlib import Path

from lam.actions import run_file_actions, validate_plan_document
from lam.tag.plan import run_tag_plan
from lam.tag.scan import run_tag_scan


def _touch(path: Path, data: bytes, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _scan(root: Path, out: Path) -> dict:
    return run_tag_scan(root, out=out, no_model=True)


def test_moves_only_high_confidence_and_review_list(tmp_path: Path):
    src = tmp_path / "in"
    _touch(src / "docs" / "doc.pdf", b"%PDF-1.4\ndoc\n", 1_700_000_000)
    _touch(src / "pics" / "shot.png", b"\x89PNG\r\n\x1a\nshot", 1_700_000_000 + 10_000)
    tags = tmp_path / "tags.json"
    _scan(src, tags)
    dest = tmp_path / "lib"
    dest.mkdir()
    plan_path = tmp_path / "plan.json"
    built = run_tag_plan(tags, dest=dest, level=1, out=plan_path)
    moves = [a for a in built["plan"]["actions"] if a["op"] == "move"]
    assert len(moves) == 1
    assert Path(moves[0]["src"]).name == "doc.pdf"
    assert Path(moves[0]["dst"]) == dest / "_Documents" / "doc.pdf"
    assert validate_plan_document(built["plan"]) == []
    review = built["review"]
    assert review["schema"] == "lam-tag-review/v1"
    assert any(item["rel"] == "pics/shot.png" and item["confidence"] == "low" for item in review["items"])
    assert (tmp_path / "plan.review.json").is_file()
    assert not (dest / "_Documents" / "doc.pdf").exists()
    assert (src / "docs" / "doc.pdf").is_file()


def test_batch_stays_together(tmp_path: Path):
    src = tmp_path / "in"
    t0 = 1_700_000_000.0
    _touch(src / "doc.pdf", b"%PDF-1.4\none\n", t0)
    _touch(src / "shot.png", b"\x89PNG\r\n\x1a\npng", t0 + 30)
    tags = tmp_path / "tags.json"
    doc = _scan(src, tags)
    assert doc["files"][0]["batch"] == doc["files"][1]["batch"]
    dest = tmp_path / "lib"
    dest.mkdir()
    built = run_tag_plan(tags, dest=dest, level=1, out=tmp_path / "plan.json")
    assert [a for a in built["plan"]["actions"] if a["op"] == "move"] == []
    rels = {item["rel"] for item in built["review"]["items"]}
    assert rels == {"doc.pdf", "shot.png"}
    assert all("batch held" in item["reason"] for item in built["review"]["items"])


def test_uniform_batch_moves(tmp_path: Path):
    src = tmp_path / "in"
    t0 = 1_700_000_000.0
    _touch(src / "a.pdf", b"%PDF-1.4\na\n", t0)
    _touch(src / "b.pdf", b"%PDF-1.4\nb\n", t0 + 20)
    tags = tmp_path / "tags.json"
    _scan(src, tags)
    dest = tmp_path / "lib"
    dest.mkdir()
    built = run_tag_plan(tags, dest=dest, level=1, out=tmp_path / "plan.json")
    names = sorted(Path(a["dst"]).name for a in built["plan"]["actions"] if a["op"] == "move")
    assert names == ["a.pdf", "b.pdf"]
    assert built["review"]["items"] == []


def test_copy_group_keeps_shortest_then_oldest(tmp_path: Path):
    src = tmp_path / "in"
    payload = b"%PDF-1.4\nsame\n"
    t0 = 1_700_000_000.0
    _touch(src / "a.pdf", payload, t0 + 50)
    _touch(src / "nest" / "a.pdf", payload, t0)
    tags = tmp_path / "tags.json"
    _scan(src, tags)
    dest = tmp_path / "lib"
    dest.mkdir()
    built = run_tag_plan(tags, dest=dest, level=1, out=tmp_path / "plan.json")
    moves = [a for a in built["plan"]["actions"] if a["op"] == "move"]
    recycles = [a for a in built["plan"]["actions"] if a["op"] == "recycle"]
    assert len(moves) == 1 and len(recycles) == 1
    assert Path(moves[0]["src"]) == (src / "a.pdf").resolve()
    assert Path(recycles[0]["src"]) == (src / "nest" / "a.pdf").resolve()
    assert "sha256" in moves[0] and "sha256" in recycles[0]

    src2 = tmp_path / "in2"
    _touch(src2 / "b.txt", b"same-text", t0)
    _touch(src2 / "a.txt", b"same-text", t0 + 80)
    tags2 = tmp_path / "tags2.json"
    _scan(src2, tags2)
    built2 = run_tag_plan(tags2, dest=dest, level=1, out=tmp_path / "plan2.json")
    moves2 = [a for a in built2["plan"]["actions"] if a["op"] == "move"]
    recycles2 = [a for a in built2["plan"]["actions"] if a["op"] == "recycle"]
    assert Path(moves2[0]["src"]).name == "b.txt"
    assert Path(recycles2[0]["src"]).name == "a.txt"


def test_destination_conflict_goes_to_review(tmp_path: Path):
    src = tmp_path / "in"
    _touch(src / "left" / "note.pdf", b"%PDF-1.4\nleft\n", 1_700_000_000)
    _touch(src / "right" / "note.pdf", b"%PDF-1.4\nright\n", 1_700_000_500)
    tags = tmp_path / "tags.json"
    _scan(src, tags)
    dest = tmp_path / "lib"
    dest.mkdir()
    built = run_tag_plan(tags, dest=dest, level=1, out=tmp_path / "plan.json")
    assert [a for a in built["plan"]["actions"] if a["op"] == "move"] == []
    assert {item["rel"] for item in built["review"]["items"]} == {"left/note.pdf", "right/note.pdf"}
    assert all("collision" in item["reason"] for item in built["review"]["items"])

    src2 = tmp_path / "in-exists"
    _touch(src2 / "doc.pdf", b"%PDF-1.4\nexists\n")
    occupied = dest / "_Documents" / "doc.pdf"
    _touch(occupied, b"already here")
    tags2 = tmp_path / "tags-exists.json"
    _scan(src2, tags2)
    built2 = run_tag_plan(tags2, dest=dest, level=1, out=tmp_path / "plan-exists.json")
    assert [a for a in built2["plan"]["actions"] if a["op"] == "move"] == []
    assert built2["review"]["items"][0]["reason"].startswith("destination exists")
    assert occupied.read_bytes() == b"already here"


def test_plan_skips_git_validates_and_dry_run(tmp_path: Path):
    src = tmp_path / "in"
    _touch(src / "doc.pdf", b"%PDF-1.4\ndry\n")
    tags_path = tmp_path / "tags.json"
    doc = _scan(src, tags_path)
    git_abs = str((src / ".git" / "config").resolve())
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("x", encoding="utf-8")
    git_row = dict(doc["files"][0])
    git_row["rel"] = ".git/config"
    git_row["abs"] = git_abs
    git_row["sha256"] = "a" * 64
    git_row["batch"] = "dir:.git"
    git_row["copy_group"] = None
    doc["files"].append(git_row)
    tags_path.write_text(json.dumps(doc), encoding="utf-8")
    dest = tmp_path / "lib"
    dest.mkdir()
    plan_path = tmp_path / "plan.json"
    built = run_tag_plan(tags_path, dest=dest, level=1, out=plan_path)
    blob = json.dumps(built["plan"])
    assert ".git" not in blob.lower()
    assert validate_plan_document(json.loads(plan_path.read_text(encoding="utf-8"))) == []
    results, code = run_file_actions(
        plan_path,
        apply=False,
        results_path=tmp_path / "results.json",
        printer=lambda _line: None,
    )
    assert code == 0
    assert results["mode"] == "dry-run"
    assert results["items"]
    assert all(item["status"] == "would_ok" for item in results["items"])
    assert (src / "doc.pdf").is_file()
    assert not (dest / "_Documents" / "doc.pdf").exists()
