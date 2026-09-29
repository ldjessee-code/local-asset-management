# SPDX-License-Identifier: AGPL-3.0-or-later
"""Profile and secrets paths: refuse repo, Dropbox, C:\\Example; allow siblings."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.token.errors import EXIT_USAGE, TokenUsageError
from lam.token.paths import (
    check_allowed_path,
    default_profile_dir,
    default_secrets_file,
    resolve_profile_dir,
    resolve_secrets_file,
)
from lam.token.profiles import builtin_profiles

REPO = Path(__file__).resolve().parents[2]


def test_path_inside_repo_refused():
    inside = REPO / "lam" / "token" / "secrets.txt"
    with pytest.raises(TokenUsageError) as caught:
        check_allowed_path(inside, kind="secrets")
    assert caught.value.exit_code == EXIT_USAGE
    assert str(caught.value).startswith("GATE path:")
    assert "repo" in str(caught.value).lower()


def test_path_under_dropbox_refused():
    samples = [
        r"F:\Dropbox\Gaming\cookie.txt",
        r"f:\dropbox\gaming\cookie.txt",
        "F:/Dropbox/Gaming/cookie.txt",
        r"F:\Dropbox\\",
        r"F:\Dropbox",
        r"F:\Dropbox\TheCourt\x",
    ]
    for raw in samples:
        with pytest.raises(TokenUsageError) as caught:
            check_allowed_path(Path(raw), kind="profile")
        assert caught.value.exit_code == EXIT_USAGE, raw
        assert str(caught.value).startswith("GATE path:"), raw
        assert "dropbox" in str(caught.value).lower(), raw


def test_path_under_example_refused():
    samples = [
        r"C:\Example",
        r"C:\Example\secrets\patreon_cookie.txt",
        r"c:\example\foo",
        r"C:/Example/foo",
    ]
    for raw in samples:
        with pytest.raises(TokenUsageError) as caught:
            check_allowed_path(Path(raw), kind="secrets")
        assert caught.value.exit_code == EXIT_USAGE, raw
        assert str(caught.value).startswith("GATE path:"), raw
        assert "example" in str(caught.value).lower(), raw


def test_sibling_dropboxold_allowed():
    check_allowed_path(Path(r"F:\DropboxOld\secrets\patreon_cookie.txt"), kind="secrets")
    check_allowed_path(Path(r"C:\Examples\cookie.txt"), kind="secrets")
    check_allowed_path(Path(r"C:\ExampleFoo\cookie.txt"), kind="profile")


def test_defaults_resolve_under_monkeypatched_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "AppLocal"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    profile = default_profile_dir("patreon")
    secrets = default_secrets_file("patreon")
    assert profile == local / "lam" / "browser-profiles" / "patreon"
    assert secrets == local / "lam" / "secrets" / "patreon_cookie.txt"
    check_allowed_path(profile, kind="profile")
    check_allowed_path(secrets, kind="secrets")

    site = builtin_profiles().get("patreon")
    assert resolve_profile_dir(site) == profile
    assert resolve_secrets_file(site) == secrets
