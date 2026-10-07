# SPDX-License-Identifier: AGPL-3.0-or-later
"""Zip file names and the shared ``_Originals_Zipped`` destination."""

from __future__ import annotations

import csv
import zipfile
from pathlib import Path

from lam.oversize.engine import run_oversize_scan, zip_archive_name
from lam.oversize.register import load_register


def _recycle_into(bucket: list[Path]):
    def fake_recycle(path: Path) -> None:
        bucket.append(Path(path))
        Path(path).unlink()

    return fake_recycle


def test_zip_archive_name_spaces_extensions_and_collapsing():
    assert (
        zip_archive_name("MalatranTombEntrance_Night Light_Gridless.jpg")
        == "MalatranTombEntrance_Night_Light_Gridless.zip"
    )
    assert zip_archive_name("Night  Light.jpg") == "Night_Light.zip"
    assert zip_archive_name("Night___Light.jpg") == "Night_Light.zip"
    assert zip_archive_name("A  __ B.jpg") == "A_B.zip"
    assert zip_archive_name("Already_Clean.jpg") == "Already_Clean.zip"
    assert zip_archive_name("Map.png") == "Map.zip"
    assert zip_archive_name("Map.jpeg") == "Map.zip"
    assert zip_archive_name("Map.JPEG") == "Map.zip"
    assert zip_archive_name("Map.JPG") == "Map.zip"
    assert zip_archive_name("Map.tif") == "Map.zip"
    assert zip_archive_name("Map.TIFF") == "Map.zip"
    assert zip_archive_name(" Foo.jpg") == "Foo.zip"
    assert zip_archive_name("Foo .jpg") == "Foo.zip"
    assert zip_archive_name("   .jpg") == "image.zip"


def test_zip_collision_keeps_original_member_name(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    first = write_image(root / "Set" / "Foo Bar.jpg", 80, 40)
    second = write_image(root / "Set" / "Foo_Bar.png", 80, 40)
    recycled: list[Path] = []
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        apply=True,
        zip_mode="all",
        register=tmp_path / "reg.json",
        recycle=_recycle_into(recycled),
    )
    assert code == 0
    by_name = {Path(item["original_path"]).name: item for item in payload["files"]}
    foo_bar = Path(by_name["Foo Bar.jpg"]["zip_path"])
    foo_bar_2 = Path(by_name["Foo_Bar.png"]["zip_path"])
    assert foo_bar.name == "Foo_Bar.zip"
    assert foo_bar_2.name == "Foo_Bar_2.zip"
    assert foo_bar.parent == foo_bar_2.parent
    with zipfile.ZipFile(foo_bar) as handle:
        assert handle.namelist() == ["Foo Bar.jpg"]
    with zipfile.ZipFile(foo_bar_2) as handle:
        assert handle.namelist() == ["Foo_Bar.png"]
    assert first not in recycled or not first.exists()
    assert not second.exists()
    assert not first.exists()


def test_plan_zip_target_and_register_zip_path_use_cleaned_name(tmp_path: Path, write_image):
    root = tmp_path / "pack"
    source = write_image(root / "2019" / "Set" / "Night Light.jpg", 80, 40)
    plan = tmp_path / "plan.csv"
    register = tmp_path / "reg.json"
    dry, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        zip_mode="all",
        plan_out=plan,
        register=register,
    )
    assert code == 0
    assert dry["mode"] == "dry-run"
    assert not register.exists()
    with plan.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert Path(rows[0]["zip_target"]) == (
        root / "_Originals_Zipped" / "2019" / "Set" / "Night_Light.zip"
    ).resolve()
    assert " " not in Path(rows[0]["zip_target"]).name
    assert not rows[0]["zip_target"].lower().endswith(".jpg.zip")

    recycled: list[Path] = []
    applied, apply_code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        apply=True,
        zip_mode="all",
        register=register,
        recycle=_recycle_into(recycled),
    )
    assert apply_code == 0
    entry = load_register(register)["entries"][0]
    assert Path(entry["zip_path"]) == Path(rows[0]["zip_target"])
    assert Path(applied["files"][0]["zip_path"]) == Path(rows[0]["zip_target"])
    with zipfile.ZipFile(entry["zip_path"]) as handle:
        assert handle.namelist() == ["Night Light.jpg"]
    assert recycled == [source]


