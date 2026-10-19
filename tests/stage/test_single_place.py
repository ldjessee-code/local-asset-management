# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-file lam stage FILE and lam place STAGED DEST.

Shared contract place/v1 (tool lam):
- stage --json and place --json print place/v1 with tool lam
- statuses: placed, dry-run, staged, conflict, sha-mismatch, refused, failed
- exit 0 ok; exit 1 refused, conflict, sha-mismatch, or failed; exit 2 usage only
- bad hex and a missing staged file are exit 1 refused
- sha256 is null on refused, conflict, and sha-mismatch
- sha256 is the staged sha on dry-run and the final dest sha on placed
- old_sha256 is the dest hash on sha-mismatch and on a replace dry-run
- size is the staged file size on dry-run, conflict, and sha-mismatch
- size is null when the staged file is missing or the hex is bad
- trash_path is null
- recycle failure is exit 1 and keeps the new dest
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.cli import main
from lam.hashing import sha256_full
from lam.schemas.registry import (
    PLACE_MANIFEST_SCHEMA_ID,
    PLACE_RESULT_SCHEMA_ID,
    PLACE_RESULT_SCHEMAS,
    SCHEMA_DIR,
)

FIELDS = (
    "schema",
    "tool",
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

STATUSES = (
    "placed",
    "dry-run",
    "staged",
    "conflict",
    "sha-mismatch",
    "refused",
    "failed",
)

REPO = Path(__file__).resolve().parents[2]


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


def _assert_shape(payload: dict) -> None:
    for field in FIELDS:
        assert field in payload
    assert payload["schema"] == "place/v1"
    assert payload["tool"] == "lam"
    assert payload["status"] in STATUSES
    assert payload["trash_path"] is None


def test_schema_id_loads():
    assert PLACE_MANIFEST_SCHEMA_ID == "lam-place-manifest/v1"
    assert PLACE_RESULT_SCHEMA_ID == "place/v1"
    document = PLACE_RESULT_SCHEMAS[PLACE_RESULT_SCHEMA_ID].load_document()
    assert document["title"] == "place/v1"
    assert document["properties"]["schema"]["const"] == "place/v1"
    assert document["properties"]["tool"]["enum"] == ["lam", "soay"]
    assert document["properties"]["status"]["enum"] == list(STATUSES)
    assert document["required"] == list(FIELDS)
    assert (SCHEMA_DIR / "place.v1.schema.json").is_file()
    assert not (SCHEMA_DIR / "lam-place.v1.schema.json").exists()


def test_capabilities_and_docs_name_place_v1():
    cap = describe_capabilities()
    assert cap["staging"]["place_schema"] == "place/v1"
    text = format_capabilities_text(cap)
    assert "place/v1" in text
    assert "lam-place/v1" not in text
    for rel in ("AGENTS.md", "README.md", "docs/staging.md", "docs/architecture.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "place/v1" in body
        assert "lam-place/v1" not in body
    assert "lam-place-manifest/v1" in (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "lam-place-manifest/v1" in (REPO / "docs" / "staging.md").read_text(encoding="utf-8")


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
    _assert_shape(payload)
    staged = Path(payload["staged"])
    assert staged.is_file()
    assert staged.read_bytes() == body
    assert payload["sha256"] == sha256_full(source)
    assert payload["size"] == len(body)
    assert payload["status"] == "staged"
    assert payload["message"] == "staged"
    assert payload["dest"] is None
    assert payload["old_sha256"] is None
    assert payload["backup_path"] is None
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
    _assert_shape(payload)
    assert payload["status"] == "placed"
    assert payload["message"] == "placed"
    assert dest.read_bytes() == b"new-bytes"
    assert payload["sha256"] == sha256_full(dest) == sha256_full(staged)
    assert payload["old_sha256"] is None
    assert payload["backup_path"] is None
    assert payload["size"] == len(b"new-bytes")
    assert staged.read_bytes() == b"new-bytes"


def test_place_new_existing_dest_is_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old")
    recycled: list[Path] = []
    monkeypatch.setattr("lam.stage.single.default_recycle", lambda path: recycled.append(Path(path)))
    before = _files(tmp_path)
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--new", "--json"],
        capsys,
    )
    assert code == 1
    assert _files(tmp_path) == before
    assert dest.read_bytes() == b"old"
    assert recycled == []
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "conflict"
    assert payload["message"] == "conflict: dest exists"
    assert payload["sha256"] is None
    assert payload["size"] == len(b"new-bytes")
    assert payload["backup_path"] is None


def test_dry_run_new_on_existing_is_still_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old")
    before = _files(tmp_path)
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--new", "--dry-run", "--json"],
        capsys,
    )
    assert code == 1
    assert _files(tmp_path) == before
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "conflict"
    assert payload["message"] == "conflict: dest exists"
    assert payload["sha256"] is None
    assert payload["size"] == len(b"new-bytes")


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
    _assert_shape(payload)
    assert payload["status"] == "placed"
    assert payload["message"] == "placed"
    assert payload["sha256"] == sha256_full(dest) == sha256_full(staged)
    assert payload["old_sha256"] == old
    assert payload["size"] == dest.stat().st_size == len(b"new-bytes")
    backup = Path(str(payload["backup_path"]))
    assert backup.is_file()
    assert backup.read_bytes() == b"old-bytes"
    assert dest.read_bytes() == b"new-bytes"
    assert seen == [(b"new-bytes", sha256_full(staged), dest.name + ".~place.old")]
    assert not Path(str(dest) + ".~place.old").exists()


def test_expect_sha_uppercase_hex_matches(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    old = sha256_full(dest)
    monkeypatch.setattr("lam.stage.single.default_recycle", lambda path: Path(path).unlink())
    code, out, err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", old.upper(), "--json"],
        capsys,
    )
    assert code == 0, err
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "placed"
    assert payload["message"] == "placed"
    assert dest.read_bytes() == b"new-bytes"


def test_expect_sha_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"older-bytes")
    recycled: list[Path] = []
    monkeypatch.setattr("lam.stage.single.default_recycle", lambda path: recycled.append(Path(path)))
    before = dest.read_bytes()
    actual = sha256_full(dest)
    snapshot = _files(tmp_path)
    wrong = "ab" * 32
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", wrong, "--json"],
        capsys,
    )
    assert code == 1
    assert dest.read_bytes() == before
    assert sha256_full(dest) == actual
    assert _files(tmp_path) == snapshot
    assert recycled == []
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "sha-mismatch"
    assert payload["message"] == "sha-mismatch: dest hash does not match"
    assert payload["sha256"] is None
    assert payload["old_sha256"] == actual
    assert payload["old_sha256"] != wrong
    assert payload["size"] == len(b"new-bytes")
    assert payload["backup_path"] is None


