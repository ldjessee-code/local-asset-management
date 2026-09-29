# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 17: default Chronos Builder exclusion."""

from __future__ import annotations

from pathlib import Path

from lam.patreon.http_source import FixtureSource
from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def test_chronos_variants_excluded_from_list_and_sync(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("LAM_PATREON_NOW", "2026-10-01T12:00:00-04:00")
    FixtureSource.downloaded = []
    titles = [
        ("c1", "Chronos Builder May"),
        ("c2", "chronos  builder pack"),
        ("c3", "CHRONOS Builder"),
        ("c4", "chronosbuilder weekly"),
        ("ok", "Harbor Keep"),
    ]
    items = []
    for pid, title in titles:
        items.append(
            post(
                pid,
                title,
                "2026-06-01T15:00:00+00:00",
                attachments=[attachment(pid, f"{pid}.zip")],
            )
        )
    root = tmp_path / "fx"
    write_fixture(root, "kidneyboy", [page(items)])
    (root / "files").mkdir()
    for pid, _title in titles:
        (root / "files" / pid).write_bytes(b"data")
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
            "2025-01-01",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("excluded (chronos)") == 4
    assert "Harbor Keep" in out
    assert invoke(
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--apply",
            "--since",
            "2025-01-01",
        ]
    ) == 0
    assert FixtureSource.downloaded == ["https://example.invalid/files/ok/ok.zip"]
