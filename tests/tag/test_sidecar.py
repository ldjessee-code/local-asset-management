# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sidecar field names and ExifTool export (missing tool, and a fake runner)."""

from __future__ import annotations

import json
from pathlib import Path

from lam.tag.scan import run_tag_scan


def test_sidecar_field_names(tmp_path: Path):
    root = tmp_path / "in"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF-1.4\n")
    out = tmp_path / "tags.json"
    run_tag_scan(root, out=out, no_model=True)
    meta_path = tmp_path / "tags.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["schema"] == "lam-tag-sidecar/v1"
    record = meta["files"][0]
    for key in (
        "file",
        "dc:subject",
        "xmp:Label",
        "xmp:Rating",
        "lam:confidence",
        "lam:rule",
        "lam:model",
        "lam:batch",
        "lam:copy_group",
        "xmp:MetadataDate",
    ):
        assert key in record
    assert record["xmp:Label"] == "_Documents"
    assert record["dc:subject"] == ["_Documents"]
    assert record["lam:confidence"] == "high"
    assert record["xmp:Rating"] == 5
    assert record["lam:model"] == "disabled"
    assert record["lam:rule"] == "ext-pdf"


def test_xmp_missing_exiftool_warns_and_skips(tmp_path: Path, capsys):
    root = tmp_path / "in"
    root.mkdir()
    src = root / "a.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    before = src.read_bytes()
    run_tag_scan(
        root,
        out=tmp_path / "tags.json",
        no_model=True,
        xmp=True,
        exiftool_which=lambda _name: None,
    )
    err = capsys.readouterr().err
    assert "ExifTool" in err
    assert "skip" in err.lower()
    assert not (tmp_path / "tags.xmp").exists()
    assert src.read_bytes() == before


def test_xmp_fake_runner_writes_args_not_a_shell_string(tmp_path: Path):
    root = tmp_path / "in"
    root.mkdir()
    src = root / "a.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    before = src.read_bytes()
    seen: list[list[str]] = []

    def runner(args: list[str]) -> int:
        assert isinstance(args, list)
        assert all(isinstance(part, str) for part in args)
        seen.append(args)
        out_flag = args.index("-o")
        Path(args[out_flag + 1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[out_flag + 1]).write_text("<xmp/>", encoding="utf-8")
        return 0

    run_tag_scan(
        root,
        out=tmp_path / "tags.json",
        no_model=True,
        xmp=True,
        exiftool_which=lambda _name: r"C:\Tools\exiftool.exe",
        exiftool_runner=runner,
    )
    assert len(seen) == 1
    args = seen[0]
    assert args[0] == r"C:\Tools\exiftool.exe"
    assert "-o" in args
    dest = Path(args[args.index("-o") + 1])
    assert dest.is_relative_to(tmp_path / "tags.xmp")
    assert str(src.resolve()) in args or str(src) in args
    assert "-overwrite_original" not in args
    assert src.read_bytes() == before
    assert dest.read_text(encoding="utf-8") == "<xmp/>"
