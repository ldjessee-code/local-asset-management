# SPDX-License-Identifier: AGPL-3.0-or-later
"""--apply builds proxies, verifies zips, and recycles only after a match."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from lam.oversize.engine import run_oversize_scan


def _scan_kwargs(register: Path, **extra):
    base = {
        "min_mb": 10_000,
        "min_mp": 0.0002,
        "max_dim": 40,
        "register": register,
        "apply": True,
        "zip_mode": "none",
        "part_scope": "set",
    }
    base.update(extra)
    return base


def test_apply_proxy_long_side_aspect_and_no_upscale(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "wide.jpg", 100, 40)
    write_image(root / "tall.jpg", 30, 90)
    write_image(root / "small.jpg", 20, 10)
    payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(tmp_path / "reg.json", min_mp=0.00001, max_dim=50),
    )
    assert code == 0
    by_name = {Path(item["original_path"]).name: item for item in payload["files"]}

    wide = by_name["wide.jpg"]
    assert wide["status"] == "done"
    assert (wide["proxy_width"], wide["proxy_height"]) == (50, 20)
    assert Path(wide["proxy_path"]).is_file()
    assert Path(wide["original_path"]).is_file()

    tall = by_name["tall.jpg"]
    assert (tall["proxy_width"], tall["proxy_height"]) == (17, 50) or (
        abs(tall["proxy_width"] - round(30 * 50 / 90)) <= 1 and tall["proxy_height"] == 50
    )

    small = by_name["small.jpg"]
    assert (small["proxy_width"], small["proxy_height"]) == (20, 10)


def test_apply_png_stays_png(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "plate.png", 60, 20)
    payload, code = run_oversize_scan(root, **_scan_kwargs(tmp_path / "reg.json", max_dim=30))
    assert code == 0
    item = payload["files"][0]
    proxy = Path(item["proxy_path"])
    assert proxy.suffix.lower() == ".png"
    assert proxy.read_bytes().startswith(b"\x89PNG")
    assert item["proxy_width"] == 30
    assert abs(item["proxy_height"] - 10) <= 1


def test_apply_tif_writes_jpeg_proxy(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "scan.tif", 80, 40)
    payload, code = run_oversize_scan(root, **_scan_kwargs(tmp_path / "reg.json"))
    assert code == 0
    proxy = Path(payload["files"][0]["proxy_path"])
    assert proxy.suffix.lower() == ".jpg"
    assert proxy.read_bytes().startswith(b"\xff\xd8")


def test_zip_verify_pass_recycles_once(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    original = write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_A_Day.jpg", 8, 8)
    write_image(root / "Set" / "Map_B_Day.jpg", 8, 8)
    recycled: list[Path] = []

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        Path.unlink(path)

    payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(tmp_path / "reg.json", zip_mode="combined-with-parts", recycle=fake_recycle),
    )
    assert code == 0
    item = payload["files"][0]
    assert item["status"] == "done"
    assert item["zip_verified"] is True
    assert item["original_recycled"] is True
    assert len(recycled) == 1
    assert recycled[0] == original
    assert not original.exists()
    assert Path(item["proxy_path"]).is_file()
    zip_path = Path(item["zip_path"])
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as handle:
        info = handle.getinfo("Map_Day.jpg")
        assert info.compress_type == zipfile.ZIP_STORED
        assert handle.namelist() == ["Map_Day.jpg"]
    assert (root / "Set" / "Map_A_Day.jpg").is_file()
    assert (root / "Set" / "Map_B_Day.jpg").is_file()


def test_corrupt_zip_leaves_original_and_exits_4(tmp_path: Path, write_image, monkeypatch):
    root = tmp_path / "maps"
    original = write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_A_Day.jpg", 8, 8)
    write_image(root / "Set" / "Map_B_Day.jpg", 8, 8)
    recycled: list[Path] = []

    def fake_testzip(self):
        return self.namelist()[0]

    monkeypatch.setattr(zipfile.ZipFile, "testzip", fake_testzip)
    payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(
            tmp_path / "reg.json",
            zip_mode="combined-with-parts",
            recycle=recycled.append,
        ),
    )
    assert code == 4
    item = payload["files"][0]
    assert item["status"] == "error"
    assert item["error"]
    assert item["original_recycled"] is False
    assert recycled == []
    assert original.is_file()


def test_apply_continues_after_one_error(tmp_path: Path, write_image, monkeypatch):
    root = tmp_path / "maps"
    good = write_image(root / "Good" / "Good_Day.jpg", 80, 40)
    write_image(root / "Good" / "Good_A_Day.jpg", 8, 8)
    write_image(root / "Good" / "Good_B_Day.jpg", 8, 8)
    bad = write_image(root / "Bad" / "Bad_Day.jpg", 80, 40)
    write_image(root / "Bad" / "Bad_A_Day.jpg", 8, 8)
    write_image(root / "Bad" / "Bad_B_Day.jpg", 8, 8)
    real_testzip = zipfile.ZipFile.testzip

    def selective(self):
        names = self.namelist()
        if any(name.startswith("Bad") for name in names):
            return names[0]
        return real_testzip(self)

    monkeypatch.setattr(zipfile.ZipFile, "testzip", selective)
    recycled: list[Path] = []

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        Path.unlink(path)

    payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(tmp_path / "reg.json", zip_mode="combined-with-parts", recycle=fake_recycle),
    )
    assert code == 4
    by_name = {Path(item["original_path"]).name: item for item in payload["files"]}
    assert by_name["Good_Day.jpg"]["status"] == "done"
    assert by_name["Bad_Day.jpg"]["status"] == "error"
    assert good not in recycled or recycled == [good]
    assert not good.exists()
    assert bad.is_file()
    assert recycled == [good]


def test_default_zip_mode_skips_partial(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    original = write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_F_Day.jpg", 8, 8)
    recycled: list[Path] = []
    payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(
            tmp_path / "reg.json",
            zip_mode="combined-with-parts",
            recycle=recycled.append,
        ),
    )
    assert code == 0
    item = payload["files"][0]
    assert item["parts_found"] == "partial"
    assert item["zip_path"] is None
    assert item["original_recycled"] is False
    assert original.is_file()
    assert recycled == []
    assert Path(item["proxy_path"]).is_file()


def test_never_overwrite_proxy_or_zip(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    original = write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_A_Day.jpg", 8, 8)
    write_image(root / "Set" / "Map_B_Day.jpg", 8, 8)
    blocker = root / "Set" / "Map_Day_max8000.jpg"
    blocker.write_bytes(b"keep-proxy")
    zip_blocker = root / "_Originals_Zipped" / "Set" / "Map_Day.zip"
    zip_blocker.parent.mkdir(parents=True, exist_ok=True)
    zip_blocker.write_bytes(b"keep-zip")
    recycled: list[Path] = []

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        Path.unlink(path)

    payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(
            tmp_path / "reg.json",
            max_dim=40,
            proxy_suffix="_max8000",
            zip_mode="combined-with-parts",
            recycle=fake_recycle,
        ),
    )
    assert code == 0
    item = payload["files"][0]
    assert blocker.read_bytes() == b"keep-proxy"
    assert zip_blocker.read_bytes() == b"keep-zip"
    assert Path(item["proxy_path"]).name == "Map_Day_max8000_2.jpg"
    assert Path(item["zip_path"]).name == "Map_Day_2.zip"
    assert not original.exists()


def test_no_hard_delete_only_injected_recycle(tmp_path: Path, write_image, monkeypatch):
    root = tmp_path / "maps"
    original = write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_A_Day.jpg", 8, 8)
    write_image(root / "Set" / "Map_B_Day.jpg", 8, 8)
    saved_unlink = os.unlink
    saved_remove = os.remove

    def protected(path: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            return False
        return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".zip"}

    def guard_unlink(self, *args, **kwargs):
        if protected(self):
            raise AssertionError(f"Path.unlink on {self}")
        return saved_unlink(self, *args, **kwargs)

    def guard_remove(path, *args, **kwargs):
        if protected(Path(path)):
            raise AssertionError(f"os.remove on {path}")
        return saved_remove(path, *args, **kwargs)

    def guard_rmtree(*args, **kwargs):
        raise AssertionError(f"shutil.rmtree {args!r}")

    monkeypatch.setattr(Path, "unlink", guard_unlink)
    monkeypatch.setattr(os, "remove", guard_remove)
    monkeypatch.setattr(os, "unlink", guard_remove)
    monkeypatch.setattr(shutil, "rmtree", guard_rmtree)
    recycled: list[Path] = []

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        saved_unlink(path)

    _payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(tmp_path / "reg.json", zip_mode="combined-with-parts", recycle=fake_recycle),
    )
    assert code == 0
    assert recycled == [original]
    assert not original.exists()


def test_apply_is_idempotent(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    original = write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_A_Day.jpg", 8, 8)
    write_image(root / "Set" / "Map_B_Day.jpg", 8, 8)
    register = tmp_path / "reg.json"
    recycled: list[Path] = []

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        Path.unlink(path)

    first, code1 = run_oversize_scan(
        root,
        **_scan_kwargs(register, zip_mode="combined-with-parts", recycle=fake_recycle),
    )
    proxy = Path(first["files"][0]["proxy_path"])
    proxy_bytes = proxy.read_bytes()
    zip_path = Path(first["files"][0]["zip_path"])
    second, code2 = run_oversize_scan(
        root,
        **_scan_kwargs(register, zip_mode="combined-with-parts", recycle=fake_recycle),
    )
    assert code1 == 0 and code2 == 0
    assert recycled == [original]
    assert proxy.read_bytes() == proxy_bytes
    assert zip_path.is_file()
    document = json.loads(register.read_text(encoding="utf-8"))
    assert len(document["entries"]) == 1
    assert second["files"] == []


def test_sha256_error_on_second_file_keeps_the_first(tmp_path: Path, write_image, monkeypatch, capsys):
    root = tmp_path / "maps"
    write_image(root / "a_big.jpg", 80, 40)
    write_image(root / "b_big.jpg", 80, 40)
    register = tmp_path / "reg.json"
    from lam.hashing import sha256_full as real_sha

    calls = {"n": 0}

    def flaky(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("locked")
        return real_sha(path, *args, **kwargs)

    monkeypatch.setattr("lam.oversize.engine.sha256_full", flaky)
    payload, code = run_oversize_scan(root, **_scan_kwargs(register))
    assert code == 4
    assert payload["errors"] >= 1
    document = json.loads(register.read_text(encoding="utf-8"))
    done = [entry for entry in document["entries"] if entry["status"] == "done"]
    errored = [entry for entry in document["entries"] if entry["status"] == "error"]
    assert len(done) == 1
    assert errored
    assert errored[0]["error"].startswith("OSError:")
    assert "locked" in errored[0]["error"]
    finals = [line for line in capsys.readouterr().err.splitlines() if line.startswith("FINAL ")]
    assert len(finals) == 1
    assert "command=oversize-scan" in finals[0]
    assert "failed=" in finals[0]


def test_register_checkpoint_keeps_first_applied_entry(tmp_path: Path, write_image, monkeypatch):
    root = tmp_path / "maps"
    write_image(root / "a_big.jpg", 80, 40)
    write_image(root / "b_big.jpg", 80, 40)
    register = tmp_path / "reg.json"
    from lam.oversize.register import save_register as real_save

    def wrapped(path, document):
        real_save(path, document)
        raise RuntimeError("killed after first checkpoint")

    monkeypatch.setattr("lam.oversize.engine.save_register", wrapped)
    with pytest.raises(RuntimeError, match="killed after first checkpoint"):
        run_oversize_scan(root, **_scan_kwargs(register))
    document = json.loads(register.read_text(encoding="utf-8"))
    assert any(entry["status"] == "done" for entry in document["entries"])


def test_interrupted_zip_leaves_no_final_zip(tmp_path: Path, write_image, monkeypatch):
    root = tmp_path / "maps"
    original = write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_A_Day.jpg", 8, 8)
    write_image(root / "Set" / "Map_B_Day.jpg", 8, 8)

    def boom(self, *args, **kwargs):
        raise OSError("killed during zip")

    monkeypatch.setattr(zipfile.ZipFile, "write", boom)
    payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(
            tmp_path / "reg.json",
            zip_mode="combined-with-parts",
            recycle=lambda path: (_ for _ in ()).throw(AssertionError(f"recycled {path}")),
        ),
    )
    assert code == 4
    assert payload["files"][0]["status"] == "error"
    assert original.is_file()
    assert list(root.rglob("*.zip")) == []
    assert list(root.rglob("*.tmp")) == []


def test_preexisting_zip_of_different_size_is_not_overwritten(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    original = write_image(root / "Set" / "Map_Day.jpg", 80, 40)
    write_image(root / "Set" / "Map_A_Day.jpg", 8, 8)
    write_image(root / "Set" / "Map_B_Day.jpg", 8, 8)
    blocker = root / "_Originals_Zipped" / "Set" / "Map_Day.zip"
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(b"keep-zip-different-size")
    from lam.oversize.engine import _write_zip

    with pytest.raises(FileExistsError):
        _write_zip(original, blocker, "ab" * 32)
    assert blocker.read_bytes() == b"keep-zip-different-size"

    recycled: list[Path] = []

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        Path.unlink(path)

    payload, code = run_oversize_scan(
        root,
        **_scan_kwargs(tmp_path / "reg.json", zip_mode="combined-with-parts", recycle=fake_recycle),
    )
    assert code == 0
    assert blocker.read_bytes() == b"keep-zip-different-size"
    assert Path(payload["files"][0]["zip_path"]).name == "Map_Day_2.zip"
    assert not original.exists()


def test_interrupted_proxy_leaves_no_final_proxy(tmp_path: Path, write_image, monkeypatch):
    root = tmp_path / "maps"
    original = write_image(root / "wide.jpg", 80, 40)

    real_replace = os.replace

    def boom(src, dst):
        if Path(dst).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
            raise OSError("killed during proxy")
        return real_replace(src, dst)

    monkeypatch.setattr("lam.oversize.images.os.replace", boom)
    payload, code = run_oversize_scan(root, **_scan_kwargs(tmp_path / "reg.json"))
    assert code == 4
    assert payload["errors"] >= 1
    assert original.is_file()
    proxies = [path for path in root.rglob("*") if path.is_file() and path != original]
    assert proxies == []
    assert list(root.rglob("*.tmp")) == []
