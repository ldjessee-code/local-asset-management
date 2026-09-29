# SPDX-License-Identifier: AGPL-3.0-or-later
"""Secrets store: temp+rename, replace own file only, one line, no write on failure."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lam.cli import main
from lam.token.errors import EXIT_USAGE, TokenUsageError
from lam.token.secret import Secret
from lam.token.store import write_secrets_file
from tests.token.fakes import FakeBrowserLayer


def _cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as caught:
        main(["token", *argv])
    return int(caught.value.code or 0)


def test_write_via_temp_then_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dest = tmp_path / "patreon_cookie.txt"
    seen: list[tuple[str, str]] = []
    real = os.replace

    def spy(src, dst):
        seen.append((str(src), str(dst)))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    write_secrets_file(dest, Secret("session_id=abc"), replace_own=False)
    assert dest.is_file()
    assert seen
    src, dst = seen[-1]
    assert Path(dst) == dest
    assert Path(src).parent == dest.parent
    assert Path(src).name.startswith(".lam-tmp-")
    assert not Path(src).exists()
    text = dest.read_text(encoding="utf-8")
    assert text == "session_id=abc\n"
    assert len(text.splitlines()) == 1

    write_secrets_file(dest, Secret("session_id=xyz"), replace_own=True)
    assert dest.read_text(encoding="utf-8") == "session_id=xyz\n"


def test_refuse_unrelated_existing_file(tmp_path: Path):
    other = tmp_path / "notes.txt"
    other.write_text("keep-me\n", encoding="utf-8")
    with pytest.raises(TokenUsageError) as caught:
        write_secrets_file(other, Secret("session_id=nope"), replace_own=False)
    assert caught.value.exit_code == EXIT_USAGE
    assert str(caught.value).startswith("GATE path:")
    assert other.read_text(encoding="utf-8") == "keep-me\n"


def test_get_does_not_write_secrets_when_it_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    secrets = local / "lam" / "secrets" / "patreon_cookie.txt"
    fake = FakeBrowserLayer(
        [
            {
                "name": "session_id",
                "value": "abc",
                "domain": ".patreon.com",
                "path": "/",
                "expires": -1,
            }
        ]
    )
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    assert _cli(["get", "patreon"]) == 3
    assert not secrets.exists()