def test_bad_hex_is_refused_not_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    before = dest.read_bytes()
    code, out, err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", "zz", "--json"],
        capsys,
    )
    assert code == 1
    assert "place/v1" in out
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "refused"
    assert payload["message"] == "refused: bad hex"
    assert payload["sha256"] is None
    assert payload["size"] is None
    assert payload["old_sha256"] is None
    assert dest.read_bytes() == before
    assert "refused: bad hex" in err

    code, out, _err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", "ab" * 31, "--json"],
        capsys,
    )
    assert code == 1
    payload = _payload(out)
    assert payload["status"] == "refused"
    assert payload["message"] == "refused: bad hex"
    assert payload["sha256"] is None
    assert payload["size"] is None


def test_missing_staged_file_is_refused_not_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "missing.md"
    dest = tmp_path / "lore" / "note.md"
    code, out, err = _run(
        ["place", str(staged), str(dest), "--new", "--json"],
        capsys,
    )
    assert code == 1
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "refused"
    assert payload["message"] == "refused: missing staged file"
    assert payload["sha256"] is None
    assert payload["size"] is None
    assert payload["old_sha256"] is None
    assert not dest.exists()
    assert "refused: missing staged file" in err


def test_dest_missing_with_expect_sha_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "lore" / "note.md"
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", "ab" * 32, "--json"],
        capsys,
    )
    assert code == 1
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "refused"
    assert payload["message"].startswith("refused:")
    assert payload["sha256"] is None
    assert payload["size"] == len(b"new-bytes")
    assert not dest.exists()


def test_neither_flag_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    code, out, err = _run(["place", str(staged), str(dest), "--json"], capsys)
    assert code == 2
    assert "expect-sha256" in err or "--new" in err
    assert "place/v1" not in out
    assert not dest.exists()


def test_both_flags_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    code, out, err = _run(
        ["place", str(staged), str(dest), "--new", "--expect-sha256", "cd" * 32, "--json"],
        capsys,
    )
    assert code == 2
    assert "only one" in err
    assert "place/v1" not in out
    assert dest.read_bytes() == b"old-bytes"


def test_argparse_usage_exits_2(capsys: pytest.CaptureFixture[str]):
    code, out, _err = _run(["place"], capsys)
    assert code == 2
    assert "place/v1" not in out
    code, out, err = _run(["stage"], capsys)
    assert code == 2
    assert "usage:" in err
    assert "place/v1" not in out


