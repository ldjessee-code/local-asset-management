# SPDX-License-Identifier: AGPL-3.0-or-later
"""lam oversize list reads the register and can export CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from lam.cli import main
from lam.oversize.engine import run_oversize_scan


def test_list_csv_export(tmp_path: Path, write_image, capsys):
    root = tmp_path / "maps"
    write_image(root / "Map_Day.jpg", 80, 40)
    register = tmp_path / "reg.json"
    run_oversize_scan(root, min_mb=10_000, min_mp=0.0002, register=register, record=True)
    out = tmp_path / "out.csv"
    with pytest.raises(SystemExit) as caught:
        main(["oversize", "list", "--register", str(register), "--csv", str(out)])
    assert caught.value.code == 0
    assert out.is_file()
    with out.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["status"] == "planned"
    assert rows[0]["flag"] == "large, revisit for subdivision"
    assert Path(rows[0]["original_path"]).name == "Map_Day.jpg"
    assert "sha256" in rows[0]


def test_list_csv_refuses_overwrite(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Map_Day.jpg", 80, 40)
    register = tmp_path / "reg.json"
    run_oversize_scan(root, min_mb=10_000, min_mp=0.0002, register=register, record=True)
    out = tmp_path / "out.csv"
    out.write_text("SENTINEL", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["oversize", "list", "--register", str(register), "--csv", str(out)])
    assert caught.value.code == 2
    assert out.read_text(encoding="utf-8") == "SENTINEL"


def test_list_status_filter_and_json(tmp_path: Path, write_image, capsys):
    root = tmp_path / "maps"
    write_image(root / "One_Day.jpg", 80, 40)
    write_image(root / "Two_Day.jpg", 90, 40)
    register = tmp_path / "reg.json"
    run_oversize_scan(root, min_mb=10_000, min_mp=0.0002, register=register, record=True)
    document = json.loads(register.read_text(encoding="utf-8"))
    document["entries"][0]["status"] = "error"
    document["entries"][0]["error"] = "boom"
    register.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["oversize", "list", "--register", str(register), "--status", "error", "--json"])
    assert caught.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["status"] == "error"
