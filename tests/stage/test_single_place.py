# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-file lam stage FILE and lam place STAGED DEST.

Test list:
- stage --json writes a copy and prints lam-place/v1
- place --new on a missing dest
- place --new refused when dest exists (exit 1)
- --expect-sha256 match: backup, verified sha, old file recycled after verify, JSON fields
- sha mismatch refused (exit 1) and dest untouched
- neither flag or both flags exit 2
- --dry-run changes nothing
- --backup-dir is where the backup is written
- --root and --grant refusals exit 1
- recycle failure after a verified copy keeps the new dest, the backup, and the old bytes
- verify failure restores the old dest and does not recycle
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lam.cli import main
from lam.hashing import sha256_full
from lam.schemas.registry import PLACE_RESULT_SCHEMA_ID, PLACE_RESULT_SCHEMAS

FIELDS = (
    "status",
    "dest",
    "staged",
    "sha256",
    "old_sha256",
    "backup_path",
    "trash_path",
    "size",
    "message",
)


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    try:
        main(argv)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    else:
        code = 0
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _payload(text: str) -> dict:
    return json.loads(text)


def _files(root: Path) -> list[tuple[str, bytes]]:
    found: list[tuple[str, bytes]] = []
    if not root.exists():
        return found
    for path in sorted(root.rglob("*")):
        if path.is_file():
            found.append((str(path), path.read_bytes()))
    return found


def test_schema_id_loads():
    assert PLACE_RESULT_SCHEMA_ID == "lam-place/v1"
    assert PLACE_RESULT_SCHEMAS[PLACE_RESULT_SCHEMA_ID].load_document()["title"] == "lam-place/v1"


def test_stage_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = tmp_path / "note.md"
    body = b"alpha\n"
    source.write_bytes(body)
    root = tmp_path / "staging"
    code, out, _err = _run(
        ["stage", str(source), "--root", str(root), "--json"],
        capsys,
    )
    assert code == 0
    payload = _payload(out)
    assert payload["schema"] == "lam-place/v1"
    for field in FIELDS:
        assert field in payload
    staged = Path(payload["staged"])
    assert staged.is_file()
    assert staged.read_bytes() == body
    assert payload["sha256"] == sha256_full(source)
    assert payload["size"] == len(body)
    assert payload["status"] == "staged"
    assert payload["dest"] is None
    assert payload["old_sha256"] is None
    assert payload["backup_path"] is None
    assert payload["trash_path"] is None
    assert source.read_bytes() == body
    assert root in staged.parents


def test_place_new_on_missing_dest(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "lore" / "note.md"
    recycled: list[Path] = []

    def boom(path: Path) -> None:
        recycled.append(Path(path))

    monkeypatch.setattr("lam.stage.single.default_recycle", boom)
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--new", "--json"],
        capsys,
    )
    assert code == 0
    assert recycled == []
    payload = _payload(out)
    for field in FIELDS:
        assert field in payload
    assert payload["schema"] == "lam-place/v1"
    assert payload["status"] == "placed"
    assert dest.read_bytes() == b"new-bytes"
    assert payload["sha256"] == sha256_full(dest) == sha256_full(staged)
    assert payload["old_sha256"] is None
    assert payload["backup_path"] is None
    assert payload["trash_path"] is None
    assert payload["size"] == len(b"new-bytes")
    assert staged.read_bytes() == b"new-bytes"


def test_place_new_refuses_existing_dest(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    recycled: list[Path] = []
    monkeypatch.setattr("lam.stage.single.default_recycle", lambda path: recycled.append(Path(path)))
    before = _files(tmp_path)
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--new", "--json"],
        capsys,
    )
    assert code == 1
    assert _files(tmp_path) == before
    assert dest.read_bytes() == b"old-bytes"
    assert recycled == []
    payload = _payload(out)
    assert payload["status"] == "refused"
    assert "dest exists" in payload["message"]


def test_expect_sha_match_recycles_after_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    old = sha256_full(dest)
    seen: list[tuple[bytes, str, str]] = []

    def fake_recycle(path: Path) -> None:
        seen.append((dest.read_bytes(), sha256_full(dest), Path(path).name))
        Path(path).unlink()

    monkeypatch.setattr("lam.stage.single.default_recycle", fake_recycle)
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", old, "--json"],
        capsys,
    )
    assert code == 0, _err
    payload = _payload(out)
    assert payload["schema"] == "lam-place/v1"
    for field in FIELDS:
        assert field in payload
    assert payload["status"] == "placed"
    assert payload["sha256"] == sha256_full(dest) == sha256_full(staged)
    assert payload["old_sha256"] == old
    assert payload["trash_path"] is None
    assert payload["size"] == dest.stat().st_size
    backup = Path(str(payload["backup_path"]))
    assert backup.is_file()
    assert backup.read_bytes() == b"old-bytes"
    assert dest.read_bytes() == b"new-bytes"
    assert seen == [(b"new-bytes", sha256_full(staged), dest.name + ".~place.old")]
    assert not Path(str(dest) + ".~place.old").exists()


