"""Console-encoding safety for `lam actions run` (kb-55 U+200A crash, 2026-10-15)."""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

from lam.actions import PLAN_SCHEMA_ID, run_file_actions

HAIR = "\u200a"


def _plan(tmp_path: Path, n: int = 2) -> tuple[Path, Path]:
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    actions = []
    for i in range(n):
        name = f"Map{i}{HAIR}Garage.png" if i == n - 1 else f"Map{i}.png"
        (src_dir / name).write_bytes(b"x" * (i + 1))
        actions.append({"op": "copy", "src": str(src_dir / name), "dst": str(dst_dir / name)})
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"schema": PLAN_SCHEMA_ID, "actions": actions}), encoding="utf-8")
    return plan, dst_dir


def _cp1252_printer(line: str) -> None:
    line.encode("cp1252")  # raises UnicodeEncodeError like a cp1252 console


def _undo_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.reader(fh))[1:]


def test_print_failure_does_not_lose_results_or_undo(tmp_path):
    plan, dst = _plan(tmp_path)
    results_path = tmp_path / "r.json"
    undo = tmp_path / "r.undo.csv"
    results, code = run_file_actions(
        plan, apply=True, results_path=results_path, undo_log_path=undo, printer=_cp1252_printer
    )
    assert code == 0
    assert results["summary"]["ok"] == 2
    assert results_path.is_file()
    assert len(_undo_rows(undo)) == 2
    assert len(list(dst.iterdir())) == 2


def test_results_and_undo_written_even_if_loop_raises(tmp_path, monkeypatch):
    plan, _ = _plan(tmp_path, n=3)
    results_path = tmp_path / "r.json"
    undo = tmp_path / "r.undo.csv"
    import lam.actions as A

    real = A._execute
    calls = {"n": 0}

    def boom(prep, rec):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return real(prep, rec)

    monkeypatch.setattr(A, "_execute", boom)
    try:
        run_file_actions(plan, apply=True, results_path=results_path, undo_log_path=undo, printer=lambda s: None)
    except KeyboardInterrupt:
        pass
    assert results_path.is_file()
    data = json.loads(results_path.read_text(encoding="utf-8"))
    assert data["summary"]["ok"] == 2
    assert len(_undo_rows(undo)) == 2


def test_cli_cp1252_console_does_not_crash(tmp_path):
    plan, dst = _plan(tmp_path)
    env = {**__import__("os").environ, "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.run(
        [sys.executable, "-m", "lam", "actions", "run", str(plan), "--apply",
         "--results", str(tmp_path / "r.json"), "--undo-log", str(tmp_path / "u.csv")],
        capture_output=True, env=env, cwd=Path(__file__).resolve().parents[1],
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert (tmp_path / "r.json").is_file()
    assert len(_undo_rows(tmp_path / "u.csv")) == 2
