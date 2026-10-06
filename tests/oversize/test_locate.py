# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase 2: pack search, location, missing parts, previews, register fields."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from lam.oversize.engine import run_oversize_scan
from lam.oversize.errors import OversizeError
from lam.oversize.images import read_dimensions
from lam.oversize.register import load_register
from lam.schema_lite import validate_json
from lam.schemas.registry import OVERSIZE_REGISTER_SCHEMA_ID, OVERSIZE_SCHEMAS


def _texture(width: int, height: int, seed: int = 1):
    """Unique noise plus a mild ramp.

    A ramp alone correlates with itself everywhere. Noise has to dominate
    so one cell has one peak. ``perlin`` in this libvips is about 0..1, which
    is invisible next to the ramp, so the base is ``gaussnoise``.
    """
    pyvips = pytest.importorskip("pyvips")
    noise = pyvips.Image.gaussnoise(width, height, sigma=48, mean=80 + seed).cast("float")
    xyz = pyvips.Image.xyz(width, height)
    gx = xyz.extract_band(0).cast("float") * (24.0 / max(width, 1))
    gy = xyz.extract_band(1).cast("float") * (16.0 / max(height, 1))
    # A light blur keeps a half-resolution part recognisable. Raw noise does
    # not survive a 0.5 resize, and a heavy blur makes every shift look alike.
    image = (noise + gx + gy).gaussblur(1.2).cast("uchar")
    if image.bands == 1:
        image = image.bandjoin([image, image])
    return image


def _other_texture(width: int, height: int):
    pyvips = pytest.importorskip("pyvips")
    image = pyvips.Image.gaussnoise(width, height, sigma=60, mean=140)
    image = image.cast("uchar")
    if image.bands == 1:
        image = image.bandjoin([image, image])
    return image


