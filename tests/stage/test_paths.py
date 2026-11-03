# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging root resolution and synced-tree refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.stage.errors import StageError
from lam.stage.job import create_job
from lam.stage.paths import DEFAULT_ROOT, MAP_BYTE_CAP, default_staging_root


def test_map_cap_is_decimal_100_million():
    assert MAP_BYTE_CAP == 100_000_000


def test_default_root_is_f_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LAM_STAGING_ROOT", raising=False)
    assert DEFAULT_ROOT == Path(r"F:\CourtStaging")
    before = DEFAULT_ROOT.exists()
    assert default_staging_root() == DEFAULT_ROOT
    assert DEFAULT_ROOT.exists() is before


def test_lam_staging_root_overrides_and_does_not_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    custom = tmp_path / "from-env"
    monkeypatch.setenv("LAM_STAGING_ROOT", str(custom))
    assert default_staging_root() == custom
    assert custom.exists() is False


def test_ark_job_lands_under_owner_on_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("LAM_STAGING_ROOT", raising=False)
    created = create_job(
        owner="Ark",
        job="maps",
        target=tmp_path / "target",
        root=tmp_path / "stage",
    )
    assert created.parent == (tmp_path / "stage" / "Ark")
    assert default_staging_root() / "Ark" == DEFAULT_ROOT / "Ark"


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