def test_expect_sha_mismatch_leaves_dest(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    recycled: list[Path] = []
    monkeypatch.setattr("lam.stage.single.default_recycle", lambda path: recycled.append(Path(path)))
    before = dest.read_bytes()
    snapshot = _files(tmp_path)
    wrong = "ab" * 32
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", wrong, "--json"],
        capsys,
    )
    assert code == 1
    assert dest.read_bytes() == before
    assert _files(tmp_path) == snapshot
    assert recycled == []
    payload = _payload(out)
    assert payload["status"] == "refused"
    assert "does not match" in payload["message"]
    assert payload["backup_path"] is None
    assert payload["sha256"] == sha256_full(dest)


def test_neither_flag_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    code, _out, err = _run(["place", str(staged), str(dest)], capsys)
    assert code == 2
    assert "expect-sha256" in err or "--new" in err
    assert not dest.exists()


def test_both_flags_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    code, _out, err = _run(
        ["place", str(staged), str(dest), "--new", "--expect-sha256", "cd" * 32],
        capsys,
    )
    assert code == 2
    assert "only one" in err
    assert dest.read_bytes() == b"old-bytes"


def test_dry_run_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    old = sha256_full(dest)
    recycled: list[Path] = []
    monkeypatch.setattr("lam.stage.single.default_recycle", lambda path: recycled.append(Path(path)))
    snapshot = _files(tmp_path)
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", old, "--dry-run", "--json"],
        capsys,
    )
    assert code == 0
    assert _files(tmp_path) == snapshot
    assert recycled == []
    payload = _payload(out)
    assert payload["status"] == "dry-run"
    assert payload["backup_path"] is None
    assert payload["trash_path"] is None
    assert payload["sha256"] == sha256_full(staged)
    assert payload["old_sha256"] == old


def test_backup_dir_honored(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    old = sha256_full(dest)
    backup_dir = tmp_path / "backs"

    def fake_recycle(path: Path) -> None:
        Path(path).unlink()

    monkeypatch.setattr("lam.stage.single.default_recycle", fake_recycle)
    code, out, err = _run(
        [
            "place",
            str(staged),
            str(dest),
            "--expect-sha256",
            old,
            "--backup-dir",
            str(backup_dir),
            "--json",
        ],
        capsys,
    )
    assert code == 0, err
    payload = _payload(out)
    backup = Path(str(payload["backup_path"]))
    assert backup.parent == backup_dir
    assert backup.read_bytes() == b"old-bytes"
    assert dest.read_bytes() == b"new-bytes"
    assert list(dest.parent.glob("*.bak")) == []


def test_root_guard_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    dropbox = tmp_path / "Dropbox"
    dropbox.mkdir()
    monkeypatch.setenv("LAM_DROPBOX_ROOT", str(dropbox))
    source = tmp_path / "note.md"
    source.write_bytes(b"alpha\n")
    code, _out, err = _run(
        ["stage", str(source), "--root", str(dropbox / "stage"), "--json"],
        capsys,
    )
    assert code == 1
    assert "Dropbox" in err
    assert not (dropbox / "stage").exists()

    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "lore" / "note.md"
    code, _out, err = _run(
        ["place", str(staged), str(dest), "--new", "--root", str(tmp_path / "other"), "--json"],
        capsys,
    )
    assert code == 1
    assert "outside --root" in err
    assert not dest.exists()


def test_grant_guard_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "lore" / "note.md"
    code, _out, err = _run(
        ["place", str(staged), str(dest), "--new", "--grant", str(tmp_path / "allowed"), "--json"],
        capsys,
    )
    assert code == 1
    assert "outside --grant" in err
    assert not dest.exists()

    code, _out, err = _run(
        ["place", str(staged), r"C:\Example\lore\note.md", "--new", "--json"],
        capsys,
    )
    assert code == 1
    assert "example path" in err


def test_recycle_failure_keeps_data(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    old = sha256_full(dest)

    def boom(_path: Path) -> None:
        raise OSError("bin unavailable")

    monkeypatch.setattr("lam.stage.single.default_recycle", boom)
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", old, "--json"],
        capsys,
    )
    assert code == 1
    payload = _payload(out)
    assert payload["status"] == "failed"
    assert "recycle failed" in payload["message"]
    assert dest.read_bytes() == b"new-bytes"
    assert payload["sha256"] == sha256_full(dest)
    backup = Path(str(payload["backup_path"]))
    assert backup.read_bytes() == b"old-bytes"
    holding = Path(str(dest) + ".~place.old")
    assert holding.is_file()
    assert holding.read_bytes() == b"old-bytes"


def test_verify_failure_restores_and_does_not_recycle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    old = sha256_full(dest)
    recycled: list[Path] = []
    real = sha256_full

    def fake_hash(path: Path) -> str:
        target = os_norm(dest)
        if os_norm(Path(path)) == target and Path(path).read_bytes() == b"new-bytes":
            return "f" * 64
        return real(path)

    def os_norm(path: Path) -> str:
        return str(path.resolve()).casefold()

    monkeypatch.setattr("lam.stage.single.sha256_full", fake_hash)
    monkeypatch.setattr("lam.stage.single.default_recycle", lambda path: recycled.append(Path(path)))
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", old, "--json"],
        capsys,
    )
    assert code == 1
    assert dest.read_bytes() == b"old-bytes"
    assert recycled == []
    assert not Path(str(dest) + ".~place.old").exists()
    payload = _payload(out)
    assert payload["status"] == "failed"
    assert "verify failed" in payload["message"]
    assert "restored" in payload["message"]
