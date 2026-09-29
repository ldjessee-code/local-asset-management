# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 8: cursor pages, early stop, max-posts, malformed next."""

from __future__ import annotations

from pathlib import Path

from lam.patreon.http_source import FixtureSource
from tests.patreon.builders import creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def _pages():
    def item(pid, day):
        return post(pid, pid, f"{day}T15:00:00+00:00")

    return [
        page(
            [item("sep", "2026-09-01"), item("aug", "2026-08-01")],
            next_link="https://example.invalid/api/posts?page=2",
        ),
        page(
            [item("jul", "2026-07-01"), item("jun", "2026-06-01")],
            next_link="https://example.invalid/api/posts?page=3",
        ),
        page([item("may", "2026-05-01"), item("jan", "2025-01-01")]),
    ]


def test_stops_before_older_page(tmp_path: Path, capsys):
    FixtureSource.pages_read = []
    root = tmp_path / "fx"
    write_fixture(root, "kidneyboy", _pages())
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
            "2026-07-01",
            "--until",
            "2026-10-01",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "sep" in out and "aug" in out and "jul" in out
    assert "jun" not in out and "may" not in out
    assert len(FixtureSource.pages_read) == 2


def test_max_posts(tmp_path: Path, capsys):
    FixtureSource.pages_read = []
    root = tmp_path / "fx"
    write_fixture(root, "kidneyboy", _pages())
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
            "2020-01-01",
            "--max-posts",
            "1",
        ]
    )
    assert code == 0
    body = [line for line in capsys.readouterr().out.splitlines() if line[:4].isdigit()]
    assert len(body) == 1
    assert len(FixtureSource.pages_read) == 1


def test_malformed_next_is_item_failure(tmp_path: Path, capsys):
    root = tmp_path / "fx"
    bad = page([post("sep", "Sep", "2026-09-01T15:00:00+00:00")], next_link="%%%bad")
    write_fixture(root, "kidneyboy", [bad])
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
            "2020-01-01",
            "--max-posts",
            "20",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "next" in (captured.err + captured.out).lower()
    assert "Traceback" not in captured.err
