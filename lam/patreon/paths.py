# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging and cookie-file location guards."""

from __future__ import annotations

import os
from pathlib import Path

from lam.patreon.errors import GATE_DRIVE, PatreonDriveError, PatreonUsageError
from lam.token.errors import gate
from lam.token.paths import dropbox_root, is_example_path, is_same_or_under, repo_root

GAMING_ROOT = Path(r"F:\Dropbox\Gaming")
DROPBOX_DEFAULT = Path(r"F:\Dropbox")


def _abs(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path))))


def drive_mounted(path: Path) -> bool:
    drive, _rest = os.path.splitdrive(str(_abs(path)))
    if not drive:
        return True
    return os.path.exists(drive + "\\")


def check_cookie_file(path: Path) -> Path:
    """Refuse a cookie file inside the repo or under Dropbox. Exit 2."""
    abs_path = _abs(path)
    if is_same_or_under(abs_path, repo_root()):
        raise PatreonUsageError(gate("path", f"cookie file is inside the repo working tree: {abs_path}"))
    if is_same_or_under(abs_path, dropbox_root()) or is_same_or_under(abs_path, DROPBOX_DEFAULT):
        raise PatreonUsageError(gate("path", f"cookie file is under Dropbox: {abs_path}"))
    if is_example_path(abs_path):
        raise PatreonUsageError(gate("path", f"cookie file is under C:\\Example: {abs_path}"))
    return abs_path


def check_staging_root(path: Path) -> Path:
    """Refuse example, Dropbox, and Gaming paths before any request.

    An unmounted drive is exit 9. A refused location is exit 2.
    """
    abs_path = _abs(path)
    if is_example_path(abs_path):
        raise PatreonUsageError(gate("path", f"staging path is under C:\\Example: {abs_path}"))
    if is_same_or_under(abs_path, GAMING_ROOT):
        raise PatreonUsageError(gate("path", f"staging path is under F:\\Dropbox\\Gaming: {abs_path}"))
    if is_same_or_under(abs_path, dropbox_root()) or is_same_or_under(abs_path, DROPBOX_DEFAULT):
        raise PatreonUsageError(gate("path", f"staging path is under Dropbox: {abs_path}"))
    if not drive_mounted(abs_path):
        raise PatreonDriveError(GATE_DRIVE)
    return abs_path
