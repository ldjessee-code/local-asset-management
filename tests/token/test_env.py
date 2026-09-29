# SPDX-License-Identifier: AGPL-3.0-or-later
"""--set-user-env is opt-in and uses the injected setter."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.cli import main
from tests.token.fakes import FakeBrowserLayer


def _cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as caught:
        main(["token", *argv])
    return int(caught.value.code or 0)


def test_set_user_env_called_only_with_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    (local / "lam" / "browser-profiles" / "patreon").mkdir(parents=True)
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
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    monkeypatch.setattr("lam.token.store._env_setter", lambda name, value: calls.append((name, value)))
    assert _cli(["get", "patreon"]) == 0
    assert calls == []
    assert _cli(["get", "patreon", "--set-user-env"]) == 0
    assert calls == [("PATREON_SESSION_COOKIE", "session_id=ok-token")]
