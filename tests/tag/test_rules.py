# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each level-1 rule and level-2 hint rule."""

from __future__ import annotations

import struct

from lam.tag.rules import SubBin, classify_rules

WEIGHT = 50 * 1024 * 1024


def _hit(name: str, header: bytes, size: int | None = None, **kwargs):
    ext = kwargs.pop("ext", None)
    if ext is None:
        dot = name.rfind(".")
        ext = name[dot:].lower() if dot >= 0 else ""
    return classify_rules(
        name,
        ext,
        len(header) if size is None else size,
        header,
        weight_min_bytes=kwargs.pop("weight_min_bytes", WEIGHT),
        **kwargs,
    )


def test_document_extensions():
    pdf = _hit("a.pdf", b"%PDF-1.4\n")
    assert (pdf.bin, pdf.confidence, pdf.rule_id) == ("_Documents", "high", "ext-pdf")
    docx = _hit("a.docx", b"PK\x03\x04docx")
    assert (docx.bin, docx.confidence, docx.rule_id) == ("_Documents", "high", "ext-docx")
    epub = _hit("a.epub", b"PK\x03\x04epub")
    assert (epub.bin, epub.confidence, epub.rule_id) == ("_Documents", "high", "ext-epub")
    text = _hit("notes.txt", b"hello")
    assert (text.bin, text.confidence, text.rule_id) == ("_Documents", "high", "ext-txt")


def test_archive_magic_not_office():
    zip_hit = _hit("pack.zip", b"PK\x03\x04zip")
    assert (zip_hit.bin, zip_hit.confidence, zip_hit.rule_id) == ("_Archives", "high", "magic-zip")
    seven = _hit("pack.7z", bytes.fromhex("377ABCAF271C") + b"xx")
    assert (seven.bin, seven.confidence, seven.rule_id) == ("_Archives", "high", "magic-7z")
    rar = _hit("pack.rar", b"Rar!\x1a\x07\x00rest")
    assert (rar.bin, rar.confidence, rar.rule_id) == ("_Archives", "high", "magic-rar")
    # Office containers are zip bytes but stay documents.
    assert _hit("a.docx", b"PK\x03\x04").bin == "_Documents"


def test_installers_mz():
    exe = _hit("setup.exe", b"MZ\x90\x00")
    assert (exe.bin, exe.confidence, exe.rule_id) == ("_Installers", "high", "magic-mz")
    msi = _hit("setup.msi", b"MZ\x90\x00")
    assert (msi.bin, msi.confidence, msi.rule_id) == ("_Installers", "high", "magic-mz")


def test_model_weight_rules():
    header = b"{}"
    safe = struct.pack("<Q", len(header)) + header
    hit = _hit("model.safetensors", safe)
    assert (hit.bin, hit.confidence, hit.rule_id) == ("_ModelWeights", "high", "ext-safetensors")
    gguf = _hit("model.gguf", b"GGUF" + b"\x00" * 8)
    assert (gguf.bin, gguf.confidence, gguf.rule_id) == ("_ModelWeights", "high", "ext-gguf")
    ckpt = _hit("model.ckpt", b"not-a-real-checkpoint")
    assert (ckpt.bin, ckpt.confidence, ckpt.rule_id) == ("_ModelWeights", "high", "ext-ckpt")
    large = _hit("weights.pt", b"\x80", size=WEIGHT)
    assert (large.bin, large.confidence, large.rule_id) == ("_ModelWeights", "high", "size-large-weight")
    small = _hit("weights.bin", b"\x00\x01", size=32)
    assert (small.bin, small.confidence, small.rule_id) == ("_Unknown", "unsure", "ext-small-weight")


def test_image_magic_is_provisional_and_map_name():
    png = _hit("shot.png", b"\x89PNG\r\n\x1a\nrest")
    assert (png.bin, png.confidence, png.rule_id) == ("_Images", "low", "magic-png")
    jpg = _hit("shot.jpg", b"\xff\xd8\xff\xe0rest")
    assert (jpg.bin, jpg.confidence, jpg.rule_id) == ("_Images", "low", "magic-jpeg")
    webp = _hit("shot.webp", b"RIFF\x00\x00\x00\x00WEBPrest")
    assert (webp.bin, webp.confidence, webp.rule_id) == ("_Images", "low", "magic-webp")
    named = _hit("area-battlemap.png", b"\x89PNG\r\n\x1a\nrest")
    assert (named.bin, named.confidence, named.rule_id) == ("_Maps", "low", "name-map")


def test_plugin_extension_and_unknown():
    plugin = _hit("module.esp", b"TES4")
    assert (plugin.bin, plugin.confidence, plugin.rule_id) == ("_GameMods", "high", "ext-esp")
    unknown = _hit("mystery.dat", b"????")
    assert (unknown.bin, unknown.confidence, unknown.rule_id) == ("_Unknown", "unsure", "none")


def test_level2_hints_only():
    bins = (
        SubBin("SubA", description="A", hints=("alpha",)),
        SubBin("SubB", description="B", hints=("beta",)),
    )
    hit = _hit("alpha-page.pdf", b"%PDF-1.4\n", subbins=bins)
    assert (hit.bin, hit.confidence, hit.rule_id) == ("SubA", "high", "hint-SubA")
    ambiguous = _hit("alpha-beta.txt", b"x", subbins=bins)
    assert ambiguous.confidence == "unsure"
    assert ambiguous.rule_id == "hint-ambiguous"
    none = _hit("note.txt", b"x", subbins=bins)
    assert (none.bin, none.confidence, none.rule_id) == ("_Unknown", "unsure", "no-hint")


def test_rule_ids_name_no_vendor():
    banned = ("foundry", "roll20", "patreon", "photoshop", "nvidia", "midjourney")
    samples = [
        _hit("a.pdf", b"%PDF"),
        _hit("a.docx", b"PK\x03\x04"),
        _hit("a.zip", b"PK\x03\x04"),
        _hit("a.exe", b"MZ"),
        _hit("a.safetensors", struct.pack("<Q", 2) + b"{}"),
        _hit("a.esp", b"TES4"),
        _hit("battlemap.png", b"\x89PNG\r\n\x1a\n"),
        _hit("x.dat", b"????"),
    ]
    for hit in samples:
        blob = f"{hit.bin} {hit.rule_id}".lower()
        for word in banned:
            assert word not in blob