def test_dry_run_replace_reports_old_sha_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old")
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
    _assert_shape(payload)
    assert payload["status"] == "dry-run"
    assert payload["message"] == "dry-run"
    assert payload["backup_path"] is None
    assert payload["sha256"] == sha256_full(staged)
    assert payload["old_sha256"] == old
    assert payload["size"] == len(b"new-bytes")


def test_dry_run_new_has_null_old_sha(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "lore" / "note.md"
    code, out, _err = _run(
        ["place", str(staged), str(dest), "--new", "--dry-run", "--json"],
        capsys,
    )
    assert code == 0
    assert not dest.exists()
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "dry-run"
    assert payload["message"] == "dry-run"
    assert payload["sha256"] == sha256_full(staged)
    assert payload["old_sha256"] is None
    assert payload["size"] == len(b"new-bytes")
    assert payload["backup_path"] is None


def test_backup_dir_and_default_name(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
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
    _assert_shape(payload)
    backup = Path(str(payload["backup_path"]))
    assert backup == backup_dir / f"{dest.name}.{old}.bak"
    assert backup.read_bytes() == b"old-bytes"
    assert dest.read_bytes() == b"new-bytes"
    assert list(dest.parent.glob("*.bak")) == []


def test_backup_collision_uses_numeric_suffix(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "note.md"
    dest.write_bytes(b"old-bytes")
    old = sha256_full(dest)
    monkeypatch.setattr("lam.stage.single.default_recycle", lambda path: Path(path).unlink())
    code, out, err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", old, "--json"],
        capsys,
    )
    assert code == 0, err
    first = Path(str(_payload(out)["backup_path"]))
    assert first == dest.parent / f"{dest.name}.{old}.bak"
    dest.write_bytes(b"old-bytes")
    code, out, err = _run(
        ["place", str(staged), str(dest), "--expect-sha256", old, "--json"],
        capsys,
    )
    assert code == 0, err
    second = Path(str(_payload(out)["backup_path"]))
    assert second == dest.parent / f"{dest.name}.{old}.2.bak"
    assert first.read_bytes() == b"old-bytes"
    assert second.read_bytes() == b"old-bytes"


def test_root_guard_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    dropbox = tmp_path / "Dropbox"
    dropbox.mkdir()
    monkeypatch.setenv("LAM_DROPBOX_ROOT", str(dropbox))
    source = tmp_path / "note.md"
    source.write_bytes(b"alpha\n")
    code, out, err = _run(
        ["stage", str(source), "--root", str(dropbox / "stage"), "--json"],
        capsys,
    )
    assert code == 1
    assert "Dropbox" in err
    assert not (dropbox / "stage").exists()
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "refused"
    assert payload["message"].startswith("refused:")
    assert "Dropbox" in payload["message"]
    assert payload["dest"] is None
    assert payload["sha256"] is None
    assert payload["size"] is None

    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "lore" / "note.md"
    code, out, err = _run(
        ["place", str(staged), str(dest), "--new", "--root", str(tmp_path / "other"), "--json"],
        capsys,
    )
    assert code == 1
    assert "outside --root" in err
    assert not dest.exists()
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "refused"
    assert payload["message"] == "refused: staged file is outside --root"
    assert payload["sha256"] is None
    assert payload["size"] == len(b"new-bytes")


def test_grant_and_example_guard_refuse(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    staged = tmp_path / "staged.md"
    staged.write_bytes(b"new-bytes")
    dest = tmp_path / "lore" / "note.md"
    code, out, err = _run(
        ["place", str(staged), str(dest), "--new", "--grant", str(tmp_path / "allowed"), "--json"],
        capsys,
    )
    assert code == 1
    assert "outside --grant" in err
    assert not dest.exists()
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "refused"
    assert payload["message"] == "refused: dest is outside --grant"
    assert payload["sha256"] is None
    assert payload["size"] == len(b"new-bytes")

    code, out, err = _run(
        ["place", str(staged), r"C:\Example\lore\note.md", "--new", "--json"],
        capsys,
    )
    assert code == 1
    assert "example path" in err
    payload = _payload(out)
    _assert_shape(payload)
    assert payload["status"] == "refused"
    assert payload["message"] == "refused: example path"
    assert payload["sha256"] is None
    assert payload["size"] == len(b"new-bytes")


def test_recycle_failure_keeps_new_dest(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
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
    _assert_shape(payload)
    assert payload["status"] == "failed"
    assert payload["message"].startswith("failed:")
    assert "recycle failed" in payload["message"]
    assert dest.read_bytes() == b"new-bytes"
    assert payload["sha256"] == sha256_full(dest)
    assert payload["size"] == len(b"new-bytes")
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
    _assert_shape(payload)
    assert payload["status"] == "failed"
    assert payload["message"].startswith("failed:")
    assert "verify failed" in payload["message"]
    assert "restored" in payload["message"]
