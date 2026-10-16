# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack and unpack tests. All fixtures live under tmp_path.

Covered:
- round-trip zip and tar.gz
- excludes, .git skip, manifest.tsv rows
- verify failure leaves no final archive and keeps the temp
- dry run writes nothing
- refuse an existing archive
- zip-slip: ../x, absolute, drive letter
- tar symlink rejected before any extract
- conflict skip, conflict backup under _prev/<stamp>, unchanged
- manifest mismatch fails before extract
- external --manifest
- one FINAL line on pass and on fail
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.cli import NO_CONFIG_CMDS, main

ROOT = Path(__file__).resolve().parents[1]


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as caught:
        main(argv)
    code = 0 if caught.value.code is None else int(caught.value.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _finals(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith("FINAL ")]


def _failed(err: str, command: str) -> int:
    lines = _finals(err)
    assert len(lines) == 1, err
    line = lines[0]
    assert f"command={command} " in line
    match = re.search(r"failed=(\d+)", line)
    assert match, line
    return int(match.group(1))


def _log_events(err: str) -> list[dict]:
    line = _finals(err)[0]
    path = Path(line.split(" log=", 1)[1].strip())
    return [json.loads(item) for item in path.read_text(encoding="utf-8").splitlines() if item]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_src(tmp: Path) -> Path:
    src = tmp / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_bytes(b"alpha")
    (src / "sub" / "b.txt").write_bytes(b"bravo")
    return src


def _tree(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in filenames:
            path = Path(dirpath) / name
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def _names(path: Path) -> list[str]:
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as handle:
            return handle.namelist()
    with tarfile.open(path, "r:gz") as handle:
        return [member.name for member in handle.getmembers()]


def _member_bytes(path: Path, name: str) -> bytes:
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as handle:
            return handle.read(name)
    with tarfile.open(path, "r:gz") as handle:
        extracted = handle.extractfile(name)
        assert extracted is not None
        with extracted:
            return extracted.read()


def _write_slip_zip(path: Path, bad_name: str) -> None:
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr(bad_name, b"bad")
        handle.writestr("ok.txt", b"ok")


def test_round_trip_zip(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.zip"
    target = tmp_path / "unpacked"
    code, _out, err = _run(["pack", str(src), str(archive), "--apply"], capsys)
    assert code == 0
    assert _failed(err, "pack") == 0
    events = _log_events(err)
    assert "plan" in [item["event"] for item in events]
    assert "summary" in [item["event"] for item in events]
    code, _out, err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code == 0
    assert _failed(err, "unpack") == 0
    assert _tree(target) == _tree(src)
    assert "manifest.tsv" in _names(archive)
    assert "manifest.tsv" not in _tree(target)


def test_round_trip_tar_gz(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.tar.gz"
    target = tmp_path / "unpacked"
    code, _out, err = _run(["pack", str(src), str(archive), "--format", "tar.gz", "--apply"], capsys)
    assert code == 0
    assert _failed(err, "pack") == 0
    code, _out, err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code == 0
    assert _failed(err, "unpack") == 0
    assert _tree(target) == _tree(src)
    assert "manifest.tsv" in _names(archive)


def test_round_trip_without_manifest(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "plain.zip"
    target = tmp_path / "out"
    code, _out, _err = _run(["pack", str(src), str(archive), "--no-manifest", "--apply"], capsys)
    assert code == 0
    assert "manifest.tsv" not in _names(archive)
    code, _out, err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code == 0
    assert _failed(err, "unpack") == 0
    assert _tree(target) == _tree(src)


def test_excludes_and_manifest_content(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    (src / "note.log").write_bytes(b"skip-me")
    (src / "sub" / "note.log").write_bytes(b"skip-nested")
    (src / "skip").mkdir()
    (src / "skip" / "a.txt").write_bytes(b"hidden")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("nope", encoding="utf-8")
    archive = tmp_path / "packed.zip"
    code, out, err = _run(
        ["pack", str(src), str(archive), "--exclude", "*.log", "--exclude", "skip/*", "--apply"],
        capsys,
    )
    assert code == 0
    assert _failed(err, "pack") == 0
    names = _names(archive)
    assert "a.txt" in names
    assert "sub/b.txt" in names
    assert "note.log" not in names
    assert "sub/note.log" not in names
    assert "skip/a.txt" not in names
    assert ".git/config" not in names
    assert "note.log" in out
    assert "exclude:" in out
    blob = _member_bytes(archive, "manifest.tsv")
    lines = blob.decode("utf-8").splitlines()
    assert lines == sorted(lines)
    rows = [line.split("\t") for line in lines]
    assert [row[0] for row in rows] == ["a.txt", "sub/b.txt"]
    by_name = {row[0]: row for row in rows}
    assert by_name["a.txt"][1] == "5"
    assert by_name["a.txt"][2] == _sha(b"alpha")
    assert by_name["sub/b.txt"][1] == "5"
    assert by_name["sub/b.txt"][2] == _sha(b"bravo")


def test_dry_run_writes_nothing(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    before = _tree(src)
    archive = tmp_path / "out.zip"
    code, out, err = _run(["pack", str(src), str(archive), "--json"], capsys)
    assert code == 0
    assert _failed(err, "pack") == 0
    assert not archive.exists()
    assert _tree(src) == before
    assert list(tmp_path.rglob("*.partial")) == []
    payload = json.loads(out)
    assert payload["status"] == "PASS"
    assert payload["target"].endswith("out.zip")
    assert any(item["rel"] == "a.txt" for item in payload["files"])
    fixture = tmp_path / "fixture.zip"
    with zipfile.ZipFile(fixture, "w") as handle:
        handle.writestr("a.txt", b"alpha")
    snap = _tree(tmp_path)
    target = tmp_path / "missing-target"
    code, out, err = _run(["unpack", str(fixture), str(target)], capsys)
    assert code == 0
    assert _failed(err, "unpack") == 0
    assert not target.exists()
    assert _tree(tmp_path) == snap
    assert "new" in out


def test_refuse_existing_archive(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.zip"
    archive.write_bytes(b"KEEP")
    code, _out, err = _run(["pack", str(src), str(archive), "--apply"], capsys)
    assert code != 0
    assert _failed(err, "pack") > 0
    assert archive.read_bytes() == b"KEEP"
    assert _tree(src) == {"a.txt": b"alpha", "sub/b.txt": b"bravo"}


def test_verify_failure_leaves_no_archive(tmp_path: Path, capsys, monkeypatch):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.zip"

    def bad(handle):
        data = handle.read()
        return len(data), "0" * 64

    monkeypatch.setattr("lam.archive._hash_archived_member", bad)
    code, _out, err = _run(["pack", str(src), str(archive), "--apply"], capsys)
    assert code != 0
    assert _failed(err, "pack") > 0
    assert not archive.exists()
    partials = [path for path in archive.parent.iterdir() if ".partial" in path.name]
    assert partials
    assert partials[0].stat().st_size > 0
    assert _tree(src) == {"a.txt": b"alpha", "sub/b.txt": b"bravo"}
    assert "mismatch" in err


@pytest.mark.parametrize("bad_name", ["../x", "/abs/x", "C:/evil.txt", "foo/../../x"])
def test_zip_slip_rejected(tmp_path: Path, capsys, bad_name: str):
    case = tmp_path / "case"
    case.mkdir()
    archive = case / "bad.zip"
    target = case / "out"
    _write_slip_zip(archive, bad_name)
    before = sorted(path.name for path in case.iterdir())
    code, out, err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code != 0
    assert _failed(err, "unpack") > 0
    assert not target.exists()
    assert sorted(path.name for path in case.iterdir()) == before
    assert "unsafe" in out


def test_tar_symlink_rejected(tmp_path: Path, capsys):
    archive = tmp_path / "link.tar.gz"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as handle:
        link = tarfile.TarInfo(name="evil")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside"
        handle.addfile(link)
        regular = tarfile.TarInfo(name="ok.txt")
        payload = b"ok"
        regular.size = len(payload)
        regular.mode = 0o644
        regular.type = tarfile.REGTYPE
        handle.addfile(regular, io.BytesIO(payload))
    archive.write_bytes(buffer.getvalue())
    target = tmp_path / "out"
    code, out, err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code != 0
    assert _failed(err, "unpack") > 0
    assert not target.exists()
    assert "unsafe" in out
    assert "symlink" in out


def test_conflict_skip_and_extra(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.zip"
    code, _out, _err = _run(["pack", str(src), str(archive), "--apply"], capsys)
    assert code == 0
    target = tmp_path / "dest"
    (target / "sub").mkdir(parents=True)
    (target / "a.txt").write_bytes(b"DIFFERENT")
    (target / "extra.txt").write_bytes(b"leave-me")
    old = (target / "a.txt").stat().st_mtime_ns
    os.utime(target / "a.txt", ns=(old, old))
    code, out, err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code == 0
    assert _failed(err, "unpack") == 0
    assert (target / "a.txt").read_bytes() == b"DIFFERENT"
    assert (target / "a.txt").stat().st_mtime_ns == old
    assert (target / "sub" / "b.txt").read_bytes() == b"bravo"
    assert (target / "extra.txt").read_bytes() == b"leave-me"
    assert "conflict-skip" in out
    assert "extra: extra.txt" in out


def test_conflict_backup(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.zip"
    code, _out, _err = _run(["pack", str(src), str(archive), "--apply"], capsys)
    assert code == 0
    target = tmp_path / "dest"
    nested = target / "sub"
    nested.mkdir(parents=True)
    (nested / "b.txt").write_bytes(b"OLD")
    code, out, err = _run(
        ["unpack", str(archive), str(target), "--on-conflict", "backup", "--apply"],
        capsys,
    )
    assert code == 0
    assert _failed(err, "unpack") == 0
    assert (target / "a.txt").read_bytes() == b"alpha"
    assert (nested / "b.txt").read_bytes() == b"bravo"
    assert "conflict-backup" in out
    found = list((target / "_prev").rglob("b.txt"))
    assert len(found) == 1
    assert found[0].read_bytes() == b"OLD"
    rel = found[0].relative_to(target / "_prev")
    assert re.fullmatch(r"\d{8}_\d{6}(_\d+)?", rel.parts[0])
    assert Path(*rel.parts[1:]).as_posix() == "sub/b.txt"


def test_unchanged_detection(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.zip"
    target = tmp_path / "dest"
    code, _out, _err = _run(["pack", str(src), str(archive), "--apply"], capsys)
    assert code == 0
    code, _out, _err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code == 0
    stamp = 1_700_000_000_000_000_000
    for path in (target / "a.txt", target / "sub" / "b.txt"):
        os.utime(path, ns=(stamp, stamp))
    code, out, err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code == 0
    assert _failed(err, "unpack") == 0
    assert "unchanged" in out
    assert (target / "a.txt").read_bytes() == b"alpha"
    assert (target / "a.txt").stat().st_mtime_ns == stamp
    assert (target / "sub" / "b.txt").stat().st_mtime_ns == stamp


def test_manifest_mismatch_fails_before_extract(tmp_path: Path, capsys):
    archive = tmp_path / "bad.zip"
    payload = b"alpha"
    digest = _sha(payload)
    wrong = "1" * 64 if digest[0] == "0" else "0" * 64
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("a.txt", payload)
        handle.writestr("manifest.tsv", f"a.txt\t{len(payload)}\t{wrong}\n".encode("utf-8"))
    target = tmp_path / "out"
    code, out, err = _run(["unpack", str(archive), str(target), "--apply"], capsys)
    assert code != 0
    assert _failed(err, "unpack") > 0
    assert not target.exists()
    assert "mismatch" in out or "mismatch" in err


def test_external_manifest(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.zip"
    code, _out, _err = _run(["pack", str(src), str(archive), "--apply"], capsys)
    assert code == 0
    manifest = tmp_path / "external.tsv"
    manifest.write_bytes(_member_bytes(archive, "manifest.tsv"))
    good = tmp_path / "good"
    code, _out, err = _run(
        ["unpack", str(archive), str(good), "--manifest", str(manifest), "--apply"],
        capsys,
    )
    assert code == 0
    assert _failed(err, "unpack") == 0
    assert _tree(good) == _tree(src)
    bad_text = manifest.read_text(encoding="utf-8").replace(_sha(b"alpha"), "ab" * 32)
    manifest.write_text(bad_text, encoding="utf-8")
    bad = tmp_path / "bad"
    code, _out, err = _run(
        ["unpack", str(archive), str(bad), "--manifest", str(manifest), "--apply"],
        capsys,
    )
    assert code != 0
    assert _failed(err, "unpack") > 0
    assert not bad.exists()


def test_final_line_on_pass_and_fail(tmp_path: Path, capsys):
    src = _make_src(tmp_path)
    archive = tmp_path / "out.zip"
    code, _out, err = _run(["pack", str(src), str(archive), "--apply"], capsys)
    assert code == 0
    assert len(_finals(err)) == 1
    assert _failed(err, "pack") == 0
    slipped = tmp_path / "slip.zip"
    _write_slip_zip(slipped, "../x")
    code, _out, err = _run(["unpack", str(slipped), str(tmp_path / "nope"), "--apply"], capsys)
    assert code != 0
    assert len(_finals(err)) == 1
    assert _failed(err, "unpack") > 0


def test_capabilities_and_docs():
    cap = describe_capabilities()
    names = [item["name"] for item in cap["commands"]]
    assert "pack" in names
    assert "unpack" in names
    assert "pack" in NO_CONFIG_CMDS
    assert "unpack" in NO_CONFIG_CMDS
    assert cap["archive"]["formats"] == ["zip", "tar.gz"]
    assert "7z" in cap["archive"]["not_yet"]
    text = format_capabilities_text(cap)
    assert "lam pack" in text
    assert "lam unpack" in text
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    doc = (ROOT / "docs" / "archive.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    arch = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "lam pack" in readme and "lam unpack" in readme
    assert "manifest.tsv" in doc
    assert "docs/archive.md" in agents
    assert "lam.archive" in arch