def _save(image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".png":
        image.pngsave(str(path))
    else:
        image.jpegsave(str(path), Q=90)
    return path


def _flat(width: int, height: int, value: int = 40):
    pyvips = pytest.importorskip("pyvips")
    band = pyvips.Image.black(width, height).add(value)
    return band.bandjoin([band, band])


def _by_name(payload: dict) -> dict[str, dict]:
    return {Path(item["original_path"]).name: item for item in payload["files"]}


def _snapshot(root: Path) -> list[tuple[str, int]]:
    rows = []
    for path in root.rglob("*"):
        if path.is_file():
            rows.append((str(path.resolve()), path.stat().st_size))
    return sorted(rows)


def _mean_abs(left: Path, right: Path) -> float:
    pyvips = pytest.importorskip("pyvips")
    first = pyvips.Image.new_from_file(str(left))
    second = pyvips.Image.new_from_file(str(right))
    if first.width != second.width or first.height != second.height:
        return 999.0
    return float((first.cast("float") - second.cast("float")).abs().avg())


def test_pack_scope_finds_other_sets_and_set_scope_does_not(tmp_path: Path, write_image):
    root = tmp_path / "pack"
    write_image(root / "Set01" / "HD_Combined" / "Citadel_Combined_Day.png", 80, 40)
    write_image(root / "Set04" / "HD" / "Citadel_D" / "Gridless" / "Citadel_D_Day.png", 8, 8)
    kwargs = {"min_mb": 10_000, "min_mp": 0.0002, "set_depth": 1}
    pack, _ = run_oversize_scan(root, register=tmp_path / "pack.json", part_scope="pack", locate=False, **kwargs)
    narrow, _ = run_oversize_scan(root, register=tmp_path / "set.json", part_scope="set", **kwargs)
    pack_item = _by_name(pack)["Citadel_Combined_Day.png"]
    set_item = _by_name(narrow)["Citadel_Combined_Day.png"]
    assert {Path(path).name for path in pack_item["part_paths"]} == {"Citadel_D_Day.png"}
    assert set_item["part_paths"] == []
    assert pack_item["part_scope"] == "pack"
    assert set_item["part_scope"] == "set"


def test_duplicate_candidates_collapse_to_one_marker(tmp_path: Path, write_image):
    root = tmp_path / "pack"
    write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    big = write_image(root / "Set" / "A" / "Map_A_Day.jpg", 20, 20)
    small = write_image(root / "Set" / "nested" / "deep" / "Map_A_Day.jpg", 8, 8)
    short = write_image(root / "Set" / "Map_B_Day.jpg", 10, 10)
    longer = write_image(root / "Set" / "nested" / "folder" / "Map_B_Day.jpg", 10, 10)
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        part_scope="pack",
        locate=False,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    item = _by_name(payload)["Map_Day.jpg"]
    kept = {Path(path).resolve() for path in item["part_paths"]}
    assert big.resolve() in kept
    assert short.resolve() in kept
    assert small.resolve() not in kept
    assert longer.resolve() not in kept
    dup_paths = {Path(row["path"]).resolve() for row in item["duplicate_candidates"]}
    assert small.resolve() in dup_paths
    assert longer.resolve() in dup_paths
    assert len(item["part_paths"]) == 2


def test_location_offset_and_scale_for_full_and_half_resolution(tmp_path: Path):
    pyvips = pytest.importorskip("pyvips")
    root = tmp_path / "maps"

    def _place(name: str, scale: float, origin: tuple[int, int], box: tuple[int, int]):
        width, height = 640, 400
        image = _texture(width, height, seed=3)
        x, y = origin
        bw, bh = box
        crop = image.crop(x, y, bw, bh)
        part = crop.resize(scale) if scale != 1 else crop
        _save(image, root / name / "HD_Combined" / "Hall_Combined_Day.png")
        _save(part, root / name / "Other" / "Hall_A_Day.png")
        _save(image.crop(x, y + bh + 10, bw, bh).resize(scale) if y + bh + 10 + bh <= height else part, root / name / "Other" / "unused.png")
        return (x, y, scale)

    # Two separate trees so each combined is its own scan root.
    image = _texture(640, 400, seed=4)
    full = image.crop(50, 40, 180, 120)
    half_src = image.crop(280, 60, 180, 120)
    half = half_src.resize(0.5)
    _save(image, root / "HD_Combined" / "Hall_Combined_Day.png")
    _save(full, root / "SetA" / "Hall_A_Day.png")
    _save(half, root / "SetB" / "Hall_B_Day.png")
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.05,
        part_scope="pack",
        register=tmp_path / "reg.json",
    )
    assert code == 0
    item = _by_name(payload)["Hall_Combined_Day.png"]
    by_marker = {part["marker"]: part for part in item["located_parts"]}
    assert set(by_marker) == {"a", "b"}, [
        (Path(row["path"]).name, row["reason"], row["score"])
        for row in item.get("rejected_candidates", [])
    ]
    assert abs(by_marker["a"]["x"] - 50) <= 2
    assert abs(by_marker["a"]["y"] - 40) <= 2
    assert abs(by_marker["b"]["x"] - 280) <= 2
    assert abs(by_marker["b"]["y"] - 60) <= 2
    assert item["scale"] is not None
    # Shared scale. The half-resolution part is the smaller file, so the
    # anchor is the full-resolution part and r stays near 1. Check B's box
    # in combined pixels, which is the unscaled crop, and a dedicated r=0.5 scan.
    assert abs(item["scale"] - 1.0) / 1.0 <= 0.01
    assert abs(by_marker["a"]["w"] - 180) <= 2

    half_root = tmp_path / "half"
    _save(image, half_root / "HD_Combined" / "Hall_Combined_Day.png")
    _save(half, half_root / "SetB" / "Hall_A_Day.png")
    half_payload, _ = run_oversize_scan(
        half_root,
        min_mb=10_000,
        min_mp=0.05,
        part_scope="pack",
        register=tmp_path / "half.json",
    )
    half_item = _by_name(half_payload)["Hall_Combined_Day.png"]
    located = half_item["located_parts"]
    assert len(located) == 1
    assert abs(located[0]["x"] - 280) <= 2
    assert abs(located[0]["y"] - 60) <= 2
    assert abs(half_item["scale"] - 0.5) / 0.5 <= 0.01
    _ = pyvips


def test_name_match_outside_the_image_is_rejected(tmp_path: Path):
    root = tmp_path / "maps"
    image = _texture(480, 320, seed=2)
    _save(image, root / "Combined" / "Hall_Combined_Day.png")
    _save(image.crop(20, 20, 120, 80), root / "Set07" / "Hall_A_Day.png")
    _save(image.crop(180, 20, 120, 80), root / "Set08" / "Hall_B_Day.png")
    foreign = _other_texture(120, 80)
    _save(foreign, root / "Set09" / "Hall_C_Day.png")
    payload, _code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.04,
        part_scope="pack",
        register=tmp_path / "reg.json",
    )
    item = _by_name(payload)["Hall_Combined_Day.png"]
    assert {part["marker"] for part in item["located_parts"]} == {"a", "b"}, (
        item.get("confidence"),
        item.get("scale"),
        item.get("located_parts"),
        [
            (Path(row["path"]).name, row["reason"], row["score"])
            for row in item.get("rejected_candidates", [])
        ],
    )
    rejected = {Path(row["path"]).name: row for row in item["rejected_candidates"]}
    assert "Hall_C_Day.png" in rejected
    assert "min-score" in rejected["Hall_C_Day.png"]["reason"]
    assert rejected["Hall_C_Day.png"]["score"] < 0.90


