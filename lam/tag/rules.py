# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extension, name, magic-byte, and size rules. Rules run before the model."""

from __future__ import annotations

from dataclasses import dataclass

from lam.tag.bins import LEVEL1_BINS

DOC_EXT = {
    ".pdf": "ext-pdf",
    ".docx": "ext-docx",
    ".epub": "ext-epub",
    ".txt": "ext-txt",
    ".md": "ext-md",
    ".csv": "ext-csv",
    ".rtf": "ext-rtf",
    ".odt": "ext-odt",
    ".xlsx": "ext-xlsx",
    ".pptx": "ext-pptx",
}
ARCHIVE_EXT = {
    ".zip": "ext-zip",
    ".7z": "ext-7z",
    ".rar": "ext-rar",
    ".tar": "ext-tar",
    ".gz": "ext-gz",
    ".tgz": "ext-tgz",
    ".bz2": "ext-bz2",
}
IMAGE_EXT = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
    ".bmp": "bmp",
    ".tif": "tiff",
    ".tiff": "tiff",
}
PLUGIN_EXT = {".esp": "ext-esp", ".esm": "ext-esm", ".esl": "ext-esl"}
MAP_TOKENS = ("battlemap", "grid-map", "dungeon-map")
SENDABLE_IMAGE_KINDS = frozenset({"jpeg", "png", "webp"})


@dataclass(frozen=True)
class SubBin:
    name: str
    description: str = ""
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleHit:
    bin: str
    confidence: str
    rule_id: str


def image_magic(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return "gif"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    if header.startswith(b"BM"):
        return "bmp"
    if header.startswith(b"II*\x00") or header.startswith(b"MM\x00*"):
        return "tiff"
    return None


def archive_magic(header: bytes) -> str | None:
    if header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06") or header.startswith(b"PK\x07\x08"):
        return "zip"
    if header.startswith(bytes.fromhex("377ABCAF271C")):
        return "7z"
    if header.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if header.startswith(b"\x1f\x8b"):
        return "gzip"
    return None


def image_kind(header: bytes, extension: str) -> str | None:
    magic = image_magic(header)
    if magic:
        return magic
    return IMAGE_EXT.get(extension)


def sendable_image(header: bytes, extension: str, size: int, max_bytes: int) -> tuple[bool, str | None]:
    """JPEG/PNG/WebP at or under *max_bytes* can be sent. Other images need Pillow."""
    kind = image_kind(header, extension)
    if kind is None:
        return False, None
    if kind not in SENDABLE_IMAGE_KINDS or image_magic(header) not in SENDABLE_IMAGE_KINDS:
        return False, "no thumbnail step without Pillow; image not sent"
    if size > max_bytes:
        return False, "image larger than max; no thumbnail step without Pillow"
    return True, None


def classify_rules(
    name: str,
    extension: str,
    size: int,
    header: bytes,
    *,
    weight_min_bytes: int,
    subbins: tuple[SubBin, ...] | list[SubBin] | None = None,
) -> RuleHit:
    """Return the first matching rule.

    Level 2 (*subbins* set) uses only the supplied hints. A single hint match
    is high confidence because the list was supplied for this pass. Image
    matches at level 1 are low: they stay provisional until a model agrees.
    """
    if subbins is not None:
        return _match_hints(name, subbins)
    ext = extension.lower()
    if ext in {".safetensors", ".gguf", ".ckpt"}:
        return RuleHit("_ModelWeights", "high", f"ext-{ext[1:]}")
    if ext in {".pt", ".bin"}:
        if size >= weight_min_bytes:
            return RuleHit("_ModelWeights", "high", "size-large-weight")
        return RuleHit("_Unknown", "unsure", "ext-small-weight")
    if ext in {".exe", ".msi"} and header.startswith(b"MZ"):
        return RuleHit("_Installers", "high", "magic-mz")
    if ext == ".msi" and header.startswith(bytes.fromhex("D0CF11E0")):
        return RuleHit("_Installers", "high", "magic-ole-msi")
    if ext == ".msi":
        return RuleHit("_Installers", "high", "ext-msi")
    if ext == ".exe":
        return RuleHit("_Installers", "low", "ext-exe-no-mz")
    if ext in DOC_EXT:
        return RuleHit("_Documents", "high", DOC_EXT[ext])
    if ext in PLUGIN_EXT:
        return RuleHit("_GameMods", "high", PLUGIN_EXT[ext])
    if ext == ".pak":
        return RuleHit("_GameMods", "low", "ext-pak")
    lowered = name.lower()
    if any(token in lowered for token in MAP_TOKENS) and (
        image_magic(header) is not None or ext in IMAGE_EXT
    ):
        return RuleHit("_Maps", "low", "name-map")
    magic = archive_magic(header)
    if magic and ext not in DOC_EXT:
        return RuleHit("_Archives", "high", f"magic-{magic}")
    if ext in ARCHIVE_EXT:
        return RuleHit("_Archives", "high", ARCHIVE_EXT[ext])
    kind = image_magic(header)
    if kind:
        return RuleHit("_Images", "low", f"magic-{kind}")
    if ext in IMAGE_EXT:
        return RuleHit("_Images", "low", f"ext-{ext[1:]}")
    return RuleHit("_Unknown", "unsure", "none")


def _match_hints(name: str, subbins: tuple[SubBin, ...] | list[SubBin]) -> RuleHit:
    lowered = name.lower()
    found: list[str] = []
    for sub in subbins:
        if any(hint and hint.lower() in lowered for hint in sub.hints):
            found.append(sub.name)
    if len(found) == 1:
        return RuleHit(found[0], "high", f"hint-{found[0]}")
    if len(found) > 1:
        return RuleHit("_Unknown", "unsure", "hint-ambiguous")
    return RuleHit("_Unknown", "unsure", "no-hint")


def is_level1_bin(name: str) -> bool:
    return name in LEVEL1_BINS
