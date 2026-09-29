# SPDX-License-Identifier: AGPL-3.0-or-later
"""Allowed locations for browser profiles and secrets files.

Profiles and secrets stay outside the repo, Dropbox, and ``C:\\Example``.
"""

from __future__ import annotations

import os
from pathlib import Path

from lam.token.errors import TokenUsageError, gate
from lam.token.profiles import SiteProfile

DEFAULT_DROPBOX_ROOT = r"F:\Dropbox"
EXAMPLE_ROOT = r"C:\Example"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def dropbox_root() -> Path:
    raw = os.environ.get("LAM_DROPBOX_ROOT") or DEFAULT_DROPBOX_ROOT
    return Path(raw)


def localappdata() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if not raw:
        raw = str(Path.home() / "AppData" / "Local")
    return Path(raw)


def default_profile_dir(site_id: str) -> Path:
    return localappdata() / "lam" / "browser-profiles" / site_id


def default_secrets_file(site_id: str) -> Path:
    return localappdata() / "lam" / "secrets" / f"{site_id}_cookie.txt"


def default_meta_file(site_id: str) -> Path:
    return localappdata() / "lam" / "secrets" / f"{site_id}_meta.json"


def _abs(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path))))


def _norm_key(path: Path) -> str:
    text = str(_abs(path))
    prefix = "\\\\?\\"
    if text.startswith(prefix):
        text = os.path.normpath(text[len(prefix):])
    return os.path.normcase(os.path.normpath(text)).rstrip("\\/")


def is_same_or_under(path: Path, root: Path) -> bool:
    """Case-insensitive whole-component prefix. ``F:\\DropboxOld`` is not under ``F:\\Dropbox``."""
    inner = _norm_key(path)
    outer = _norm_key(root)
    if inner == outer:
        return True
    sep = os.sep
    alt = "/" if sep != "/" else "\\"
    return inner.startswith(outer + sep) or inner.startswith(outer + alt)


def is_example_path(path: Path) -> bool:
    return is_same_or_under(path, Path(EXAMPLE_ROOT))


def check_allowed_path(path: Path, *, kind: str) -> Path:
    """Raise ``TokenUsageError`` (exit 2) when *path* is in a refused tree."""
    abs_path = _abs(path)
    if is_same_or_under(abs_path, repo_root()):
        raise TokenUsageError(
            gate("path", f"{kind} path is inside the repo working tree: {abs_path}")
        )
    if is_same_or_under(abs_path, dropbox_root()):
        raise TokenUsageError(
            gate("path", f"{kind} path is under Dropbox: {abs_path}")
        )
    if is_example_path(abs_path):
        raise TokenUsageError(
            gate("path", f"{kind} path is under C:\\Example: {abs_path}")
        )
    return abs_path


def resolve_profile_dir(site: SiteProfile) -> Path:
    if site.profile_dir:
        path = Path(site.profile_dir)
    else:
        path = default_profile_dir(site.id)
    return check_allowed_path(path, kind="profile")


def resolve_secrets_file(site: SiteProfile) -> Path:
    if site.secrets_file:
        path = Path(site.secrets_file)
    else:
        path = default_secrets_file(site.id)
    return check_allowed_path(path, kind="secrets")


def resolve_meta_file(site: SiteProfile) -> Path:
    secrets = resolve_secrets_file(site)
    return check_allowed_path(secrets.with_name(f"{site.id}_meta.json"), kind="secrets")