def test_complete_located_set_zips_like_phase1(tmp_path: Path):
    root = tmp_path / "maps"
    cell = 80
    image = _texture(cell * 2, cell * 2, seed=5)
    _save(image, root / "Set0" / "HD_Combined" / "Hall_Combined_Day.png")
    for index, letter in enumerate("AB"):
        # 2x2, both rows, so the letter order is unique. A B / C D.
        pass
    letters = "ABCD"
    for index, letter in enumerate(letters):
        col, row = index % 2, index // 2
        crop = image.crop(col * cell, row * cell, cell, cell)
        folder = root / f"Set{row + 1}" / f"Hall_{letter}" / "Gridless"
        _save(crop, folder / f"Hall_{letter}_Day.png")
    original = root / "Set0" / "HD_Combined" / "Hall_Combined_Day.png"
    recycled: list[Path] = []

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        Path.unlink(path)

    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.02,
        max_dim=40,
        part_scope="pack",
        apply=True,
        zip_mode="combined-with-parts",
        register=tmp_path / "reg.json",
        recycle=fake_recycle,
    )
    assert code == 0
    item = _by_name(payload)["Hall_Combined_Day.png"]
    assert item["parts_found"] == "yes"
    assert item["action"] == "overview + zip"
    assert item["confidence"] == "confident"
    assert item["zip_verified"] is True
    assert item["original_recycled"] is True
    assert recycled == [original]
    assert not original.exists()
    assert Path(item["proxy_path"]).is_file()
    assert Path(item["zip_path"]).is_file()


def test_recreate_missing_letter_on_a_non_a_lattice(tmp_path: Path):
    root = tmp_path / "maps"
    cell_w, cell_h = 64, 48
    cols, rows = 5, 3
    image = _texture(cols * cell_w, rows * cell_h, seed=6)
    letters = [chr(ord("f") + index) for index in range(cols * rows)]
    assert letters[0] == "f" and letters[-1] == "t" and "m" in letters
    truth = tmp_path / "truth"
    for index, letter in enumerate(letters):
        col, row = index % cols, index // cols
        crop = image.crop(col * cell_w, row * cell_h, cell_w, cell_h)
        folder = root / f"Set{row + 1}" / "HD" / f"Zone_{letter.upper()}" / "Gridless"
        dest = folder / f"A2_Zone_{letter.upper()}_Day.png"
        if letter == "m":
            _save(crop, truth / "A2_Zone_M_Day.png")
            continue
        _save(crop, dest)
    original = _save(image, root / "Set0" / "HD_Combined" / "Zone_Combined_Day.png")
    recycled: list[Path] = []

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        Path.unlink(path)

    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.02,
        max_dim=48,
        part_scope="pack",
        apply=True,
        zip_mode="combined-with-parts",
        register=tmp_path / "reg.json",
        recycle=fake_recycle,
    )
    assert code == 0, payload["files"][0].get("error")
    item = _by_name(payload)["Zone_Combined_Day.png"]
    assert item["confidence"] == "confident"
    assert item["action"] == "recreate 1 parts + overview + zip"
    assert item["parts_found"] == "yes"
    assert len(item["recreated_parts"]) == 1
    made = item["recreated_parts"][0]
    assert made["marker"] == "m"
    assert Path(made["path"]).name == "A2_Zone_M_Day.png"
    assert "Zone_M" in made["path"]
    assert "Gridless" in made["path"]
    assert (made["width"], made["height"]) == (cell_w, cell_h)
    assert made["verify_score"] >= 0.98
    assert _mean_abs(Path(made["path"]), truth / "A2_Zone_M_Day.png") < 5
    assert item["zip_verified"] is True
    assert recycled == [original]
    assert not original.exists()
    assert Path(item["proxy_path"]).is_file()
    missing = item["missing_cells"]
    assert len(missing) == 1
    assert missing[0]["marker"] == "m"


