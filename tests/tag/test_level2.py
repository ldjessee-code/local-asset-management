# SPDX-License-Identifier: AGPL-3.0-or-later
"""Level 2 uses a supplied sub-bin list. Bad schema ids exit 2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lam.cli import main
from lam.tag.errors import TagError
from lam.tag.model import TagRequest, TagResult
from lam.tag.plan import run_tag_plan
from lam.tag.scan import run_tag_scan


SUBBINS = {
    "schema": "lam-subbins/v1",
    "bin": "_Maps",
    "subbins": [
        {"name": "SubA", "description": "Neutral placeholder group A", "hints": ["alpha"]},
        {"name": "SubB", "description": "Neutral placeholder group B", "hints": ["beta"]},
    ],
}


class ScriptedTagger:
    model_name = "fake-model"

    def __init__(self, labels: list[str]) -> None:
        self.labels = list(labels)
        self.calls: list[TagRequest] = []

    def classify(self, request: TagRequest) -> TagResult:
        self.calls.append(request)
        return TagResult(label=self.labels.pop(0))


def _write_subbins(path: Path, payload: dict | None = None) -> None:
    path.write_text(json.dumps(payload or SUBBINS), encoding="utf-8")


def test_level2_hint_plan_and_model_labels(tmp_path: Path):
    dest = tmp_path / "lib"
    maps = dest / "_Maps"
    maps.mkdir(parents=True)
    (maps / "alpha").mkdir()
    (maps / "notes").mkdir()
    (maps / "shots").mkdir()
    (maps / "alpha" / "alpha-page.pdf").write_bytes(b"%PDF-1.4\nalpha\n")
    (maps / "notes" / "note.txt").write_text("mystery", encoding="utf-8")
    (maps / "shots" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\nimg")
    sub = tmp_path / "subbins.json"
    _write_subbins(sub)
    tags = tmp_path / "tags.json"
    tagger = ScriptedTagger(["Unsure", "SubB", "SubB"])
    doc = run_tag_scan(
        maps,
        out=tags,
        level=2,
        bin_name="_Maps",
        subbins_path=sub,
        tagger=tagger,
    )
    by = {item["rel"]: item for item in doc["files"]}
    assert by["alpha/alpha-page.pdf"]["bin"] == "_Maps"
    assert by["alpha/alpha-page.pdf"]["subbin"] == "SubA"
    assert by["alpha/alpha-page.pdf"]["confidence"] == "high"
    assert by["notes/note.txt"]["confidence"] == "unsure"
    assert by["notes/note.txt"]["subbin"] is None
    assert tagger.calls
    assert tagger.calls[0].labels == ("SubA", "SubB", "Unsure")
    assert by["shots/shot.png"]["subbin"] == "SubB"
    assert by["shots/shot.png"]["confidence"] == "high"
    built = run_tag_plan(
        tags,
        dest=dest,
        level=2,
        bin_name="_Maps",
        subbins_path=sub,
        out=tmp_path / "plan.json",
    )
    moves = [a for a in built["plan"]["actions"] if a["op"] == "move"]
    assert {Path(a["dst"]) for a in moves} == {
        dest / "_Maps" / "SubA" / "alpha-page.pdf",
        dest / "_Maps" / "SubB" / "shot.png",
    }
    reviewed = {item["rel"] for item in built["review"]["items"]}
    assert reviewed == {"notes/note.txt"}


def test_unknown_schema_exits_2(tmp_path: Path, capsys):
    dest = tmp_path / "lib"
    dest.mkdir()
    bad = tmp_path / "bad-tags.json"
    bad.write_text(json.dumps({"schema": "lam-tags/v9", "files": []}), encoding="utf-8")
    with pytest.raises(TagError, match="unsupported tags schema"):
        run_tag_plan(bad, dest=dest, level=1, out=tmp_path / "plan.json")
    with pytest.raises(SystemExit) as caught:
        main(["tag", "plan", str(bad), "--dest", str(dest), "--level", "1", "--out", str(tmp_path / "p.json")])
    assert caught.value.code == 2
    assert "unsupported" in capsys.readouterr().err

    maps = dest / "_Maps"
    maps.mkdir()
    (maps / "a.txt").write_text("a", encoding="utf-8")
    bad_sub = tmp_path / "bad-sub.json"
    bad_sub.write_text(
        json.dumps({"schema": "lam-subbins/v9", "bin": "_Maps", "subbins": [{"name": "SubA"}]}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "tag",
                "scan",
                str(maps),
                "--no-model",
                "--level",
                "2",
                "--bin",
                "_Maps",
                "--subbins",
                str(bad_sub),
                "--out",
                str(tmp_path / "tags.json"),
            ]
        )
    assert caught.value.code == 2
    assert "unsupported" in capsys.readouterr().err
