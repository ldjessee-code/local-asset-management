# SPDX-License-Identifier: AGPL-3.0-or-later
"""Named dest-path templates. Layouts are chosen in config, not inferred."""

from __future__ import annotations

from pathlib import Path
from string import Formatter

from lam.config import LibraryConfig


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def tokens_for(
    cfg: LibraryConfig,
    *,
    source_name: str,
    relpath: str,
    stem: str,
    ext: str,
    bundle: str,
    pack: str,
    relpath_from_pack: str,
    year: str,
    month: str,
    zip_name: str = "",
    parent_dir: str = "",
    group: str = "",
) -> dict[str, str]:
    ext_name = ext[1:] if ext.startswith(".") else ext
    return {
        "library_root": cfg.library_root.as_posix(),
        "quarantine": cfg.quarantine.as_posix(),
        "source_name": source_name,
        "relpath": relpath.replace("\\", "/"),
        "stem": stem,
        "ext": ext,
        "ext_name": ext_name,
        "bundle": bundle,
        "pack": pack,
        "relpath_from_pack": relpath_from_pack.replace("\\", "/"),
        "year": year,
        "month": month,
        "zip_name": zip_name or (stem + ext),
        "parent_dir": parent_dir,
        "group": group,
    }


def render_layout(template: str, tokens: dict[str, str]) -> str:
    return Formatter().vformat(template, (), _SafeDict(tokens))


def dest_path(cfg: LibraryConfig, layout_name: str, tokens: dict[str, str]) -> Path:
    template = cfg.layout.get(layout_name)
    if not template:
        raise KeyError(f"unknown layout: {layout_name}")
    rendered = render_layout(template, tokens)
    return Path(rendered)


def confined_to(path: Path, *roots: Path) -> Path:
    resolved = path if path.is_absolute() else path
    # Don't require the dest to exist; resolve parents.
    candidate = Path(resolved.as_posix())
    try:
        cand_res = candidate.resolve()
    except OSError:
        cand_res = candidate
    for root in roots:
        try:
            root_res = root.resolve()
        except OSError:
            root_res = root
        try:
            cand_res.relative_to(root_res)
            return cand_res
        except ValueError:
            continue
    raise ValueError(f"destination escapes library roots: {candidate}")
