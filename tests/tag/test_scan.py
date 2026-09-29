# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan fields, hashes, copy groups, batches, burst window, .git skip."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from lam.tag.errors import TagError
from lam.tag.scan import _is_reparse, run_tag_scan


def _write(path: Path, data: bytes, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_fields_hash_copy_groups_and_git_skip(tmp_path: Path):
    root = tmp_path / "in"
    payload = b"%PDF-1.4\nhello\n"
    _write(root / "a.pdf", payload)
    _write(root / "nested" / "a-copy.pdf", payload)
    _write(root / "other.txt", b"unique-text")
    _write(root / ".git" / "HEAD", b"ref: refs/heads/main\n")
    _write(root / "sub" / ".git" / "config", b"[core]\n")
    out = tmp_path / "tags.json"
    doc = run_tag_scan(root, out=out, no_model=True)
    rels = {item["rel"] for item in doc["files"]}
    assert rels == {"a.pdf", "nested/a-copy.pdf", "other.txt"}
    by = {item["rel"]: item for item in doc["files"]}
    assert by["a.pdf"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert by["a.pdf"]["size"] == len(payload)
    assert by["a.pdf"]["extension"] == ".pdf"
    assert by["other.txt"]["extension"] == ".txt"
    assert Path(by["a.pdf"]["abs"]).is_absolute()
    assert by["a.pdf"]["mtime"][10] == "T"
    assert any(mark in by["a.pdf"]["mtime"] for mark in ("+", "-"))
    assert by["a.pdf"]["copy_group"] == by["nested/a-copy.pdf"]["copy_group"]
    assert by["a.pdf"]["copy_group"].startswith("cg-")
    assert by["other.txt"]["copy_group"] is None
    assert doc["schema"] == "lam-tags/v1"
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == "lam-tags/v1"
    assert by["a.pdf"]["bin"] == "_Documents"
    assert by["a.pdf"]["confidence"] == "high"


def test_burst_window_and_parent_fallback(tmp_path: Path):
    root = tmp_path / "in"
    t0 = 1_700_000_000.0
    _write(root / "a.txt", b"a", t0)
    _write(root / "b.txt", b"b", t0 + 300)
    _write(root / "c.txt", b"c", t0 + 301 + 300)
    _write(root / "d.txt", b"d", t0 + 301 + 300 + 5_000)
    doc = run_tag_scan(root, out=tmp_path / "tags.json", no_model=True, burst_window=300)
    by = {item["rel"]: item for item in doc["files"]}
    assert by["a.txt"]["batch"] == by["b.txt"]["batch"]
    assert by["a.txt"]["batch"].startswith("burst:")
    assert by["c.txt"]["batch"].startswith("dir:")
    assert by["c.txt"]["batch"] == by["d.txt"]["batch"]
    assert by["c.txt"]["batch"] != by["a.txt"]["batch"]


def test_zip_batch_precedes_burst(tmp_path: Path):
    root = tmp_path / "in"
    t0 = 1_700_000_000.0
    _write(root / "Pack.zip", b"PK\x03\x04pack", t0)
    _write(root / "Pack" / "inside.txt", b"inside", t0)
    _write(root / "Pack.txt", b"sidecar", t0)
    _write(root / "loose-a.txt", b"la", t0)
    _write(root / "loose-b.txt", b"lb", t0 + 10)
    doc = run_tag_scan(root, out=tmp_path / "tags.json", no_model=True, burst_window=300)
    by = {item["rel"]: item for item in doc["files"]}
    assert by["Pack.zip"]["batch"].startswith("zip:")
    assert by["Pack/inside.txt"]["batch"] == by["Pack.zip"]["batch"]
    assert by["Pack.txt"]["batch"] == by["Pack.zip"]["batch"]
    assert by["loose-a.txt"]["batch"].startswith("burst:")
    assert by["loose-a.txt"]["batch"] == by["loose-b.txt"]["batch"]
    assert by["loose-a.txt"]["batch"] != by["Pack.zip"]["batch"]


def test_reparse_predicate_skips_junctions():
    class _Junction:
        def is_symlink(self) -> bool:
            return False

        def is_junction(self) -> bool:
            return True

    class _Plain:
        def is_symlink(self) -> bool:
            return False

        def is_junction(self) -> bool:
            return False

    assert _is_reparse(_Junction()) is True
    assert _is_reparse(_Plain()) is False


def test_symlink_is_not_followed(tmp_path: Path):
    root = tmp_path / "in"
    outside = tmp_path / "outside"
    _write(root / "keep.txt", b"yes")
    _write(outside / "secret.txt", b"nope")
    try:
        (root / "link.txt").symlink_to(outside / "secret.txt")
        (root / "linkdir").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink not permitted")
    doc = run_tag_scan(root, out=tmp_path / "tags.json", no_model=True)
    assert {item["rel"] for item in doc["files"]} == {"keep.txt"}


def test_refuses_overwrite_of_different_file(tmp_path: Path):
    root = tmp_path / "in"
    _write(root / "a.txt", b"one")
    out = tmp_path / "tags.json"
    run_tag_scan(root, out=out, no_model=True)
    _write(root / "b.txt", b"two")
    with pytest.raises(TagError, match="overwrite"):
        run_tag_scan(root, out=out, no_model=True)


def test_missing_folder(tmp_path: Path):
    with pytest.raises(TagError):
        run_tag_scan(tmp_path / "nope", out=tmp_path / "tags.json", no_model=True)
