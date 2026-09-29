# SPDX-License-Identifier: AGPL-3.0-or-later
"""Model cascade: agreement, disagreement, Unsure, double-check, fallback."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from lam.tag.errors import TagError
from lam.tag.model import (
    LEVEL1_BINS,
    OllamaTagger,
    TagRequest,
    TagResult,
    prompt_confirm,
    prompt_primary,
)
from lam.tag.model import ModelUnavailable
from lam.tag.scan import run_tag_scan

BANNED = ("foundry", "roll20", "patreon", "photoshop", "nvidia", "midjourney", "qwen3.8")


class ScriptedTagger:
    model_name = "fake-model"

    def __init__(self, labels: list[object]):
        self.labels = list(labels)
        self.calls: list[TagRequest] = []

    def classify(self, request: TagRequest) -> TagResult:
        self.calls.append(request)
        if not self.labels:
            raise AssertionError("model called more times than scripted")
        item = self.labels.pop(0)
        if isinstance(item, BaseException):
            raise item
        return TagResult(label=str(item))


def _png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pixels")


def _scan(tmp_path: Path, tagger: ScriptedTagger, *, name: str = "shot.png") -> tuple[dict, ScriptedTagger]:
    root = tmp_path / "in"
    root.mkdir()
    _png(root / name)
    doc = run_tag_scan(root, out=tmp_path / "tags.json", tagger=tagger)
    return doc, tagger


def test_agree_is_high_and_one_call(tmp_path: Path):
    doc, tagger = _scan(tmp_path, ScriptedTagger(["_Images"]))
    item = doc["files"][0]
    assert item["bin"] == "_Images"
    assert item["confidence"] == "high"
    assert len(tagger.calls) == 1


def test_same_answer_twice_is_high_and_prompts_differ(tmp_path: Path):
    doc, tagger = _scan(tmp_path, ScriptedTagger(["_Maps", "_Maps"]))
    item = doc["files"][0]
    assert item["bin"] == "_Maps"
    assert item["confidence"] == "high"
    assert len(tagger.calls) == 2
    assert tagger.calls[0].prompt != tagger.calls[1].prompt
    assert "Unsure" in tagger.calls[0].prompt
    assert "_Maps" in tagger.calls[0].labels


def test_disagree_is_low(tmp_path: Path):
    doc, tagger = _scan(tmp_path, ScriptedTagger(["_Maps", "_Documents"]))
    item = doc["files"][0]
    assert item["confidence"] == "low"
    assert item["bin"] == "_Images"
    assert len(tagger.calls) == 2


def test_unsure_stays_unsure(tmp_path: Path):
    doc, _tagger = _scan(tmp_path, ScriptedTagger(["Unsure"]))
    item = doc["files"][0]
    assert item["confidence"] == "unsure"


def test_unreachable_falls_back_to_rules(tmp_path: Path, capsys):
    doc, tagger = _scan(tmp_path, ScriptedTagger([ModelUnavailable("down")]))
    item = doc["files"][0]
    assert doc["model"] == "unavailable"
    assert item["bin"] == "_Images"
    assert item["confidence"] == "low"
    assert tagger.calls
    err = capsys.readouterr().err
    assert "unavailable" in err.lower()
    assert err.lower().count("unavailable") == 1


def test_rules_only_skips_model_for_sure_documents(tmp_path: Path):
    root = tmp_path / "in"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF-1.4\n")
    tagger = ScriptedTagger(["_Images"])
    doc = run_tag_scan(root, out=tmp_path / "tags.json", tagger=tagger)
    assert doc["files"][0]["bin"] == "_Documents"
    assert doc["files"][0]["confidence"] == "high"
    assert tagger.calls == []


def test_large_image_is_not_sent(tmp_path: Path):
    root = tmp_path / "in"
    root.mkdir()
    (root / "big.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    tagger = ScriptedTagger(["_Maps", "_Maps"])
    doc = run_tag_scan(root, out=tmp_path / "tags.json", tagger=tagger, max_image_bytes=16)
    item = doc["files"][0]
    assert tagger.calls == []
    assert item["confidence"] == "low"
    assert any("Pillow" in note for note in item["notes"])


def test_qwen38_refused():
    with pytest.raises(TagError, match="qwen3.8"):
        OllamaTagger(model="qwen3.8:27b")
    with pytest.raises(TagError, match="qwen3.8"):
        OllamaTagger(model="vendor-Qwen3.8-extra")
    OllamaTagger(model="qwen3.5:9b", poster=lambda *_a, **_k: b"{}")


def test_request_body_shape():
    seen: dict = {}

    def poster(url: str, payload: bytes, timeout: float) -> bytes:
        seen["url"] = url
        seen["timeout"] = timeout
        seen["body"] = json.loads(payload)
        return json.dumps({"message": {"content": json.dumps({"bin": "_Documents"})}}).encode()

    tagger = OllamaTagger(model="qwen3.5:9b", poster=poster)
    result = tagger.classify(
        TagRequest(
            prompt="pick one",
            labels=(*LEVEL1_BINS, "Unsure"),
            image=b"\xff\xd8\xff\xe0jpeg",
            filename="a.jpg",
            rule_bin="_Images",
            rule_id="magic-jpeg",
        )
    )
    assert result.label == "_Documents"
    body = seen["body"]
    assert body["model"] == "qwen3.5:9b"
    assert body["think"] is False
    assert body["options"]["temperature"] == 0
    enum = body["format"]["properties"]["bin"]["enum"]
    assert list(enum) == [*LEVEL1_BINS, "Unsure"]
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert "images" in body["messages"][0]
    assert isinstance(body["messages"][0]["images"][0], str)


def test_retries_then_unavailable():
    calls = {"n": 0}

    def poster(url: str, payload: bytes, timeout: float) -> bytes:
        calls["n"] += 1
        raise urllib.error.URLError("connection refused")

    tagger = OllamaTagger(poster=poster)
    with pytest.raises(ModelUnavailable):
        tagger.classify(
            TagRequest(
                prompt="pick one",
                labels=(*LEVEL1_BINS, "Unsure"),
                image=None,
                filename="a.pdf",
                rule_bin="_Documents",
                rule_id="ext-pdf",
            )
        )
    assert calls["n"] == 3


def test_non_loopback_url_refused():
    with pytest.raises(TagError, match="localhost"):
        OllamaTagger(base_url="http://example.com:11434", poster=lambda *_a, **_k: b"{}")


def test_prompts_do_not_name_vendors():
    labels = (*LEVEL1_BINS, "Unsure")
    text = prompt_primary(
        filename="shot.png",
        extension=".png",
        size=10,
        folder="incoming",
        rule_bin="_Images",
        rule_id="magic-png",
        labels=labels,
    ) + prompt_confirm(
        filename="shot.png",
        extension=".png",
        size=10,
        folder="incoming",
        rule_bin="_Images",
        rule_id="magic-png",
        labels=labels,
    )
    lowered = text.lower()
    for word in BANNED:
        assert word not in lowered
    assert prompt_primary(
        filename="a",
        extension="",
        size=1,
        folder="f",
        rule_bin="_Unknown",
        rule_id="none",
        labels=labels,
    ) != prompt_confirm(
        filename="a",
        extension="",
        size=1,
        folder="f",
        rule_bin="_Unknown",
        rule_id="none",
        labels=labels,
    )
