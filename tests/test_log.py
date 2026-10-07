# SPDX-License-Identifier: AGPL-3.0-or-later
"""One JSONL file and one FINAL line per run."""

from __future__ import annotations

import json
import re

from lam.log import finish_run, log_event, log_file_event, start_run


def test_jsonl_fields_final_line_and_redaction(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("LAM_LOG_LEVEL", raising=False)
    run = start_run("scan", tmp_path)
    log_file_event(
        run,
        "info",
        "hash",
        tmp_path / "a.bin",
        size=3,
        ok=True,
        func="lam.scan.run_scan",
    )
    log_event(
        run,
        "info",
        "file",
        action="download",
        cookie="session_id=SECRETVALUE",
        note="prefix session_id=SECRETVALUE suffix",
    )
    line = finish_run(run, ok=1, skipped=2, failed=0)
    err = capsys.readouterr().err.strip()
    assert err == line
    assert line.startswith("FINAL ")
    assert f"run={run.run_id}" in line
    assert "command=scan" in line
    assert "ok=1" in line
    assert "skipped=2" in line
    assert "failed=0" in line
    assert f"log={run.path.resolve()}" in line
    assert re.fullmatch(r"scan-\d{8}T\d{6}-[0-9a-f]{12}\.jsonl", run.path.name)

    rows = [json.loads(item) for item in run.path.read_text(encoding="utf-8").splitlines() if item]
    assert [row["event"] for row in rows] == ["start", "file", "file", "summary"]
    for row in rows:
        assert set(row) >= {"ts", "level", "run_id", "command", "event"}
        assert row["run_id"] == run.run_id
        assert row["command"] == "scan"
        assert "T" in row["ts"]
        assert row["ts"][-6] in {"+", "-"}
    secret_row = rows[2]
    assert secret_row["cookie"] == "<redacted>"
    assert secret_row["note"] == "<redacted>"
    assert "SECRETVALUE" not in run.path.read_text(encoding="utf-8")
    assert rows[-1]["ok"] == 1
    assert rows[-1]["skipped"] == 2
    assert rows[-1]["failed"] == 0


def test_debug_lines_stay_out_when_floor_is_info(tmp_path, monkeypatch):
    monkeypatch.setenv("LAM_LOG_LEVEL", "info")
    run = start_run("apply", tmp_path)
    log_event(run, "debug", "progress", action="hash", path="cache-hit")
    text = run.path.read_text(encoding="utf-8")
    assert "cache-hit" not in text
    assert "debug" not in text
