# SPDX-License-Identifier: AGPL-3.0-or-later
"""Name warnings, kind patterns, and --strict."""

from __future__ import annotations

from pathlib import Path

from lam.stage.job import create_job, load_job
from lam.stage.lint import lint_relative
from lam.stage.place import place_job


def _warnings(name: str, kind: str) -> list[str]:
    return lint_relative(Path(name), kind)


def test_universal_warnings():
    assert _warnings("bad name.txt", "generic")
    assert _warnings("a<b.txt", "generic")
    assert _warnings("trail.", "generic")
    assert any("reserved" in item for item in _warnings("CON.txt", "generic"))
    assert _warnings("note.txt", "generic") == []


def test_kind_patterns():
    assert _warnings("Dnd_5e_Phandelver.pdf", "game-book") == []
    assert _warnings("Dnd_Fan_5e_Phandelver.pdf", "game-book") == []
    assert _warnings("Dnd_5e_Phandelver_Lost.pdf", "game-book") == []
    assert _warnings("bad name.pdf", "game-book")
    assert _warnings("Tolkien_1954_Fellowship.pdf", "fiction") == []
    assert _warnings("Tolkien_1954_Fellowship_LordOfTheRings_01_coauth_Someone.pdf", "fiction") == []
    assert _warnings("notLib_short_hint_20261016.md", "review") == []
    assert _warnings("notLib_short_hint_20261016_1530.md", "review") == []
    assert _warnings("pic.jpg", "image") == []
    assert _warnings("pic_8k.jpg", "image") == []
    assert _warnings("pic_web.jpg", "image") == []
    assert _warnings("pic_4k.jpg", "image")


def test_strict_blocks_before_copy(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    folder = create_job(owner="Ark", job="notes", target=target, root=tmp_path / "root")
    (folder / "out" / "bad name.txt").write_bytes(b"x")
    calls: list[str] = []
    code = place_job(folder, strict=True, jobs_dir=tmp_path / "jobs", doorbell=calls.append)
    assert code == 1
    assert not (target / "bad name.txt").exists()
    assert load_job(folder / "job.json")["status"] == "unplaced"
    assert (folder / "logs" / "lint.txt").is_file()
    assert calls == ["FAIL"]
