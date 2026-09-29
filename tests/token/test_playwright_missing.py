# SPDX-License-Identifier: AGPL-3.0-or-later
"""Playwright is optional: login/get gate, status still works, import stays lazy."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lam.cli import main
from lam.token.errors import EXIT_DEPS, PLAYWRIGHT_INSTALL_HINT


def _cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as caught:
        main(["token", *argv])
    return int(caught.value.code or 0)


def test_import_lam_token_does_not_import_playwright():
    mods = {name for name in sys.modules if name == "playwright" or name.startswith("playwright.")}
    assert not mods
    import lam.token as token_mod

    assert token_mod is not None
    mods = {name for name in sys.modules if name == "playwright" or name.startswith("playwright.")}
    assert not mods


def test_login_and_get_exit_8_when_playwright_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr("lam.token.browser._context_factory", None)
    monkeypatch.setattr("lam.token.browser._wait_fn", lambda _ctx: None)
    login_code = _cli(["login", "patreon"])
    login_err = capsys.readouterr().err
    assert login_code == EXIT_DEPS
    assert login_err.startswith("GATE deps: playwright not installed - run: ")
    assert PLAYWRIGHT_INSTALL_HINT in login_err

    profile = local / "lam" / "browser-profiles" / "patreon"
    profile.mkdir(parents=True)
    get_code = _cli(["get", "patreon"])
    get_err = capsys.readouterr().err
    assert get_code == EXIT_DEPS
    assert get_err.startswith("GATE deps: playwright not installed - run: ")
    assert PLAYWRIGHT_INSTALL_HINT in get_err


def test_status_offline_works_without_playwright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr("lam.token.browser._context_factory", None)
    code = _cli(["status", "patreon"])
    out = capsys.readouterr()
    assert "no saved login" in out.out
    assert code == 3
    assert "GATE auth:" in out.err