def test_ambiguous_letter_order_recreates_nothing(tmp_path: Path):
    root = tmp_path / "maps"
    image = _texture(400, 200, seed=7)
    _save(image, root / "Combined" / "Hall_Combined_Day.png")
    _save(image.crop(0, 0, 100, 100), root / "Set" / "Hall_A" / "Hall_A_Day.png")
    _save(image.crop(100, 0, 100, 100), root / "Set" / "Hall_B" / "Hall_B_Day.png")
    original = root / "Combined" / "Hall_Combined_Day.png"
    before_names = {path.name for path in root.rglob("*.png")}
    recycled: list[Path] = []
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.02,
        max_dim=40,
        part_scope="pack",
        apply=True,
        zip_mode="combined-with-parts",
        register=tmp_path / "reg.json",
        recycle=recycled.append,
    )
    assert code == 0
    item = _by_name(payload)["Hall_Combined_Day.png"]
    assert item["parts_found"] == "partial"
    assert item["confidence"].startswith("not confident:")
    assert "ambiguous" in item["confidence"]
    assert item["action"] == "stand-in only"
    assert item["recreated_parts"] == []
    assert item["zip_path"] is None
    assert original.is_file()
    assert recycled == []
    after = {path.name for path in root.rglob("*") if path.is_file() and "max8000" not in path.name}
    assert after == before_names


def test_flat_part_is_below_min_score(tmp_path: Path):
    root = tmp_path / "maps"
    image = _texture(360, 240, seed=8)
    _save(image, root / "Combined" / "Hall_Combined_Day.png")
    _save(image.crop(30, 40, 100, 80), root / "Set" / "Hall_A_Day.png")
    _save(_flat(100, 80, 30), root / "Set" / "Hall_B_Day.png")
    payload, _code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.03,
        part_scope="pack",
        register=tmp_path / "reg.json",
    )
    item = _by_name(payload)["Hall_Combined_Day.png"]
    assert {part["marker"] for part in item["located_parts"]} == {"a"}
    rejected = {Path(row["path"]).name: row for row in item["rejected_candidates"]}
    assert "Hall_B_Day.png" in rejected
    assert "min-score" in rejected["Hall_B_Day.png"]["reason"]
    assert rejected["Hall_B_Day.png"]["score"] < 0.90


def test_misnamed_part_is_detected_and_not_recreated(tmp_path: Path):
    root = tmp_path / "maps"
    cell = 90
    image = _texture(cell * 2, cell * 2, seed=9)
    _save(image, root / "Set0" / "HD_Combined" / "Swamp_Combined_Day.png")
    letters = "ABCD"
    misnamed = None
    for index, letter in enumerate(letters):
        col, row = index % 2, index // 2
        crop = image.crop(col * cell, row * cell, cell, cell)
        folder = root / "Set1" / "A2" / f"Swamp_{letter}" / "Gridless"
        if letter == "C":
            misnamed = _save(crop, folder / "Swamp_Day.png")
            continue
        _save(crop, folder / f"Swamp_{letter}_Day.png")
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.02,
        part_scope="pack",
        register=tmp_path / "reg.json",
    )
    assert code == 0
    item = _by_name(payload)["Swamp_Combined_Day.png"]
    assert item["recreated_parts"] == []
    assert len(item["misnamed_parts"]) == 1
    assert Path(item["misnamed_parts"][0]["path"]) == misnamed.resolve()
    assert item["misnamed_parts"][0]["marker"] == "c"
    assert not (root / "Set1" / "A2" / "Swamp_C" / "Gridless" / "Swamp_C_Day.png").exists()
    assert item["parts_found"] == "yes"
    assert item["action"] == "overview + zip"


def test_dry_run_previews_and_plan_do_not_touch_the_tree(tmp_path: Path):
    root = tmp_path / "maps"
    cell_w, cell_h = 64, 48
    image = _texture(cell_w * 2, cell_h * 2, seed=10)
    # 2x2 with D missing so a recreate preview is planned. Letters A-C located,
    # D is the hole. 2x2 row-major is unique.
    _save(image, root / "Set0" / "Zone_Combined_Day.png")
    for index, letter in enumerate("ABC"):
        col, row = index % 2, index // 2
        # D is index 3 (col 1, row 1). A B are row 0, C is col 0 row 1.
        crop = image.crop(col * cell_w, row * cell_h, cell_w, cell_h)
        folder = root / f"Set{row + 1}" / f"Zone_{letter}" / "Gridless"
        _save(crop, folder / f"Zone_{letter}_Day.png")
    before = _snapshot(root)
    preview = tmp_path / "previews"
    plan = tmp_path / "plan.csv"
    register = tmp_path / "reg.json"
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.005,
        part_scope="pack",
        preview_dir=preview,
        plan_out=plan,
        register=register,
    )
    assert code == 0
    assert payload["mode"] == "dry-run"
    assert _snapshot(root) == before
    assert not register.exists()
    overviews = list(preview.glob("*overview*.jpg"))
    recreates = list(preview.glob("*recreate*.jpg"))
    assert overviews
    assert recreates
    for path in overviews:
        width, height = read_dimensions(path)
        assert max(width, height) <= 1600
    for path in recreates:
        width, height = read_dimensions(path)
        assert max(width, height) <= 1024
    with plan.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert rows[0]["standin_mb_note"] == "estimate from pixel ratio"
    assert "d" in rows[0]["recreate_markers"]
    assert rows[0]["action"].startswith("recreate ")
    sentinel = plan.read_text(encoding="utf-8")
    with pytest.raises(OversizeError):
        run_oversize_scan(
            root,
            min_mb=10_000,
            min_mp=0.005,
            part_scope="pack",
            plan_out=plan,
            register=tmp_path / "other.json",
        )
    assert plan.read_text(encoding="utf-8") == sentinel
    inside = root / "previews"
    with pytest.raises(OversizeError):
        run_oversize_scan(
            root,
            min_mb=10_000,
            min_mp=0.005,
            part_scope="pack",
            preview_dir=inside,
            register=tmp_path / "inside.json",
        )
    assert _snapshot(root) == before
    assert not inside.exists()


