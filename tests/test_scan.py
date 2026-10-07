import json
from pathlib import Path

from lam.report import write_reports
from lam.scan import run_scan
from lam.walk import walk_files


def test_scan_indexes_dupes_zip_and_sidecar(library_env):
    cfg = library_env["cfg"]
    stats = run_scan(cfg)
    assert stats["files"] >= 7
    assert stats["duplicate_groups"] >= 1
    assert stats["zips"] == 1
    assert stats["archive_entries"] >= 3  # token, readme, nested zip + inner

    data = write_reports(cfg)
    summary = Path(data["summary_path"]).read_text(encoding="utf-8")
    exceptions = Path(data["exceptions_path"]).read_text(encoding="utf-8")
    assert "Exact duplicate groups" in summary
    assert "Artist" in summary or "patreon" in summary.lower()
    assert "Zip pairing" in exceptions


def test_scan_records_walk_error_and_one_final_line(library_env, capsys, monkeypatch):
    cfg = library_env["cfg"]

    def wrapped(root, exclude_names, on_error=None):
        if on_error is not None:
            on_error(str(Path(root) / "locked.bin"), "PermissionError: locked")
        yield from walk_files(root, exclude_names, on_error=on_error)

    monkeypatch.setattr("lam.scan.walk_files", wrapped)
    stats = run_scan(cfg)
    err = capsys.readouterr().err
    finals = [line for line in err.splitlines() if line.startswith("FINAL ")]
    assert len(finals) == 1
    assert f"failed={stats['errors']}" in finals[0]
    assert "command=scan" in finals[0]
    assert stats["errors"] >= 1
    logs = sorted(cfg.log_dir.glob("scan-*.jsonl"))
    assert logs
    rows = [json.loads(line) for line in logs[-1].read_text(encoding="utf-8").splitlines() if line]
    assert any(row.get("event") == "file" and "locked.bin" in str(row.get("path")) for row in rows)
    assert rows[-1]["event"] == "summary"
    assert rows[-1]["failed"] == stats["errors"]
