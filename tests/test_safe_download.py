# SPDX-License-Identifier: AGPL-3.0-or-later
"""Atomic downloads, temp names, and creator split-sets. No network."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import httpx
import pytest

from lam.patreon.errors import PatreonAuthError, PatreonChallengeError, PatreonRateLimitError
from lam.patreon.http_source import HttpPatreonSource, RequestClock
from lam.patreon.parse import MediaItem
from lam.patreon.safe_download import (
    TEMP_SUFFIX,
    DownloadSizeError,
    group_split_sets,
    is_our_temp,
    our_temp_target,
    reassemble,
    run_bounded,
    split_part_info,
    temp_path_for,
    write_atomic,
)
from lam.token.secret import Secret

# name, ours, target, scheme, base, index, concat
_SPLIT_CASES = [
    ("a.zip.001", False, None, "numeric", "a.zip", 1, True),
    ("a.part1.rar", False, None, "rar_part", "a.rar", 1, False),
    ("name.part01.rar", False, None, "rar_part", "name.rar", 1, False),
    ("a.z01", False, None, "zip_z", "a.zip", 1, False),
    ("Office Part 2.zip", False, None, "named_part", "Office.zip", 2, False),
    ("Map (Part 2).zip", False, None, "named_part", "Map.zip", 2, False),
    ("dungeon_pt1.zip", False, None, "named_part", "dungeon.zip", 1, False),
    ("Map Part 1.zip", False, None, "named_part", "Map.zip", 1, False),
    ("a.zip.part1", True, "a.zip", None, None, None, None),
    ("a.zip.partial", True, "a.zip", None, None, None, None),
    ("a.zip.bad2", True, "a.zip", None, None, None, None),
    ("a.zip.bad", True, "a.zip", None, None, None, None),
    ("$3 alley rewards.zip.part1", True, "$3 alley rewards.zip", None, None, None, None),
    ("Map Pack #1.zip", False, None, None, None, None, None),
]


def _media(name: str = "a.zip", *, size: int | None = None, url: str | None = None) -> MediaItem:
    return MediaItem(
        media_id="m1",
        name=name,
        url=url or f"https://www.patreon.com/file/{name}",
        kind="zip",
        source_kind="attachment",
        size=size,
    )


def _source(handler, clock: RequestClock | None = None, *, min_delay: float = 0.0) -> HttpPatreonSource:
    return HttpPatreonSource(
        Secret("session_id=fixture-session"),
        transport=httpx.MockTransport(handler),
        clock=clock or RequestClock(),
        min_delay=min_delay,
        retry_cap=5.0,
    )


def _block_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_seconds: float) -> None:
        raise AssertionError("real sleep")

    monkeypatch.setattr(time, "sleep", boom)


def test_temp_path_uses_partial_suffix(tmp_path: Path):
    dest = tmp_path / "nested" / "a.zip"
    assert TEMP_SUFFIX == ".partial"
    assert temp_path_for(dest) == dest.with_name("a.zip.partial")


def test_write_atomic_renames_only_after_the_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dest = tmp_path / "nested" / "a.zip"
    temp = temp_path_for(dest)
    seen: dict[str, bool] = {}
    modes: list[str] = []
    real_open = Path.open

    def spy(self: Path, mode: str = "r", *args, **kwargs):
        modes.append(mode)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)

    def chunks():
        seen["dest_during"] = dest.exists()
        seen["temp_during"] = temp.exists()
        yield b"abc"
        seen["dest_mid"] = dest.exists()
        yield b"def"

    result = write_atomic(dest, chunks())
    assert seen == {"dest_during": False, "temp_during": True, "dest_mid": False}
    assert dest.read_bytes() == b"abcdef"
    assert not temp.exists()
    assert result.path == dest
    assert result.size == 6
    assert result.sha256 == hashlib.sha256(b"abcdef").hexdigest()
    assert result.skipped_existing is False
    assert result.resumed is False
    assert "wb" in modes
    assert "xb" not in modes


def test_size_mismatch_removes_temp_and_final(tmp_path: Path):
    dest = tmp_path / "a.zip"
    with pytest.raises(DownloadSizeError):
        write_atomic(dest, [b"hello"], expected_size=3)
    assert not dest.exists()
    assert not temp_path_for(dest).exists()


def test_zero_byte_download_is_an_error(tmp_path: Path):
    dest = tmp_path / "a.zip"
    with pytest.raises(DownloadSizeError):
        write_atomic(dest, [b"", b""])
    assert not dest.exists()
    assert not temp_path_for(dest).exists()


def test_leftover_partial_and_legacy_part_are_overwritten(tmp_path: Path):
    dest = tmp_path / "a.zip"
    temp = temp_path_for(dest)
    legacy = tmp_path / "a.zip.part1"
    temp.write_bytes(b"stale-partial")
    legacy.write_bytes(b"stale-legacy")
    result = write_atomic(dest, [b"new-bytes"])
    assert dest.read_bytes() == b"new-bytes"
    assert result.resumed is True
    assert not temp.exists()
    assert legacy.read_bytes() == b"stale-legacy"

    other = tmp_path / "b.zip"
    (tmp_path / "b.zip.part1").write_bytes(b"old-part")
    again = write_atomic(other, [b"fresh"])
    assert other.read_bytes() == b"fresh"
    assert again.resumed is True


def test_download_atomic_rerun_skips_without_file_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_real_sleep(monkeypatch)
    calls = {"n": 0}
    body = b"payload-bytes"

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=body, headers={"content-type": "application/zip"})

    source = _source(handler)
    dest = tmp_path / "post" / "a.zip"
    dest.parent.mkdir()
    (dest.parent / "a.zip.partial").write_bytes(b"stale")
    (dest.parent / "a.zip.part2").write_bytes(b"stale-legacy")
    (dest.parent / "a.zip.bad").write_bytes(b"stale-bad")
    first = source.download_atomic(_media(), dest)
    assert isinstance(first.sha256, str) and first.sha256 == hashlib.sha256(body).hexdigest()
    assert first.skipped_existing is False
    assert first.resumed is True
    assert dest.read_bytes() == body
    assert not temp_path_for(dest).exists()
    second = source.download_atomic(_media(), dest, expected_size=len(body))
    assert second.skipped_existing is True
    assert second.size == len(body)
    assert calls["n"] == 1
    digest = source.download(_media(), dest)
    assert digest == first.sha256
    assert calls["n"] == 1


def test_retry_backoff_on_429_then_200(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_real_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "30"}, text="slow")
        return httpx.Response(200, content=b"ok-body", headers={"content-type": "application/octet-stream"})

    clock = RequestClock()
    source = _source(handler, clock, min_delay=0.4)
    result = source.download_atomic(_media(), tmp_path / "a.zip")
    assert result.size == len(b"ok-body")
    assert (tmp_path / "a.zip").read_bytes() == b"ok-body"
    assert calls["n"] == 2
    assert clock.sleeps == [30.0]


def test_5xx_and_timeout_retry_then_auth_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_real_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="down")
        if calls["n"] == 2:
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200, content=b"recovered", headers={"content-type": "application/zip"})

    clock = RequestClock()
    source = _source(handler, clock)
    result = source.download_atomic(_media(url="https://c10.patreonusercontent.com/a.zip"), tmp_path / "a.zip")
    assert result.sha256 == hashlib.sha256(b"recovered").hexdigest()
    assert calls["n"] == 3
    assert clock.sleeps

    auth_calls = {"n": 0}

    def auth(_request: httpx.Request) -> httpx.Response:
        auth_calls["n"] += 1
        return httpx.Response(401, text="nope")

    with pytest.raises(PatreonAuthError):
        _source(auth).download_atomic(_media(), tmp_path / "nope.zip")
    assert auth_calls["n"] == 1

    challenge_calls = {"n": 0}

    def challenge(_request: httpx.Request) -> httpx.Response:
        challenge_calls["n"] += 1
        return httpx.Response(403, text="<html>Just a moment cf-challenge</html>")

    with pytest.raises(PatreonChallengeError):
        _source(challenge).download_atomic(_media(), tmp_path / "challenge.zip")
    assert challenge_calls["n"] == 1


def test_429_gives_up_after_four_tries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_real_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"}, text="slow")

    with pytest.raises(PatreonRateLimitError):
        _source(handler).download_atomic(_media(), tmp_path / "a.zip")
    assert calls["n"] == 4
    assert not (tmp_path / "a.zip").exists()


def test_download_keeps_min_delay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_real_sleep(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"zz", headers={"content-type": "application/zip"})

    clock = RequestClock()
    source = _source(handler, clock, min_delay=0.4)
    source.download_atomic(_media(name="a.zip"), tmp_path / "a.zip")
    source.download_atomic(
        _media(name="b.zip", url="https://www.patreon.com/file/b.zip"),
        tmp_path / "b.zip",
    )
    assert clock.sleeps
    assert clock.sleeps[0] >= 0.4


def test_run_bounded_cap_default_and_spacing(monkeypatch: pytest.MonkeyPatch):
    import lam.patreon.safe_download as sd

    sleeps: list[float] = []
    monkeypatch.setattr(sd.time, "sleep", lambda seconds: sleeps.append(seconds))

    current = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def hold(item: int) -> int:
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
            if current >= 2:
                release.set()
        assert release.wait(timeout=3)
        with lock:
            current -= 1
        return item

    out = run_bounded(hold, [1, 2, 3, 4], max_workers=2, min_interval=0)
    assert out == [1, 2, 3, 4]
    assert peak == 2
    assert sleeps == []

    current = 0
    peak = 0

    def slow(item: int) -> int:
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.05)
        with lock:
            current -= 1
        return item

    serial = run_bounded(slow, [1, 2, 3])
    assert serial == [1, 2, 3]
    assert peak == 1

    sleeps.clear()
    spaced = run_bounded(lambda item: item, ["a", "b", "c"], max_workers=2, min_interval=1.25)
    assert spaced == ["a", "b", "c"]
    assert sleeps == [1.25, 1.25]

    def boom(item: int) -> int:
        if item == 1:
            raise RuntimeError("boom")
        return item

    mixed = run_bounded(boom, [0, 1, 2], max_workers=2, min_interval=0)
    assert mixed[0] == 0
    assert isinstance(mixed[1], RuntimeError)
    assert mixed[2] == 2

    with pytest.raises(ValueError):
        run_bounded(lambda item: item, [1], max_workers=3)
    with pytest.raises(ValueError):
        run_bounded(lambda item: item, [1], max_workers=0)


@pytest.mark.parametrize(
    ("name", "ours", "target", "scheme", "base", "index", "concat"),
    _SPLIT_CASES,
)
def test_split_detection_versus_our_temps(name, ours, target, scheme, base, index, concat):
    assert is_our_temp(name) is ours
    assert our_temp_target(name) == target
    info = split_part_info(name)
    if scheme is None:
        assert info is None
        return
    assert info is not None
    assert info.scheme == scheme
    assert info.base == base
    assert info.index == index
    assert info.concat is concat


def test_group_split_sets_ignores_temps_and_singletons():
    grouped = group_split_sets(
        [
            "a.zip.002",
            "a.zip.001",
            "a.zip.part1",
            "a.zip.partial",
            "lonely.zip.001",
            "note.txt",
            "Map Pack #1.zip",
            "Office Part 2.zip",
            "pack.part2.rar",
            "pack.part1.rar",
            "pack.part1.rar.partial",
            "map.z02",
            "map.zip",
            "map.z01",
            "map.zip.part3",
        ]
    )
    assert grouped["a.zip"] == ["a.zip.001", "a.zip.002"]
    assert "lonely.zip.001" not in {name for names in grouped.values() for name in names}
    assert grouped["pack.rar"] == ["pack.part1.rar", "pack.part2.rar"]
    assert grouped["map.zip"] == ["map.z01", "map.z02", "map.zip"]
    assert "Office Part 2.zip" not in {name for names in grouped.values() for name in names}
    assert all(not is_our_temp(name) for names in grouped.values() for name in names)


def test_reassemble_concatenates_numeric_parts_and_refuses_rar(tmp_path: Path):
    first = tmp_path / "a.zip.001"
    second = tmp_path / "a.zip.002"
    first.write_bytes(b"AAA")
    second.write_bytes(b"BB")
    dest = tmp_path / "joined.zip"
    result = reassemble([first, second], dest)
    assert dest.read_bytes() == b"AAABB"
    assert first.read_bytes() == b"AAA"
    assert second.read_bytes() == b"BB"
    assert result.sha256 == hashlib.sha256(b"AAABB").hexdigest()
    assert result.size == 5
    assert not temp_path_for(dest).exists()

    rar1 = tmp_path / "a.part1.rar"
    rar2 = tmp_path / "a.part2.rar"
    rar1.write_bytes(b"R1")
    rar2.write_bytes(b"R2")
    with pytest.raises(ValueError):
        reassemble([rar1, rar2], tmp_path / "a.rar")
    assert rar1.read_bytes() == b"R1"
    assert rar2.read_bytes() == b"R2"
    assert not (tmp_path / "a.rar").exists()
