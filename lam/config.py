# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load, validate, and save library.jsonc.

Rules the rest of the engine relies on:

- Default file is ``library.jsonc`` (or ``library.json``) in cwd, or ``LAM_CONFIG``.
- The config file must not sit inside a source or the destination tree.
- Source folders must exist. Destination folders are created on scan/apply.
- ``output_folder`` in the setup form maps to ``output/media`` plus sidecar dirs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from lam.jsonc import dump_path, load_path
from lam.util import source_name

DEFAULT_GROUPS: dict[str, list[str]] = {
    "images": [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"],
    "sidecars": [".txt", ".md", ".pdf", ".docx", ".nfo", ".json", ".html", ".url"],
    "archives": [".zip"],
    "archives_optional": [".7z", ".rar"],
}

DEFAULT_LAYOUTS: dict[str, str] = {
    "keep_relpath": "{library_root}/{source_name}/{relpath}",
    "by_year": "{library_root}/screenshots/{year}/{stem}{ext}",
    "by_year_month": "{library_root}/photos/{year}/{month}/{stem}{ext}",
    "pack_tree": "{library_root}/packs/{bundle}/{pack}/{relpath_from_pack}",
    "by_ext": "{library_root}/by-ext/{ext_name}/{stem}{ext}",
    "sidecar_with_parent": "{parent_dir}/_notes/{stem}{ext}",
    "zip_store": "{library_root}/_zips/{bundle}/{zip_name}",
}

DEFAULT_EXCLUDE = ["node_modules", ".git", "__MACOSX", ".DS_Store"]
DEFAULT_CONFIG_NAMES = ("library.jsonc", "library.json")
LAYOUT_NAMES = tuple(DEFAULT_LAYOUTS)


class ConfigError(ValueError):
    """User-facing config problem (missing path, config sitting in a tree, …)."""


@dataclass
class Source:
    path: Path
    layout: str
    priority: int = 0
    name: str = ""


@dataclass
class ZipSettings:
    index_nested: bool = True
    extract_on_apply: bool = False
    max_depth: int = 2


@dataclass
class ApplySettings:
    default_op: str = "copy"
    never_modify_sources: bool = True
    quarantine_exact_dupes: bool = True


@dataclass
class ServeSettings:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class LibraryConfig:
    config_path: Path
    profile: str
    sources: list[Source]
    quarantine: Path
    library_root: Path
    index_db: Path
    log_dir: Path
    reports_dir: Path
    groups: dict[str, list[str]]
    layout: dict[str, str]
    exclude_dir_names: set[str]
    zip: ZipSettings
    helpers: dict[str, Any]
    apply: ApplySettings
    serve: ServeSettings
    hash_workers: int = 4
    ext_to_group: dict[str, str] = field(default_factory=dict)
    protect_paths: list[Path] = field(default_factory=list)

    def group_for(self, ext: str) -> str | None:
        return self.ext_to_group.get(ext.lower())

    def archive_exts(self) -> set[str]:
        exts = set(self.groups.get("archives", []))
        exts |= set(self.groups.get("archives_optional", []))
        return {e.lower() for e in exts}

    def sidecar_exts(self) -> set[str]:
        return {e.lower() for e in self.groups.get("sidecars", [])}

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path),
            "profile": self.profile,
            "sources": [
                {
                    "path": str(s.path),
                    "layout": s.layout,
                    "priority": s.priority,
                    "name": s.name,
                }
                for s in self.sources
            ],
            "quarantine": str(self.quarantine),
            "library_root": str(self.library_root),
            "index_db": str(self.index_db),
            "log_dir": str(self.log_dir),
            "reports_dir": str(self.reports_dir),
            "layout_names": sorted(self.layout),
            "exclude_dir_names": sorted(self.exclude_dir_names),
            "apply": {
                "default_op": self.apply.default_op,
                "never_modify_sources": self.apply.never_modify_sources,
                "quarantine_exact_dupes": self.apply.quarantine_exact_dupes,
            },
            "hash_workers": self.hash_workers,
            "serve": {"host": self.serve.host, "port": self.serve.port},
            "protect_paths": [str(p) for p in self.protect_paths],
            "needs_setup": False,
        }

    def managed_roots(self) -> list[Path]:
        roots = [self.library_root, self.quarantine, self.log_dir, self.reports_dir, self.index_db.parent]
        # output\media plus siblings (_quarantine, index, logs) — keep config out of that whole tree
        if self.library_root.name.lower() == "media":
            roots.append(self.library_root.parent)
        roots.extend(s.path for s in self.sources)
        return roots

    def as_save_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "sources": [
                {
                    "path": s.path.as_posix(),
                    "layout": s.layout,
                    "priority": s.priority,
                    **({"name": s.name} if s.name and s.name != source_name(s.path) else {}),
                }
                for s in self.sources
            ],
            "library_root": self.library_root.as_posix(),
            "quarantine": self.quarantine.as_posix(),
            "index_db": self.index_db.as_posix(),
            "log_dir": self.log_dir.as_posix(),
            "reports_dir": self.reports_dir.as_posix(),
            "apply": {
                "default_op": self.apply.default_op,
                "never_modify_sources": self.apply.never_modify_sources,
                "quarantine_exact_dupes": self.apply.quarantine_exact_dupes,
            },
            "hash_workers": self.hash_workers,
            "serve": {"host": self.serve.host, "port": self.serve.port},
            "protect_paths": [p.as_posix() for p in self.protect_paths],
        }