def test_default_zip_root_mirrors_under_originals_zipped(tmp_path: Path, write_image):
    root = tmp_path / "Party_of_Two"
    write_image(root / "2019" / "Set" / "Map.jpg", 80, 40)
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        zip_mode="all",
        plan_out=tmp_path / "plan.csv",
        register=tmp_path / "reg.json",
    )
    assert code == 0
    with (tmp_path / "plan.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert Path(row["zip_target"]) == (root / "_Originals_Zipped" / "2019" / "Set" / "Map.zip").resolve()
    assert payload["files"][0]["zip_path"] is None


def test_zip_root_from_subfolder_scan_still_mirrors_year(tmp_path: Path, write_image):
    library = tmp_path / "Party_of_Two"
    source_dir = library / "2019" / "Night Set" / "Gridless"
    write_image(source_dir / "MalatranTombEntrance_Night Light_Gridless.jpg", 80, 40)
    zip_root = library / "_Originals_Zipped"
    plan = tmp_path / "plan.csv"
    _payload, code = run_oversize_scan(
        library / "2019",
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        zip_mode="all",
        zip_root=zip_root,
        plan_out=plan,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    with plan.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    expected = (
        zip_root
        / "2019"
        / "Night Set"
        / "Gridless"
        / "MalatranTombEntrance_Night_Light_Gridless.zip"
    )
    assert Path(row["zip_target"]) == expected.resolve()
    assert not zip_root.exists()


def test_zip_refused_outside_zip_root_parent(tmp_path: Path, write_image):
    scan = tmp_path / "maps"
    original = write_image(scan / "Map.jpg", 80, 40)
    zip_root = tmp_path / "other" / "_Originals_Zipped"
    dry, dry_code = run_oversize_scan(
        scan,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        zip_mode="all",
        zip_root=zip_root,
        plan_out=tmp_path / "plan.csv",
        register=tmp_path / "dry.json",
    )
    assert dry_code == 0
    assert "outside the zip root parent" in (dry["files"][0]["error"] or "")
    with (tmp_path / "plan.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["zip_target"] == ""
    assert not zip_root.exists()
    assert original.is_file()

    recycled: list[Path] = []
    applied, apply_code = run_oversize_scan(
        scan,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        apply=True,
        zip_mode="all",
        zip_root=zip_root,
        register=tmp_path / "apply.json",
        recycle=_recycle_into(recycled),
    )
    assert apply_code == 4
    item = applied["files"][0]
    assert item["status"] == "error"
    assert "outside the zip root parent" in (item["error"] or "")
    assert item["zip_path"] is None
    assert item["original_recycled"] is False
    assert recycled == []
    assert original.is_file()
    assert not zip_root.exists()
    assert not list(zip_root.parent.rglob("*.zip"))


def test_walker_skips_originals_zipped(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "keep.jpg", 80, 40)
    write_image(root / "_Originals_Zipped" / "2019" / "hidden.jpg", 80, 40)
    write_image(root / "nested" / "_originals_zipped" / "also.jpg", 80, 40)
    write_image(root / "more" / "_ORIGINALS_ZIPPED" / "third.jpg", 80, 40)
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        register=tmp_path / "reg.json",
    )
    assert code == 0
    assert {Path(item["original_path"]).name for item in payload["files"]} == {"keep.jpg"}


def test_dry_run_does_not_create_zip_root(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "2019" / "Set" / "Night Light.jpg", 80, 40)
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    _payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        zip_mode="all",
        plan_out=tmp_path / "plan.csv",
        register=tmp_path / "reg.json",
    )
    assert code == 0
    after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    assert after == before
    assert not (root / "_Originals_Zipped").exists()


def test_apply_zip_under_root_verifies_and_recycles(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    original = write_image(root / "2019" / "Set" / "Map Day.jpg", 80, 40)
    recycled: list[Path] = []
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="set",
        apply=True,
        zip_mode="all",
        register=tmp_path / "reg.json",
        recycle=_recycle_into(recycled),
    )
    assert code == 0
    item = payload["files"][0]
    zip_path = Path(item["zip_path"])
    assert zip_path == (root / "_Originals_Zipped" / "2019" / "Set" / "Map_Day.zip").resolve()
    assert item["zip_verified"] is True
    assert item["original_recycled"] is True
    assert recycled == [original]
    assert not original.exists()
    with zipfile.ZipFile(zip_path) as handle:
        assert handle.testzip() is None
        assert handle.namelist() == ["Map Day.jpg"]
        assert handle.getinfo("Map Day.jpg").compress_type == zipfile.ZIP_STORED
    assert Path(item["proxy_path"]).is_file()
    assert Path(item["proxy_path"]).parent == original.parent
