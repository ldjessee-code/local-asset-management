from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from lam.actions import (
    PLAN_SCHEMA_ID,
    REASON_ALREADY,
    REASON_DST_EXISTS,
    REASON_DST_IS_FILE,
    REASON_GIT,
    REASON_MISSING_SRC,
    REASON_NOT_V1,
    REASON_RESTORE_MANUAL,
    REASON_SHA,
    REASON_STOP,
    PlanError,
    run_file_actions,
    undo_file_actions,
    validate_plan_document,
    validate_results_document,
)
from lam.cli import main
from lam.hashing import sha256_full
from start import resolve_lam_argv

REPO = Path(__file__).resolve().parents[1]
SAMPLE_PLAN = REPO / "examples" / "file-action-plan.sample.json"
SAMPLE_RESULTS = REPO / "examples" / "file-action-results.sample.json"


@pytest.fixture(autouse=True)
def fake_recycle(request, tmp_path, monkeypatch):
    if request.node.get_closest_marker("real_recycle"):
        yield None
        return
    trash = tmp_path / "_recycle_bin"
    trash.mkdir()

    def _recycle(path: Path) -> None:
        path = Path(path)
        dest = trash / path.name
        n = 1
        while dest.exists():
            dest = trash / f"{path.name}.{n}"
            n += 1
        os.rename(path, dest)

    monkeypatch.setattr("lam.actions.recycle_path", _recycle)
    yield trash


def _plan(path: Path, actions: list[dict], **extra) -> Path:
    payload = {"schema": PLAN_SCHEMA_ID, "actions": actions, **extra}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _run(plan_path: Path, tmp_path: Path, name: str, **kwargs):
    results_path = tmp_path / f"{name}.json"
    undo_log_path = kwargs.pop("undo_log_path", tmp_path / f"{name}.undo.csv")
    lines: list[str] = []
    results, code = run_file_actions(
        plan_path,
        results_path=results_path,
        undo_log_path=undo_log_path if kwargs.get("apply") else None,
        printer=lines.append,
        **kwargs,
    )
    return results, code, lines, results_path, undo_log_path


def _cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as caught:
        main(argv)
    return int(caught.value.code)


def test_samples_validate():
    plan = json.loads(SAMPLE_PLAN.read_text(encoding="utf-8"))
    assert validate_plan_document(plan) == []
    results = json.loads(SAMPLE_RESULTS.read_text(encoding="utf-8"))
    assert validate_results_document(results) == []


