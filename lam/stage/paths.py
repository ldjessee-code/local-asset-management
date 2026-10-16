# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging roots and path refusals.

Staging must sit outside Dropbox and OneDrive. The place target may be
inside Dropbox. ``C:\\Example`` and ``.git`` are refused as write targets.
"""

from __future__ import annotations

import os
from pathlib import Path

from lam.stage.errors import StageError
from lam.token.paths import dropbox_root, is_example_path, is_same_or_under

MAP_BYTE_CAP = 100_000_000
PREFERRED_ROOT = Path(r"C:\CourtStaging")
FALLBACK_ROOT = Path(r"F:\CourtStaging")
HARD_DROPBOX = Path(r"F:\Dropbox")
ILLEGAL = set('<>:"/\\|?*')
RESERVED = {"CON", "PRN", "AUX", "NUL"}
RESERVED.update(f"COM{n}" for n in range(1, 10))
RESERVED.update(f"LPT{n}" for n in range(1, 10))
ONEDRIVE_ENVS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")


def drive_exists(drive: str) -> bool:
    """True when ``drive`` (like ``C:``) has a root directory."""
    return Path(drive + "\\").exists()


def default_staging_root() -> Path:
    """``LAM_STAGING_ROOT``, else ``C:\\CourtStaging`` when that drive exists, else ``F:\\CourtStaging``.

    This does not create either directory.
    """
    raw = os.environ.get("LAM_STAGING_ROOT")
    if raw:
        return Path(raw)
    if PREFERRED_ROOT.drive and drive_exists(PREFERRED_ROOT.drive):
        return PREFERRED_ROOT
    return FALLBACK_ROOT


def default_jobs_dir(job: str) -> Path:
    raw = os.environ.get("LAM_PLACE_JOBS_DIR")
    if raw:
        return Path(raw) / job
    return Path(r"F:\Dropbox\TheCourt\research\_jobs") / job


def abs_path(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path))))


def path_key(path: Path) -> str:
    return os.path.normcase(str(abs_path(path)))


def under_git(path: Path) -> bool:
    return any(part == ".git" for part in Path(path).parts)


def _onedrive_component(path: Path) -> bool:
    for part in Path(path).parts:
        lowered = part.casefold()
        if lowered == "onedrive" or lowered.startswith("onedrive "):
            return True
    return False


def assert_staging_root_allowed(root: Path) -> Path:
    """Refuse a staging root inside Dropbox, OneDrive, or ``C:\\Example``."""
    root = abs_path(root)
    if is_same_or_under(root, dropbox_root()) or is_same_or_under(root, HARD_DROPBOX):
        raise StageError(f"refused: staging root is inside Dropbox: {root}")
    for name in ONEDRIVE_ENVS:
        raw = os.environ.get(name)
        if raw and is_same_or_under(root, Path(raw)):
            raise StageError(f"refused: staging root is inside OneDrive: {root}")
    if _onedrive_component(root):
        raise StageError(f"refused: staging root is inside OneDrive: {root}")
    if is_example_path(root):
        raise StageError("refused: example path")
    return root


def assert_write_target_allowed(path: Path) -> Path:
    """Refuse place/publish destinations under ``C:\\Example`` or ``.git``."""
    path = abs_path(path)
    if is_example_path(path):
        raise StageError("refused: example path")
    if under_git(path):
        raise StageError(f"refused: path under .git: {path}")
    return path


def safe_segment(value: str, label: str) -> str:
    """One path segment: no slash, no Windows-illegal character, not ``.`` or ``..``."""
    if not isinstance(value, str) or value in {"", ".", ".."}:
        raise StageError(f"{label} must be one safe path segment")
    if "/" in value or "\\" in value:
        raise StageError(f"{label} must be one safe path segment")
    if any(char in ILLEGAL or ord(char) < 32 for char in value):
        raise StageError(f"{label} contains an illegal character")
    if value.endswith(" ") or value.endswith("."):
        raise StageError(f"{label} must not end with a dot or space")
    return value
