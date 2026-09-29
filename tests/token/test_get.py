# SPDX-License-Identifier: AGPL-3.0-or-later
"""get uses a headless persistent context and always closes it."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.cli import main
from tests.token.fakes import FakeBrowserLayer


def _cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as caught:
        main(["token", *argv])
    return int(caught.value.code or 0)


def test_get_headless_and_closed_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    profile = local / "lam" / "browser-profiles" / "patreon"
    profile.mkdir(parents=True)
    fake = FakeBrowserLayer(
        [
            {
                "name": "session_id",
                "value": "ok-token",
                "domain": ".patreon.com",
                "path": "/",
                "expires": -1,
            }
        ]
    )
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    assert _cli(["get", "patreon"]) == 0
    assert fake.launches[0]["headless"] is True
    assert fake.contexts[0].closed is True


def test_get_closed_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    profile = local / "lam" / "browser-profiles" / "patreon"
    profile.mkdir(parents=True)
    fake = FakeBrowserLayer(
        [
            {
                "name": "session_id",
                "value": "ok-token",
                "domain": ".patreon.com",
                "path": "/",
                "expires": -1,
            }
        ]
    )
    fake.cookies_error = RuntimeError("simulated browser failure")
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    assert _cli(["get", "patreon"]) != 0
    assert fake.contexts[0].closed is True


def test_status_without_site_exit_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr("lam.token.browser._context_factory", FakeBrowserLayer())
    code = _cli(["status"])
    captured = capsys.readouterr()
    assert code == 3
    assert "no saved login" in captured.out
    assert captured.err.startswith("GATE auth: no saved login for patreon")


def test_get_missing_profile_exit_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    fake = FakeBrowserLayer()
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    code = _cli(["get", "patreon"])
    err = capsys.readouterr().err
    assert code == 3
    assert err.startswith("GATE auth: no saved login for patreon - run: lam token login patreon")
    assert fake.launches == []
