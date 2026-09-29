# SPDX-License-Identifier: AGPL-3.0-or-later
"""login uses a visible persistent context and never types credentials."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.cli import main
from tests.token.fakes import FakeBrowserLayer


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
