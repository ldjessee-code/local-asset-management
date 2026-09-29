# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 12: apply layout, sanitizing, and staging refusals."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def _apply(tmp_path: Path, title: str, name: str, monkeypatch, stage: Path) -> int:
    monkeypatch.setenv("LAM_PATREON_NOW", "2026-10-01T12:00:00-04:00")
    root = tmp_path / "fx"
    write_fixture(
        root,
        "kidneyboy",
        [page([post("p1", title, "2026-06-01T15:00:00+00:00", attachments=[attachment("a1", name)])])],
    )
    (root / "files").mkdir(exist_ok=True)
    (root / "files" / "a1").write_bytes(b"hello-bytes")
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(stage),
    )
    return invoke(
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
    )


def test_inbox_layout(tmp_path: Path, monkeypatch):
    stage = tmp_path / "stage"
    assert _apply(tmp_path, "Harbor Keep", "map.zip", monkeypatch, stage) == 0
    found = list(stage.rglob("map.zip"))
    assert len(found) == 1
    path = found[0]
    assert "_inbox_20261001" in path.parts
    assert "Kidney_Boy" in path.parts
    assert path.parent.name.startswith("p1_")
    assert "Harbor" in path.parent.name


def test_sanitize_reserved_trailing_and_long(tmp_path: Path, monkeypatch):
    stage = tmp_path / "stage"
    title = "CON" + ("x" * 180) + "..."
    assert _apply(tmp_path, title, "CON.zip", monkeypatch, stage) == 0
    found = list(stage.rglob("*.zip"))
    assert len(found) == 1
    assert found[0].name.lower() != "con.zip"
    assert not found[0].name.endswith(".")
    assert not found[0].parent.name.endswith(".")
    assert len(found[0].parent.name) <= 90


@pytest.mark.parametrize(
    "staging",
    [r"F:\Dropbox\Gaming\maps", r"F:\Dropbox\PatreonDL", r"C:\Example\PatreonDL"],
)
def test_refuses_staging_before_request(tmp_path: Path, staging: str, capsys, monkeypatch):
    called = {"n": 0}

    def factory():
        called["n"] += 1
        raise AssertionError("request")

    import lam.patreon.http_source as http_source

    monkeypatch.setattr(http_source, "_client_factory", factory)
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=staging,
    )
    code = invoke(["patreon", "sync", "--creators", str(cfg), "--apply", "--since", "2025-01-01"])
    captured = capsys.readouterr()
    assert code == 2
    assert called["n"] == 0
    assert "GATE " in captured.err
