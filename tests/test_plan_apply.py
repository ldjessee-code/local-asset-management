from pathlib import Path

from lam.apply import run_apply
from lam.db import connect
from lam.plan import run_plan
from lam.scan import run_scan


def test_plan_quarantines_lower_priority_duplicate_and_apply_copies(library_env):
    cfg = library_env["cfg"]
    run_scan(cfg)
    summary = run_plan(cfg)
    assert summary["rows"] > 0
    assert summary["quarantine"] >= 1

    conn = connect(cfg.index_db)
    rows = list(conn.execute("SELECT src, dest, op, reason FROM plan_rows"))
    conn.close()
    quarantine_rows = [r for r in rows if r["op"] == "quarantine"]
    assert quarantine_rows
    assert any("downloads" in r["src"].replace("\\", "/") for r in quarantine_rows)

    keep = [r for r in rows if r["op"] != "quarantine" and str(r["src"]).endswith("token.png")]
    assert keep
    assert "/packs/Artist/Pack/token.png" in keep[0]["dest"].replace("\\", "/")

    stats = run_apply(cfg, yes=True)
    assert stats["failed"] == 0
    assert stats["copied"] + stats["quarantined"] + stats["hardlinked"] >= 1

    dest_token = cfg.library_root / "packs" / "Artist" / "Pack" / "token.png"
    assert dest_token.is_file()
    assert dest_token.read_bytes().startswith(b"\x89PNG")

    # sources are left in place
    assert (library_env["root"] / "src" / "patreon" / "Artist" / "Pack" / "token.png").is_file()
    assert (library_env["root"] / "src" / "downloads" / "token.png").is_file()

    q_hits = list(cfg.quarantine.rglob("token.png"))
    assert q_hits


def test_interrupted_copy_leaves_no_final_dest(library_env, monkeypatch):
    cfg = library_env["cfg"]
    run_scan(cfg)
    run_plan(cfg)
    dest_token = cfg.library_root / "packs" / "Artist" / "Pack" / "token.png"

    def fake(src, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(".lam-tmp-killed")
        tmp.write_bytes(b"partial")
        raise OSError("killed")

    monkeypatch.setattr("lam.apply._copy_via_temp", fake)
    stats = run_apply(cfg, yes=True)
    assert stats["failed"] >= 1
    assert not dest_token.exists()


def test_failed_replace_removes_temp_and_leaves_no_dest(library_env, monkeypatch):
    cfg = library_env["cfg"]
    run_scan(cfg)
    run_plan(cfg)
    dest_token = cfg.library_root / "packs" / "Artist" / "Pack" / "token.png"
    real_replace = __import__("os").replace

    def boom(src, dst):
        if ".lam-tmp-" in Path(src).name:
            raise OSError("killed")
        return real_replace(src, dst)

    monkeypatch.setattr("lam.apply.os.replace", boom)
    stats = run_apply(cfg, yes=True)
    assert stats["failed"] >= 1
    assert not dest_token.exists()
    assert list(cfg.library_root.rglob(".lam-tmp-*")) == []
    assert list(cfg.quarantine.rglob(".lam-tmp-*")) == []


def test_second_apply_skips_same_size(library_env):
    cfg = library_env["cfg"]
    run_scan(cfg)
    run_plan(cfg)
    first = run_apply(cfg, yes=True)
    assert first["failed"] == 0
    assert first["copied"] + first["quarantined"] + first["hardlinked"] >= 1
    second = run_apply(cfg, yes=True)
    assert second["failed"] == 0
    assert second["copied"] == 0
    assert second["hardlinked"] == 0
    assert second["quarantined"] == 0
    assert second["skipped"] >= 1


def test_existing_different_size_still_errors(library_env):
    cfg = library_env["cfg"]
    run_scan(cfg)
    run_plan(cfg)
    dest_token = cfg.library_root / "packs" / "Artist" / "Pack" / "token.png"
    dest_token.parent.mkdir(parents=True, exist_ok=True)
    dest_token.write_bytes(b"not-the-same-size")
    before = dest_token.read_bytes()
    stats = run_apply(cfg, yes=True)
    assert stats["failed"] >= 1
    assert dest_token.read_bytes() == before
    conn = connect(cfg.index_db)
    rows = list(conn.execute("SELECT dest, op, reason FROM actions_log"))
    conn.close()
    hit = [
        row
        for row in rows
        if row["dest"] and row["dest"].replace("\\", "/").endswith("packs/Artist/Pack/token.png")
    ]
    assert hit
    assert hit[0]["op"] == "error"
    assert "different size" in hit[0]["reason"]


def test_hardlink_oserror_is_recorded_then_copy(library_env, monkeypatch):
    cfg = library_env["cfg"]
    cfg.apply.default_op = "hardlink"
    run_scan(cfg)
    run_plan(cfg)

    def boom(src, dest):
        raise OSError("cross-device link")

    monkeypatch.setattr("lam.apply.os.link", boom)
    stats = run_apply(cfg, yes=True)
    assert stats["failed"] == 0
    assert stats["copied"] + stats["quarantined"] >= 1
    dest_token = cfg.library_root / "packs" / "Artist" / "Pack" / "token.png"
    assert dest_token.is_file()
    conn = connect(cfg.index_db)
    reasons = [row["reason"] for row in conn.execute("SELECT reason FROM actions_log")]
    conn.close()
    assert any("OSError" in reason and "cross-device link" in reason for reason in reasons)
