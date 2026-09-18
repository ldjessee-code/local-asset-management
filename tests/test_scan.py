from pathlib import Path

from lam.report import write_reports
from lam.scan import run_scan


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
    assert (cfg.reports_dir / "summary.md").is_file()