def _resolve(raw: str | Path, cfg_dir: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cfg_dir / path
    return path


def _norm_exts(values: list[Any]) -> list[str]:
    out: list[str] = []
    for v in values:
        s = str(v).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        out.append(s)
    return out


def path_is_under(path: Path, root: Path) -> bool:
    """True if path is root or a file/dir inside root (Windows-case-insensitive)."""
    try:
        p = os.path.normcase(str(path.expanduser().resolve()))
        r = os.path.normcase(str(root.expanduser().resolve()))
    except OSError:
        p = os.path.normcase(str(Path(path)))
        r = os.path.normcase(str(Path(root)))
    if p == r:
        return True
    sep = os.sep
    prefix = r.rstrip("\\/") + sep
    return p.startswith(prefix)


def _can_create(path: Path) -> bool:
    cur = path
    while True:
        try:
            if cur.exists():
                return cur.is_dir()
        except OSError:
            return False
        parent = cur.parent
        if parent == cur:
            return False
        cur = parent


def paths_from_output_folder(output: Path) -> dict[str, Path]:
    """Map a user-facing output folder to library_root + sidecar dirs."""
    home = output.expanduser().resolve()
    return {
        "library_root": home / "media",
        "quarantine": home / "_quarantine",
        "index_db": home / "index" / "library.sqlite",
        "log_dir": home / "logs",
        "reports_dir": home / "reports",
    }


def find_default_config(cwd: Path | None = None) -> Path | None:
    env = os.environ.get("LAM_CONFIG")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p.resolve()
        raise ConfigError(f"LAM_CONFIG is set but not a file: {p}")
    here = cwd or Path.cwd()
    for name in DEFAULT_CONFIG_NAMES:
        p = here / name
        if p.is_file():
            return p.resolve()
    return None


def load_config(path: Path | str, *, validate: bool = True) -> LibraryConfig:
    """Load JSONC/JSON from ``path`` and, by default, run ``validate_config``."""
    cfg_path = Path(path).expanduser().resolve()
    if not cfg_path.is_file():
        raise ConfigError(f"config not found: {cfg_path}")
    raw = load_path(cfg_path)
    if not isinstance(raw, dict):
        raise ConfigError("config root must be an object")
    cfg = config_from_raw(raw, cfg_path)
    if validate:
        validate_config(cfg)
    return cfg


def config_from_raw(raw: dict[str, Any], cfg_path: Path) -> LibraryConfig:
    cfg_dir = cfg_path.parent
    sources_raw = raw.get("sources") or []
    if not sources_raw:
        raise ConfigError("at least one source folder is required")

    if raw.get("output_folder") and not raw.get("library_root"):
        derived = paths_from_output_folder(_resolve(raw["output_folder"], cfg_dir))
        library_root = derived["library_root"]
        index_db = _resolve(raw.get("index_db") or derived["index_db"], cfg_dir).resolve()
        quarantine = _resolve(raw.get("quarantine") or derived["quarantine"], cfg_dir).resolve()
        log_dir = _resolve(raw.get("log_dir") or derived["log_dir"], cfg_dir).resolve()
        reports_dir = _resolve(raw.get("reports_dir") or derived["reports_dir"], cfg_dir).resolve()
    else:
        library_root = _resolve(raw.get("library_root") or "./library/media", cfg_dir).resolve()
        index_db = _resolve(raw.get("index_db") or "./library/index/library.sqlite", cfg_dir).resolve()
        library_home = library_root.parent
        quarantine = _resolve(raw.get("quarantine") or (library_home / "_quarantine"), cfg_dir).resolve()
        log_dir = _resolve(raw.get("log_dir") or (library_home / "logs"), cfg_dir).resolve()
        reports_dir = _resolve(raw.get("reports_dir") or (library_home / "reports"), cfg_dir).resolve()

    sources: list[Source] = []
    for item in sources_raw:
        if not isinstance(item, dict) or not item.get("path"):
            raise ConfigError("each source needs a path")
        p = _resolve(item["path"], cfg_dir).resolve()
        layout = str(item.get("layout") or "keep_relpath")
        src = Source(
            path=p,
            layout=layout,
            priority=int(item.get("priority") or 0),
            name=str(item.get("name") or source_name(p)),
        )
        sources.append(src)

    groups = {k: _norm_exts(v) for k, v in DEFAULT_GROUPS.items()}
    for key, vals in (raw.get("groups") or {}).items():
        groups[key] = _norm_exts(vals)

    layouts = dict(DEFAULT_LAYOUTS)
    layouts.update({k: str(v) for k, v in (raw.get("layout") or {}).items()})

    zip_raw = raw.get("zip") or {}
    apply_raw = raw.get("apply") or {}
    serve_raw = raw.get("serve") or {}
    profile = str(raw.get("profile") or "spacious")
    default_op = str(apply_raw.get("default_op") or ("hardlink" if profile == "tight" else "copy"))

    ext_to_group: dict[str, str] = {}
    for group_name, exts in groups.items():
        label = "archives" if group_name == "archives_optional" else group_name
        for ext in exts:
            ext_to_group.setdefault(ext, label)

    return LibraryConfig(
        config_path=cfg_path,
        profile=profile,
        sources=sources,
        quarantine=quarantine,
        library_root=library_root,
        index_db=index_db,
        log_dir=log_dir,
        reports_dir=reports_dir,
        groups=groups,
        layout=layouts,
        exclude_dir_names=set(raw.get("exclude_dir_names") or DEFAULT_EXCLUDE),
        zip=ZipSettings(
            index_nested=bool(zip_raw.get("index_nested", True)),
            extract_on_apply=bool(zip_raw.get("extract_on_apply", False)),
            max_depth=int(zip_raw.get("max_depth") or 2),
        ),
        helpers=dict(raw.get("helpers") or {"fclones": "auto"}),
        apply=ApplySettings(
            default_op=default_op,
            never_modify_sources=bool(apply_raw.get("never_modify_sources", True)),
            quarantine_exact_dupes=bool(apply_raw.get("quarantine_exact_dupes", True)),
        ),
        serve=ServeSettings(
            host=str(serve_raw.get("host") or "127.0.0.1"),
            port=int(serve_raw.get("port") or 8765),
        ),
        hash_workers=max(1, int(raw.get("hash_workers") or 4)),
        ext_to_group=ext_to_group,
        protect_paths=[
            _resolve(p, cfg_dir).resolve() for p in (raw.get("protect_paths") or [])
        ],
    )


def validate_config(cfg: LibraryConfig) -> None:
    for src in cfg.sources:
        if not src.path.exists():
            raise ConfigError(f"source folder does not exist: {src.path}")
        if not src.path.is_dir():
            raise ConfigError(f"source path is not a folder: {src.path}")
        if path_is_under(src.path, cfg.library_root) or path_is_under(cfg.library_root, src.path):
            raise ConfigError(
                f"source and destination overlap: {src.path} vs {cfg.library_root}"
            )

    if not _can_create(cfg.library_root):
        raise ConfigError(f"cannot create destination: {cfg.library_root}")
    if cfg.library_root.exists() and not cfg.library_root.is_dir():
        raise ConfigError(f"destination is not a folder: {cfg.library_root}")

    cfg_file = cfg.config_path
    for root in cfg.managed_roots():
        if path_is_under(cfg_file, root):
            raise ConfigError(
                f"config file sits inside a source or destination tree ({root}). "
                f"Keep it somewhere else (for example the program folder) so a copy "
                f"or rename cannot overwrite it: {cfg_file}"
            )


def save_config(cfg: LibraryConfig, path: Path | str | None = None) -> Path:
    dest = Path(path).expanduser().resolve() if path else cfg.config_path
    staged = replace(cfg, config_path=dest)
    validate_config(staged)
    dump_path(dest, staged.as_save_dict())
    return dest


def setup_from_form(payload: dict[str, Any], *, save: bool) -> LibraryConfig:
    """Build a config from the UI/CLI setup form."""
    if not (payload.get("output_folder") or payload.get("library_root")):
        raise ConfigError("output folder is required")
    cfg_path_raw = payload.get("config_path") or (Path.cwd() / "library.jsonc")
    cfg_path = Path(cfg_path_raw).expanduser()
    if not cfg_path.is_absolute():
        cfg_path = Path.cwd() / cfg_path
    cfg_path = cfg_path.resolve()

    raw = dict(payload)
    if raw.get("output_folder") and not raw.get("library_root"):
        pass  # config_from_raw handles output_folder
    cfg = config_from_raw(raw, cfg_path)
    validate_config(cfg)
    if save:
        save_config(cfg, cfg_path)
        cfg = load_config(cfg_path)
    return cfg


def ensure_dirs(cfg: LibraryConfig) -> None:
    cfg.index_db.parent.mkdir(parents=True, exist_ok=True)
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    cfg.library_root.mkdir(parents=True, exist_ok=True)
    cfg.quarantine.mkdir(parents=True, exist_ok=True)
