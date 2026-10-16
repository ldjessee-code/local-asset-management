# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging root resolution and synced-tree refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.stage.errors import StageError
from lam.stage.job import create_job
from lam.stage.paths import FALLBACK_ROOT, MAP_BYTE_CAP, PREFERRED_ROOT, default_staging_root


def test_map_cap_is_decimal_100_million():
    assert MAP_BYTE_CAP == 100_000_000


def test_default_root_prefers_c_when_drive_exists(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LAM_STAGING_ROOT", raising=False)
    monkeypatch.setattr("lam.stage.paths.drive_exists", lambda drive: drive.upper().startswith("C"))
    assert default_staging_root() == PREFERRED_ROOT


def test_default_root_falls_back_when_drive_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LAM_STAGING_ROOT", raising=False)
    monkeypatch.setattr("lam.stage.paths.drive_exists", lambda _drive: False)
    assert default_staging_root() == FALLBACK_ROOT


def test_dropbox_root_refused_and_sibling_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dropbox = tmp_path / "Dropbox"
    dropbox.mkdir()
    monkeypatch.setenv("LAM_DROPBOX_ROOT", str(dropbox))
    with pytest.raises(StageError, match="Dropbox"):
        create_job(owner="Ark", job="maps", target=tmp_path / "target", root=dropbox / "stage")
    created = create_job(
        owner="Ark",
        job="maps",
        target=tmp_path / "target",
        root=tmp_path / "DropboxOld",
    )
    assert (created / "job.json").is_file()


def test_onedrive_component_refused(tmp_path: Path):
    with pytest.raises(StageError, match="OneDrive"):
        create_job(
            owner="Ark",
            job="maps",
            target=tmp_path / "target",
            root=tmp_path / "OneDrive" / "stage",
        )


def test_unsafe_owner_and_job(tmp_path: Path):
    root = tmp_path / "root"
    for owner, job in (("..", "maps"), ("a/b", "maps"), ("bad:name", "maps"), ("Ark", "a\\b")):
        with pytest.raises(StageError):
            create_job(owner=owner, job=job, target=tmp_path / "t", root=root)
