# SPDX-License-Identifier: AGPL-3.0-or-later
"""login uses a visible persistent context and never types credentials."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.cli import main
from lam.token.browser import wait_for_login
from tests.token.fakes import FakeBrowserLayer, FakeContext, TargetClosedError


def _cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as caught:
        main(["token", *argv])
    return int(caught.value.code or 0)


def test_login_visible_persistent_never_fills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    fake = FakeBrowserLayer()
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    monkeypatch.setattr("lam.token.browser._wait_fn", lambda _ctx: None)
    assert _cli(["login", "patreon"]) == 0
    assert len(fake.launches) == 1
    launch = fake.launches[0]
    assert launch["headless"] is False
    profile = local / "lam" / "browser-profiles" / "patreon"
    assert Path(launch["user_data_dir"]) == profile
    assert profile.is_dir()
    assert str(profile).startswith(str(local))
    ctx = fake.contexts[0]
    assert ctx.closed is True
    page_calls = [call[0] for page in ctx.pages for call in page.calls]
    assert "goto" in page_calls
    assert "fill" not in page_calls
    assert "type" not in page_calls
    assert "press" not in page_calls


CLOSED_MSG = (
    'login window closed - session saved in profile; run "lam token status patreon --check" to confirm'
)

_SESSION = {
    "name": "session_id",
    "value": "ok-token",
    "domain": ".patreon.com",
    "path": "/",
    "expires": -1,
}


def test_login_closed_window_reopens_headless_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    fake = FakeBrowserLayer([_SESSION])
    fake.cookies_error_queue = [
        TargetClosedError("BrowserContext.cookies: Target page, context or browser has been closed"),
        None,
    ]
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    monkeypatch.setattr("lam.token.browser._wait_fn", lambda _ctx: None)
    assert _cli(["login", "patreon"]) == 0
    captured = capsys.readouterr()
    assert CLOSED_MSG in captured.err
    assert "Traceback" not in captured.err
    assert "TargetClosedError" not in captured.err
    assert len(fake.launches) == 2
    assert fake.launches[0]["headless"] is False
    assert fake.launches[1]["headless"] is True
    assert all(ctx.closed for ctx in fake.contexts)
    meta = local / "lam" / "secrets" / "patreon_meta.json"
    assert meta.is_file()


def test_login_closed_window_without_cookies_is_auth_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    fake = FakeBrowserLayer([])
    fake.cookies_error_queue = [
        TargetClosedError("BrowserContext.cookies: Target page, context or browser has been closed"),
        None,
    ]
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    monkeypatch.setattr("lam.token.browser._wait_fn", lambda _ctx: None)
    code = _cli(["login", "patreon"])
    captured = capsys.readouterr()
    assert code == 3
    assert captured.err.startswith("GATE auth:")
    assert "Traceback" not in captured.err
    assert "TargetClosedError" not in captured.err


def test_wait_for_login_prompt_prefers_enter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    class _Stdin:
        def isatty(self) -> bool:
            return False

        def readline(self) -> str:
            return "\n"

    monkeypatch.setattr("lam.token.browser._wait_fn", None)
    monkeypatch.setattr("lam.token.browser.sys.stdin", _Stdin())
    ctx = FakeContext(Path("."), headless=False, cookies=[])
    wait_for_login(ctx)
    err = capsys.readouterr().err
    assert "press Enter" in err
