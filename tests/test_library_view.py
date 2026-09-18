from lam.library_view import compare_grid, overview
from lam.scan import run_scan


def test_metadata_scan_finds_quick_collisions_and_overview(library_env):
    cfg = library_env["cfg"]
    stats = run_scan(cfg, deep=False)
    assert stats["files"] >= 7
    assert stats["deep"] is False
    ov = overview(cfg)
    assert ov["deep"] is False
    assert ov["last_scan_end"]
    assert ov["last_scan_duration_seconds"] >= 0
    grid = compare_grid(cfg)
    assert grid["rows"]
    rels = {r["relpath"] for r in grid["rows"]}
    assert any("token.png" in r for r in rels)


def test_deep_scan_still_groups_hash_dupes(library_env):
    cfg = library_env["cfg"]
    stats = run_scan(cfg, deep=True)
    assert stats["duplicate_groups"] >= 1
    ov = overview(cfg)
    assert ov["deep"] is True
    assert ov["space_to_save"] >= 0
