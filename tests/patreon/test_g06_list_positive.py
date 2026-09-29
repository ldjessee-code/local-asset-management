# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 6: three accessible posts, newest first."""

from __future__ import annotations

from pathlib import Path

from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "tests" / "fixtures" / "patreon"
SAMPLE = REPO / "examples" / "patreon-creators.sample.json"


def test_three_accessible_newest_first(tmp_path: Path, capsys):
    items = []
    specs = [
        ("p-new", "Newest", "2026-09-01T15:00:00+00:00"),
        ("p-mid", "Middle", "2026-06-01T15:00:00+00:00"),
        ("p-oldish", "Older", "2025-10-02T15:00:00+00:00"),
        ("p-out", "Outside", "2025-08-01T15:00:00+00:00"),
    ]
    for pid, title, published in specs:
        items.append(post(pid, title, published, attachments=[attachment(pid + "-a", "map.zip")]))
    root = tmp_path / "fx"
    write_fixture(root, "kidneyboy", [page(items)])
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(tmp_path / "stage"),
    )
    code = invoke(
        [
            "patreon",
            "list",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--since",
            "2025-09-28",
            "--until",
            "2026-09-29",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    body = [line for line in out.splitlines() if line[:4].isdigit()]
    assert len(body) == 3
    assert [line.split()[0] for line in body] == ["2026-09-01", "2026-06-01", "2025-10-02"]
    assert "Outside" not in out
    assert "3 accessible" in out
    assert "Kidney_Boy (kidneyboy)" in out


def test_demo_fixture_window(monkeypatch, capsys):
    monkeypatch.setenv("LAM_PATREON_NOW", "2026-09-28T20:00:00-04:00")
    code = invoke(
        [
            "patreon",
            "list",
            "--creators",
            str(SAMPLE),
            "--fixture-dir",
            str(DEMO),
            "--last-months",
            "12",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Kidney_Boy (kidneyboy) - posts 2025-09-28..2026-09-28 (America/Indianapolis)" in out
    assert "Harbor Keep" in out
    assert "Old Pack" not in out
    assert "excluded (chronos)" in out
    assert "Party_of_Two: 0 accessible posts in window (2 listed, 2 locked for your tier)" in out
