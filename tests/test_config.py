from pathlib import Path

import pytest

from lam.config import ConfigError, find_default_config, load_config, setup_from_form


def _write_cfg(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_find_default_config_in_cwd(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LAM_CONFIG", raising=False)
    assert find_default_config() is None
    cfg = tmp_path / "library.jsonc"
    cfg.write_text("{}", encoding="utf-8")
    assert find_default_config() == cfg.resolve()


def test_rejects_missing_source(tmp_path: Path):
    src = tmp_path / "missing"
    dest_home = tmp_path / "out"
    dest_home.mkdir()
    cfg = _write_cfg(
        tmp_path / "library.jsonc",
        f'{{"sources":[{{"path":"{src.as_posix()}"}}],"library_root":"{(dest_home / "media").as_posix()}"}}',
    )
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(cfg)


def test_rejects_config_inside_destination(tmp_path: Path):
    src = tmp_path / "pics"
    src.mkdir()
    dest_home = tmp_path / "out"
    dest_home.mkdir()
    cfg = _write_cfg(
        dest_home / "library.jsonc",
        f'{{"sources":[{{"path":"{src.as_posix()}"}}],"library_root":"{(dest_home / "media").as_posix()}"}}',
    )
    with pytest.raises(ConfigError, match="inside a source or destination"):
        load_config(cfg)


def test_rejects_config_inside_source(tmp_path: Path):
    src = tmp_path / "pics"
    src.mkdir()
    dest_home = tmp_path / "out"
    dest_home.mkdir()
    cfg = _write_cfg(
        src / "library.jsonc",
        f'{{"sources":[{{"path":"{src.as_posix()}"}}],"library_root":"{(dest_home / "media").as_posix()}"}}',
    )
    with pytest.raises(ConfigError, match="inside a source or destination"):
        load_config(cfg)


def test_protect_paths_round_trip(tmp_path: Path):
    src = tmp_path / "pics"
    src.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    locked = tmp_path / "keep"
    locked.mkdir()
    cfg = _write_cfg(
        tmp_path / "library.jsonc",
        f"""{{
          "sources": [{{"path": "{src.as_posix()}"}}],
          "library_root": "{(dest / "media").as_posix()}",
          "protect_paths": ["{locked.as_posix()}"]
        }}""",
    )
    loaded = load_config(cfg)
    assert loaded.protect_paths == [locked.resolve()]


def test_setup_from_form_saves_outside_trees(tmp_path: Path):
    src = tmp_path / "pics"
    src.mkdir()
    (src / "a.png").write_bytes(b"png")
    dest = tmp_path / "out"
    dest.mkdir()
    cfg_path = tmp_path / "library.jsonc"
    cfg = setup_from_form(
        {
            "config_path": str(cfg_path),
            "output_folder": str(dest),
            "sources": [{"path": str(src), "layout": "keep_relpath", "priority": 10}],
        },
        save=True,
    )
    assert cfg_path.is_file()
    assert cfg.library_root == (dest / "media").resolve()
    loaded = load_config(cfg_path)
    assert loaded.sources[0].path == src.resolve()
