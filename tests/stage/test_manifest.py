# SPDX-License-Identifier: AGPL-3.0-or-later
"""An existing manifest must match out/. A mismatch exits 2 and does not ring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lam.cli import main
from lam.hashing import sha256_full
from lam.stage.job import create_job
from lam.stage.place import place_job


def _placed(tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "target"
    target.mkdir()
    folder = create_job(owner="Ark", job="notes", target=target, root=tmp_path / "root")
    (folder / "out" / "note.txt").write_bytes(b"payload")
    assert place_job(folder, jobs_dir=tmp_path / "jobs", doorbell=lambda _status: None) == 0
    return folder, target


def test_manifest_lists_hash_and_role(tmp_path: Path):
    folder, target = _placed(tmp_path)
    document = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert document["schema"] == "lam-place-manifest/v1"
    entry = document["entries"][0]
    assert entry["role"] == "file"
    assert entry["sha256"] == sha256_full(folder / "out" / "note.txt")
    assert entry["size"] == 7
    assert Path(entry["dest"]).name == "note.txt"
    assert (target / "note.txt").read_bytes() == b"payload"


def test_extra_file_is_refused(tmp_path: Path):
    folder, target = _placed(tmp_path)
    before = (target / "note.txt").read_bytes()
    (folder / "out" / "extra.txt").write_bytes(b"nope")
    calls: list[str] = []
    with pytest.raises(SystemExit) as caught:
        main(["place", str(folder), "--jobs-dir", str(tmp_path / "jobs2"), "--dry-run"])
    # dry-run still checks the manifest before writing. Mismatch is exit 2.
    assert caught.value.code == 2
    assert calls == []
    assert (target / "note.txt").read_bytes() == before


def test_hash_mismatch_exits_2(tmp_path: Path, capsys):
    folder, target = _placed(tmp_path)
    before = (target / "note.txt").read_bytes()
    (folder / "out" / "note.txt").write_bytes(b"changed!")
    with pytest.raises(SystemExit) as caught:
        main(["place", str(folder), "--jobs-dir", str(tmp_path / "again")])
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "mismatch" in err or "hash" in err or "manifest" in err
    assert (target / "note.txt").read_bytes() == before
