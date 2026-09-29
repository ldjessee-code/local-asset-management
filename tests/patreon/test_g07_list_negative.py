# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 7: empty, locked, and not-a-member are exit 0, not gates."""

from __future__ import annotations

from pathlib import Path

from tests.patreon.builders import creator, page, post, write_config, write_fixture
from tests.patreon.conftest import gate_lines, invoke


def _run(tmp_path: Path, vanity: str, slug: str, items, *, member: bool) -> tuple[int, str, str]:
    root = tmp_path / "fx"
    write_fixture(root, vanity, [page(items)] if items is not None else [], member=member)
    if items is None:
        write_fixture(root, vanity, [{"data": [], "included": [], "links": {}}], member=member)
    cfg = write_config(
        tmp_path / "creators.json",
        [creator(slug, f"https://www.patreon.com/{vanity}")],
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
    from tests.patreon import conftest as support

    captured_out = ""
    return code, root, cfg


def test_zero_posts_in_window(tmp_path: Path, capsys):
    root = tmp_path / "fx"
    resource, included = post("old", "Ancient", "2020-01-01T00:00:00+00:00")
    write_fixture(root, "emptyone", [page([(resource, included)])])
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Empty_One", "https://www.patreon.com/emptyone")],
        staging_root=str(tmp_path / "stage"),
    )
    before = set(tmp_path.rglob("*"))
    code = invoke(
        [
            "patreon",
            "list",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--since",
            "2026-01-01",
            "--until",
            "2026-02-01",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Empty_One: 0 posts in window" in captured.out
    assert gate_lines(captured.err) == []
    assert set(tmp_path.rglob("*")) == before


def test_all_locked_for_tier(tmp_path: Path, capsys):
    root = tmp_path / "fx"
    items = [
        post("a", "One", "2026-06-01T15:00:00+00:00", can_view=False),
        post("b", "Two", "2026-05-01T15:00:00+00:00", can_view=False),
    ]
    write_fixture(root, "partyoftwo", [page(items)], member=True)
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Party_of_Two", "https://www.patreon.com/partyoftwo")],
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
    out = capsys.readouterr().out
    assert code == 0
    assert "Party_of_Two: 0 accessible posts in window (2 listed, 2 locked for your tier)" in out


def test_not_a_member_is_warning(tmp_path: Path, capsys):
    root = tmp_path / "fx"
    items = [post("a", "Hidden", "2026-06-01T15:00:00+00:00", can_view=False)]
    write_fixture(root, "stranger", [page(items)], member=False)
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Stranger", "https://www.patreon.com/stranger")],
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
    captured = capsys.readouterr()
    assert code == 0
    assert "not a member" in captured.out
    assert gate_lines(captured.err) == []
    assert not (tmp_path / "stage").exists()
