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
