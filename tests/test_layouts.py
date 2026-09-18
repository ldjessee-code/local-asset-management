from pathlib import Path

from lam.config import load_config
from lam.layouts import dest_path, tokens_for
from lam.util import split_bundle_pack


def test_bundle_skips_date_folders():
    bundle, pack, rest = split_bundle_pack("2024/01/shot.png")
    assert bundle == "_unknown"
    assert pack == "_root"
    assert rest == "shot.png"


def test_pack_tree_tokens(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "lib"
    dest.mkdir()
    cfg_path = tmp_path / "c.jsonc"
    cfg_path.write_text(
        '{"sources":[{"path":"src","layout":"pack_tree"}],"library_root":"lib"}',
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    tok = tokens_for(
        cfg,
        source_name="patreon",
        relpath="Artist/Pack/tokens/orc.png",
        stem="orc",
        ext=".png",
        bundle="Artist",
        pack="Pack",
        relpath_from_pack="tokens/orc.png",
        year="2024",
        month="09",
    )
    dest = dest_path(cfg, "pack_tree", tok)
    assert dest.as_posix().endswith("/packs/Artist/Pack/tokens/orc.png")
