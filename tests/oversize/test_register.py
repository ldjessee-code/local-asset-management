# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one central register: fields, schema, atomic .prev, no duplicates."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from lam.oversize.engine import run_oversize_scan
from lam.oversize.register import resolve_register_path
from lam.schema_lite import validate_json
from lam.schemas.registry import OVERSIZE_REGISTER_SCHEMA_ID, OVERSIZE_SCHEMAS

FIELDS = {
    "original_path",
    "sha256",
    "bytes",
    "mb",
    "width",
    "height",
    "megapixels",
    "combined",
    "combined_evidence",
    "parts_found",
    "part_paths",
    "parts_area_pct",
    "proxy_path",
    "proxy_width",
    "proxy_height",
    "zip_path",
    "zip_verified",
    "original_recycled",
    "flag",
    "status",
    "error",
    "first_seen",
    "updated",
    "paths_seen",
    "part_scope",
    "scale",
    "coverage_pct",
    "padding_px",
    "located_parts",
    "rejected_candidates",
    "duplicate_candidates",
    "missing_cells",
    "recreated_parts",
    "misnamed_parts",
    "confidence",
    "action",
}


def _record(root: Path, register: Path):
    return run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        register=register,
        record=True,
        part_scope="set",
    )


def test_record_writes_one_register_with_every_field(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_A_Day.jpg", 8, 8)
    register = tmp_path / "reg.json"
    _payload, code = _record(root, register)
    assert code == 0
    assert register.is_file()
    document = json.loads(register.read_text(encoding="utf-8"))
    assert document["schema"] == "lam-oversize-register/v1"
    assert len(document["entries"]) == 1
    entry = document["entries"][0]
    assert set(entry) == FIELDS
    assert len(entry["sha256"]) == 64
    assert entry["width"] == 80 and entry["height"] == 40
    assert entry["combined"] is True
    assert entry["parts_found"] == "partial"
    assert entry["flag"] == "large, revisit for subdivision"
    assert entry["status"] == "planned"
    assert entry["error"] is None
    assert entry["proxy_path"] is None
    assert entry["zip_verified"] is False
    assert entry["original_recycled"] is False
    assert entry["paths_seen"] == [entry["original_path"]]
    assert entry["first_seen"]
    assert entry["updated"]
    assert entry["part_scope"] == "set"
    assert entry["confidence"] is None
    assert entry["action"] == "stand-in only"
    assert entry["located_parts"] == []


def test_record_schema_valid(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Map_Day.jpg", 80, 40)
    register = tmp_path / "reg.json"
    _record(root, register)
    document = json.loads(register.read_text(encoding="utf-8"))
    schema = OVERSIZE_SCHEMAS[OVERSIZE_REGISTER_SCHEMA_ID].load_document()
    assert validate_json(document, schema) == []


def test_register_atomic_prev_copy(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "One_Day.jpg", 80, 40)
    register = tmp_path / "reg.json"
    prev = Path(str(register) + ".prev")
    _record(root, register)
    assert not prev.exists()
    first = register.read_text(encoding="utf-8")
    write_image(root / "Two_Day.jpg", 90, 40)
    _record(root, register)
    assert prev.is_file()
    assert prev.read_text(encoding="utf-8") == first
    second = json.loads(register.read_text(encoding="utf-8"))
    assert len(second["entries"]) == 2
    assert len(json.loads(first)["entries"]) == 1


def test_rerun_does_not_duplicate(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Map_Day.jpg", 80, 40)
    register = tmp_path / "reg.json"
    _record(root, register)
    _record(root, register)
    document = json.loads(register.read_text(encoding="utf-8"))
    assert len(document["entries"]) == 1
    assert len(document["entries"][0]["paths_seen"]) == 1


def test_moved_file_updates_path_and_keeps_history(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    source = write_image(root / "old" / "Map_Day.jpg", 80, 40)
    register = tmp_path / "reg.json"
    _record(root, register)
    dest = root / "new" / "Map_Day.jpg"
    dest.parent.mkdir()
    shutil.move(source, dest)
    _record(root, register)
    document = json.loads(register.read_text(encoding="utf-8"))
    assert len(document["entries"]) == 1
    entry = document["entries"][0]
    assert Path(entry["original_path"]) == dest.resolve()
    assert len(entry["paths_seen"]) == 2
    assert Path(entry["paths_seen"][-1]) == dest.resolve()
    assert "old" in entry["paths_seen"][0]


def test_register_path_from_flag_env_or_localappdata(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("LAM_OVERSIZE_REGISTER", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert resolve_register_path(None) == tmp_path / "lam" / "oversize-register.json"
    monkeypatch.setenv("LAM_OVERSIZE_REGISTER", str(tmp_path / "custom.json"))
    assert resolve_register_path(None) == Path(tmp_path / "custom.json")
    assert resolve_register_path(tmp_path / "flag.json") == Path(tmp_path / "flag.json")