def test_each_op_dry_run_and_apply(tmp_path: Path, fake_recycle: Path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    move_src = src_dir / "move.txt"
    copy_src = src_dir / "copy.txt"
    rec_src = src_dir / "recycle.txt"
    move_src.write_text("move-me", encoding="utf-8")
    copy_src.write_text("copy-me", encoding="utf-8")
    rec_src.write_text("recycle-me", encoding="utf-8")
    move_dst = dst_dir / "moved.txt"
    copy_dst = dst_dir / "copied.txt"
    new_dir = dst_dir / "made"
    actions = [
        {"id": "m", "op": "move", "src": str(move_src), "dst": str(move_dst)},
        {"id": "c", "op": "copy", "src": str(copy_src), "dst": str(copy_dst)},
        {"id": "r", "op": "recycle", "src": str(rec_src)},
        {"id": "k", "op": "mkdir", "dst": str(new_dir)},
    ]
    plan = _plan(tmp_path / "plan.json", actions)

    dry, dry_code, _, _, _ = _run(plan, tmp_path, "dry")
    assert dry_code == 0
    assert dry["mode"] == "dry-run"
    assert dry["summary"]["would_ok"] == 4
    assert move_src.is_file() and not move_dst.exists()
    assert copy_src.is_file() and not copy_dst.exists()
    assert rec_src.is_file()
    assert not new_dir.exists()

    applied, code, _, _, undo_path = _run(plan, tmp_path, "apply", apply=True)
    assert code == 0
    assert applied["mode"] == "apply"
    assert applied["summary"]["ok"] == 4
    assert not move_src.exists() and move_dst.read_text(encoding="utf-8") == "move-me"
    assert copy_src.is_file() and copy_dst.read_text(encoding="utf-8") == "copy-me"
    assert not rec_src.exists()
    assert any(p.name.startswith("recycle.txt") for p in fake_recycle.iterdir())
    assert new_dir.is_dir()
    assert undo_path.is_file()


def test_sha256_match_and_mismatch(tmp_path: Path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"abc")
    digest = sha256_full(src)
    dst_ok = tmp_path / "ok.bin"
    dst_bad = tmp_path / "bad.bin"
    actions = [
        {"op": "copy", "src": str(src), "dst": str(dst_ok), "sha256": digest},
        {"op": "copy", "src": str(src), "dst": str(dst_bad), "sha256": "0" * 64},
    ]
    plan = _plan(tmp_path / "plan.json", actions)
    results, code, _, _, _ = _run(plan, tmp_path, "sha", apply=True)
    assert code == 1
    assert results["items"][0]["status"] == "ok"
    assert results["items"][1]["status"] == "failed"
    assert results["items"][1]["reason"] == REASON_SHA
    assert dst_ok.is_file()
    assert not dst_bad.exists()


def test_destination_exists_never_overwritten(tmp_path: Path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("new", encoding="utf-8")
    dst.write_text("keep-me", encoding="utf-8")
    plan = _plan(
        tmp_path / "plan.json",
        [{"op": "copy", "src": str(src), "dst": str(dst)}],
    )
    results, code, _, _, _ = _run(plan, tmp_path, "exists", apply=True)
    assert code == 1
    assert results["items"][0]["reason"] == REASON_DST_EXISTS
    assert dst.read_text(encoding="utf-8") == "keep-me"
    assert src.read_text(encoding="utf-8") == "new"

    move_plan = _plan(
        tmp_path / "move.json",
        [{"op": "move", "src": str(src), "dst": str(dst)}],
    )
    moved, mcode, _, _, _ = _run(move_plan, tmp_path, "exists-move", apply=True)
    assert mcode == 1
    assert moved["items"][0]["reason"] == REASON_DST_EXISTS
    assert dst.read_text(encoding="utf-8") == "keep-me"
    assert src.is_file()


def test_missing_source(tmp_path: Path):
    missing = tmp_path / "nope.txt"
    dst = tmp_path / "out.txt"
    plan = _plan(
        tmp_path / "plan.json",
        [{"op": "move", "src": str(missing), "dst": str(dst)}],
    )
    results, code, _, _, _ = _run(plan, tmp_path, "missing")
    assert code == 1
    assert results["items"][0]["status"] == "would_fail"
    assert results["items"][0]["reason"] == REASON_MISSING_SRC


def test_git_refusal_src_and_dst(tmp_path: Path):
    git_src = tmp_path / "repo" / ".git" / "HEAD"
    git_src.parent.mkdir(parents=True)
    git_src.write_text("ref", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    src_ok = tmp_path / "ok.txt"
    src_ok.write_text("x", encoding="utf-8")
    git_dst = tmp_path / "other" / ".GIT" / "ok.txt"
    plan = _plan(
        tmp_path / "plan.json",
        [
            {"op": "move", "src": str(git_src), "dst": str(out / "HEAD")},
            {"op": "copy", "src": str(src_ok), "dst": str(git_dst)},
        ],
    )
    results, code, _, _, _ = _run(plan, tmp_path, "git")
    assert code == 1
    assert results["items"][0]["reason"] == REASON_GIT
    assert results["items"][1]["reason"] == REASON_GIT
    assert git_src.is_file()
    assert src_ok.is_file()
    assert not (out / "HEAD").exists()


def test_mkdir_idempotent_and_over_file(tmp_path: Path):
    folder = tmp_path / "already"
    folder.mkdir()
    as_file = tmp_path / "blocked"
    as_file.write_text("nope", encoding="utf-8")
    plan = _plan(
        tmp_path / "plan.json",
        [
            {"op": "mkdir", "dst": str(folder)},
            {"op": "mkdir", "dst": str(as_file)},
        ],
    )
    results, code, _, results_path, undo_path = _run(plan, tmp_path, "mkdir", apply=True)
    assert code == 1
    assert results["items"][0]["status"] == "ok"
    assert results["items"][0]["reason"] == REASON_ALREADY
    assert results["items"][1]["status"] == "failed"
    assert results["items"][1]["reason"] == REASON_DST_IS_FILE
    assert as_file.is_file()
    text = undo_path.read_text(encoding="utf-8")
    assert "MKDIR" not in text.splitlines()[1:] if text.strip() else True
    rows = list(csv.reader(undo_path.open(newline="", encoding="utf-8")))
    assert rows[0] == ["action", "source", "destination", "size", "sha256", "timestamp"]
    assert len(rows) == 1


def test_plan_order_simulation(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    inbox = tmp_path / "inbox"
    dest = inbox / "a.txt"
    other = tmp_path / "b.txt"
    other.write_text("other", encoding="utf-8")
    plan = _plan(
        tmp_path / "plan.json",
        [
            {"op": "mkdir", "dst": str(inbox)},
            {"op": "move", "src": str(src), "dst": str(dest)},
            {"op": "copy", "src": str(other), "dst": str(dest)},
            {"op": "recycle", "src": str(src)},
        ],
    )
    dry, dry_code, _, _, _ = _run(plan, tmp_path, "order-dry")
    assert dry_code == 1
    assert dry["items"][0]["status"] == "would_ok"
    assert dry["items"][1]["status"] == "would_ok"
    assert dry["items"][2]["status"] == "would_fail"
    assert dry["items"][2]["reason"] == REASON_DST_EXISTS
    assert dry["items"][3]["status"] == "would_fail"
    assert dry["items"][3]["reason"] == REASON_MISSING_SRC
    assert src.is_file()
    assert not inbox.exists()

    applied, code, _, _, _ = _run(plan, tmp_path, "order-apply", apply=True)
    assert code == 1
    assert applied["items"][0]["status"] == "ok"
    assert applied["items"][1]["status"] == "ok"
    assert applied["items"][2]["status"] == "failed"
    assert applied["items"][3]["status"] == "failed"
    assert dest.read_text(encoding="utf-8") == "hello"
    assert not src.exists()
    assert other.is_file()


def test_stop_on_error(tmp_path: Path):
    src = tmp_path / "ok.txt"
    src.write_text("ok", encoding="utf-8")
    dst = tmp_path / "out.txt"
    missing = tmp_path / "missing.txt"
    plan = _plan(
        tmp_path / "plan.json",
        [
            {"op": "move", "src": str(missing), "dst": str(tmp_path / "x.txt")},
            {"op": "copy", "src": str(src), "dst": str(dst)},
        ],
    )
    results, code, _, _, _ = _run(plan, tmp_path, "stop", stop_on_error=True, apply=True)
    assert code == 1
    assert results["items"][0]["status"] == "failed"
    assert results["items"][1]["status"] == "skipped"
    assert results["items"][1]["reason"] == REASON_STOP
    assert not dst.exists()


def test_exit_codes_and_invalid_plans(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("a", encoding="utf-8")
    dst = tmp_path / "b.txt"
    good = _plan(tmp_path / "good.json", [{"op": "copy", "src": str(src), "dst": str(dst)}])
    results, code, _, results_path, _ = _run(good, tmp_path, "good-apply", apply=True)
    assert code == 0
    assert results["summary"]["ok"] == 1

    missing = _plan(
        tmp_path / "missing.json",
        [{"op": "copy", "src": str(tmp_path / "no.txt"), "dst": str(tmp_path / "z.txt")}],
    )
    _, bad_code, _, _, _ = _run(missing, tmp_path, "missing-code")
    assert bad_code == 1

    cases = [
        (
            "bad-schema",
            {"schema": "file-action-plan/v0", "actions": []},
        ),
        (
            "relative",
            {
                "schema": PLAN_SCHEMA_ID,
                "actions": [{"op": "move", "src": "relative\\a.txt", "dst": str(tmp_path / "a.txt")}],
            },
        ),
        (
            "unknown-op",
            {
                "schema": PLAN_SCHEMA_ID,
                "actions": [{"op": "delete", "src": str(src)}],
            },
        ),
        (
            "missing-field",
            {
                "schema": PLAN_SCHEMA_ID,
                "actions": [{"op": "move", "src": str(src)}],
            },
        ),
    ]
    for name, payload in cases:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(PlanError):
            run_file_actions(path, results_path=tmp_path / f"{name}-out.json", printer=lambda _s: None)
        assert _cli(["actions", "run", str(path), "--results", str(tmp_path / f"{name}-cli.json")]) == 2


def test_results_json_shape(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("a", encoding="utf-8")
    dst = tmp_path / "b.txt"
    plan = _plan(tmp_path / "plan.json", [{"id": "c1", "op": "copy", "src": str(src), "dst": str(dst)}])
    results, code, _, results_path, _ = _run(plan, tmp_path, "shape", apply=True)
    assert code == 0
    loaded = json.loads(results_path.read_text(encoding="utf-8"))
    assert validate_results_document(loaded) == []
    assert loaded["schema"] == "file-action-results/v1"
    assert loaded["mode"] == "apply"
    assert loaded["plan"]
    assert loaded["started"]
    assert loaded["finished"]
    assert set(loaded["summary"]) == {"total", "ok", "would_ok", "failed", "would_fail", "skipped"}
    item = loaded["items"][0]
    assert item["index"] == 0
    assert item["id"] == "c1"
    assert item["op"] == "copy"
    assert item["status"] == "ok"
    assert item["size"] == 1
    assert item["sha256"] == sha256_full(dst)


def test_undo_csv_header_quoting_and_uppercase(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_bytes(b"xyz")
    dst = tmp_path / "b.txt"
    digest = sha256_full(src)
    plan = _plan(
        tmp_path / "plan.json",
        [{"op": "move", "src": str(src), "dst": str(dst), "sha256": digest}],
    )
    _, code, _, _, undo_path = _run(plan, tmp_path, "csv", apply=True)
    assert code == 0
    raw = undo_path.read_text(encoding="utf-8")
    first = raw.splitlines()[0]
    assert first == '"action","source","destination","size","sha256","timestamp"'
    rows = list(csv.reader(undo_path.open(newline="", encoding="utf-8")))
    assert rows[1][0] == "MOVE"
    assert rows[1][3] == "3"
    assert rows[1][4] == digest.upper()
    assert rows[1][4] == rows[1][4].upper()
    assert all(c in "0123456789ABCDEF" for c in rows[1][4])


def test_undo_round_trip_move_and_copy(tmp_path: Path, fake_recycle: Path):
    move_src = tmp_path / "from.txt"
    move_dst = tmp_path / "to.txt"
    copy_src = tmp_path / "orig.txt"
    copy_dst = tmp_path / "dup.txt"
    move_src.write_text("moved-bytes", encoding="utf-8")
    copy_src.write_text("copied-bytes", encoding="utf-8")
    plan = _plan(
        tmp_path / "plan.json",
        [
            {"op": "move", "src": str(move_src), "dst": str(move_dst)},
            {"op": "copy", "src": str(copy_src), "dst": str(copy_dst)},
        ],
    )
    _, code, _, _, undo_path = _run(plan, tmp_path, "round", apply=True)
    assert code == 0
    assert move_dst.is_file() and not move_src.exists()
    assert copy_src.is_file() and copy_dst.is_file()

    dry, dcode, _, _, _ = _undo(undo_path, tmp_path, "undo-dry")
    assert dcode == 0
    assert dry["items"][0]["op"] == "copy"
    assert dry["items"][0]["status"] == "would_ok"
    assert dry["items"][1]["op"] == "move"
    assert dry["items"][1]["status"] == "would_ok"
    assert move_dst.is_file() and copy_dst.is_file()

    applied, acode, _, _, _ = _undo(undo_path, tmp_path, "undo-apply", apply=True)
    assert acode == 0
    assert applied["items"][0]["status"] == "ok"
    assert applied["items"][1]["status"] == "ok"
    assert move_src.read_text(encoding="utf-8") == "moved-bytes"
    assert not move_dst.exists()
    assert copy_src.is_file()
    assert not copy_dst.exists()
    assert any(p.name.startswith("dup.txt") for p in fake_recycle.iterdir())


def _undo(undo_path: Path, tmp_path: Path, name: str, **kwargs):
    results_path = tmp_path / f"{name}.json"
    lines: list[str] = []
    results, code = undo_file_actions(
        undo_path,
        results_path=results_path,
        printer=lines.append,
        **kwargs,
    )
    return results, code, lines, results_path, None


def test_undo_reads_quoted_prior_art_format(tmp_path: Path):
    src = tmp_path / "route_table.csv"
    dst_dir = tmp_path / "inbox"
    dst_dir.mkdir()
    dst = dst_dir / "route_table.csv"
    src.write_bytes(b"x" * 20)
    digest = sha256_full(src).upper()
    os.rename(src, dst)
    log = tmp_path / "undo.csv"
    log.write_text(
        '"action","source","destination","size","sha256","timestamp"\n'
        f'"MOVE","{src}","{dst}","20","{digest}","2026-09-28T13:22:21-04:00"\n',
        encoding="utf-8",
    )
    results, code, _, _, _ = _undo(log, tmp_path, "prior-art", apply=True)
    assert code == 0
    assert src.is_file()
    assert not dst.exists()
    assert results["items"][0]["op"] == "move"


def test_undo_recycle_is_manual(tmp_path: Path):
    log = tmp_path / "undo.csv"
    gone = tmp_path / "gone.txt"
    log.write_text(
        '"action","source","destination","size","sha256","timestamp"\n'
        f'"RECYCLE","{gone}","","4","","2026-09-28T13:22:21-04:00"\n',
        encoding="utf-8",
    )
    results, code, _, _, _ = _undo(log, tmp_path, "recycle-undo")
    assert code == 0
    assert results["items"][0]["status"] == "skipped"
    assert results["items"][0]["reason"] == REASON_RESTORE_MANUAL


def test_directory_move_same_volume(tmp_path: Path):
    folder = tmp_path / "pack"
    folder.mkdir()
    inner = folder / "token.txt"
    inner.write_text("tok", encoding="utf-8")
    dest = tmp_path / "relocated"
    plan = _plan(
        tmp_path / "plan.json",
        [{"op": "move", "src": str(folder), "dst": str(dest)}],
    )
    results, code, _, _, _ = _run(plan, tmp_path, "dir-move", apply=True)
    assert code == 0
    assert not folder.exists()
    assert (dest / "token.txt").read_text(encoding="utf-8") == "tok"
    assert results["items"][0]["sha256"] is None


def test_copy_directory_not_supported(tmp_path: Path):
    folder = tmp_path / "dir"
    folder.mkdir()
    plan = _plan(
        tmp_path / "plan.json",
        [{"op": "copy", "src": str(folder), "dst": str(tmp_path / "out")}],
    )
    results, code, _, _, _ = _run(plan, tmp_path, "copydir")
    assert code == 1
    assert results["items"][0]["reason"] == REASON_NOT_V1


def test_cli_dry_run_exit_and_start_argv(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("a", encoding="utf-8")
    dst = tmp_path / "b.txt"
    plan = _plan(tmp_path / "plan.json", [{"op": "copy", "src": str(src), "dst": str(dst)}])
    results_path = tmp_path / "cli-results.json"
    assert _cli(["actions", "run", str(plan), "--results", str(results_path)]) == 0
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry-run"
    assert not dst.exists()
    assert resolve_lam_argv(["actions", "run", "x.json"]) == ["actions", "run", "x.json"]


def test_cli_missing_plan_exit_2(tmp_path: Path):
    assert _cli(["actions", "run", str(tmp_path / "no-such.json")]) == 2


@pytest.mark.real_recycle
@pytest.mark.skipif(
    os.environ.get("LAM_TEST_REAL_RECYCLE") != "1",
    reason="LAM_TEST_REAL_RECYCLE not set",
)
def test_real_recycle_optional(tmp_path: Path):
    src = tmp_path / "real.txt"
    src.write_text("to-bin", encoding="utf-8")
    plan = _plan(tmp_path / "plan.json", [{"op": "recycle", "src": str(src)}])
    results, code, _, _, _ = _run(plan, tmp_path, "real-recycle", apply=True)
    assert code == 0
    assert not src.exists()
    assert results["items"][0]["status"] == "ok"