def test_never_overwrite_a_recreated_target(tmp_path: Path):
    root = tmp_path / "maps"
    cell = 80
    image = _texture(cell * 2, cell * 2, seed=11)
    _save(image, root / "Set0" / "Hall_Combined_Day.png")
    for index, letter in enumerate("ABC"):
        col, row = index % 2, index // 2
        crop = image.crop(col * cell, row * cell, cell, cell)
        _save(crop, root / "Set1" / f"Hall_{letter}" / "Gridless" / f"Hall_{letter}_Day.png")
    dry, _ = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.02,
        part_scope="pack",
        register=tmp_path / "dry.json",
    )
    item = _by_name(dry)["Hall_Combined_Day.png"]
    assert item["confidence"] == "confident"
    target = Path(item["missing_cells"][0]["target_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"SENTINEL")
    original = root / "Set0" / "Hall_Combined_Day.png"
    recycled: list[Path] = []
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.02,
        part_scope="pack",
        apply=True,
        register=tmp_path / "reg.json",
        recycle=recycled.append,
    )
    assert target.read_bytes() == b"SENTINEL"
    assert original.is_file()
    assert recycled == []
    applied = _by_name(payload)["Hall_Combined_Day.png"]
    assert applied["status"] == "error"
    assert "target exists" in (applied["error"] or "")
    assert applied["recreated_parts"] == []
    assert not list(root.rglob("*_max8000*"))
    assert code == 4


def test_register_fields_schema_and_v1_still_loads(tmp_path: Path):
    root = tmp_path / "maps"
    cell = 70
    image = _texture(cell * 2, cell * 2, seed=12)
    _save(image, root / "Hall_Combined_Day.png")
    for index, letter in enumerate("ABCD"):
        col, row = index % 2, index // 2
        _save(
            image.crop(col * cell, row * cell, cell, cell),
            root / "parts" / f"Hall_{letter}_Day.png",
        )
    register = tmp_path / "reg.json"
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.01,
        part_scope="pack",
        record=True,
        register=register,
    )
    assert code == 0
    document = json.loads(register.read_text(encoding="utf-8"))
    schema = OVERSIZE_SCHEMAS[OVERSIZE_REGISTER_SCHEMA_ID].load_document()
    assert validate_json(document, schema) == []
    entry = document["entries"][0]
    for key in (
        "part_scope",
        "scale",
        "coverage_pct",
        "located_parts",
        "rejected_candidates",
        "duplicate_candidates",
        "missing_cells",
        "recreated_parts",
        "misnamed_parts",
        "confidence",
        "action",
    ):
        assert key in entry
    assert entry["part_scope"] == "pack"
    assert entry["parts_found"] == "yes"
    assert entry["action"] == "overview + zip"
    assert entry["located_parts"]
    assert {"path", "marker", "x", "y", "w", "h", "score", "margin"} <= set(entry["located_parts"][0])
    item = _by_name(payload)["Hall_Combined_Day.png"]
    assert item["action"] == "overview + zip"

    legacy = json.loads(register.read_text(encoding="utf-8"))
    for key in (
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
    ):
        del legacy["entries"][0][key]
    legacy_path = tmp_path / "v1.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = load_register(legacy_path)
    assert loaded["schema"] == "lam-oversize-register/v1"
    assert loaded["entries"][0]["sha256"] == entry["sha256"]
