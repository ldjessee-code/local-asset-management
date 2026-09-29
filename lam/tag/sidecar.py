# SPDX-License-Identifier: AGPL-3.0-or-later
"""XMP/IPTC-style sidecar JSON, plus an optional ExifTool ``.xmp`` export.

ExifTool is invoked with an argument list. ``-o`` points at a file under the
export folder, so the source file is not edited. A missing ExifTool warns
and skips.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from lam.schemas.registry import TAG_SIDECAR_SCHEMA_ID
from lam.tag.bins import RATING

WhichFn = Callable[[str], str | None]
RunnerFn = Callable[[list[str]], int]


def meta_path_for(tags_out: Path) -> Path:
    return tags_out.with_name(tags_out.stem + ".meta.json")


def xmp_dir_for(tags_out: Path) -> Path:
    return tags_out.with_name(tags_out.stem + ".xmp")


def model_field(model_state: str, model_label: str | None) -> str:
    if model_state in {"disabled", "unavailable"}:
        return model_state
    if model_label:
        return f"{model_state}={model_label}"
    return f"{model_state}:not-called"


def build_sidecar(
    *,
    tags_path: Path,
    created: str,
    files: list[dict[str, Any]],
    model_state: str,
) -> dict[str, Any]:
    records = []
    for item in files:
        subjects = [item["bin"]]
        if item.get("subbin"):
            subjects.append(item["subbin"])
        records.append(
            {
                "file": item["abs"],
                "dc:subject": subjects,
                "xmp:Label": item["bin"],
                "xmp:Rating": RATING.get(item["confidence"], 0),
                "lam:confidence": item["confidence"],
                "lam:rule": item["rule"],
                "lam:model": model_field(model_state, item.get("model_label")),
                "lam:batch": item["batch"],
                "lam:copy_group": item.get("copy_group"),
                "xmp:MetadataDate": created,
            }
        )
    return {
        "schema": TAG_SIDECAR_SCHEMA_ID,
        "created": created,
        "tags": str(Path(tags_path).resolve()),
        "files": records,
    }


def default_exiftool_runner(args: list[str]) -> int:
    completed = subprocess.run(args, check=False, capture_output=True)
    return int(completed.returncode)


def export_xmp(
    files: list[dict[str, Any]],
    rels: list[str],
    xmp_root: Path,
    *,
    runner: RunnerFn | None = None,
    which: WhichFn | None = None,
    stderr: TextIO | None = None,
) -> bool:
    """Return True if ExifTool was invoked. Never modifies source files."""
    err = stderr or sys.stderr
    finder = which or shutil.which
    found = finder("exiftool")
    if not found:
        print("warning: ExifTool not on PATH; skipping XMP export", file=err)
        return False
    run = runner or default_exiftool_runner
    for item, rel in zip(files, rels):
        dest = xmp_root / Path(rel).parent / (Path(rel).name + ".xmp")
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = [found, "-o", str(dest)]
        for subject in item["dc:subject"]:
            args.append(f"-XMP-dc:Subject={subject}")
        args.append(f"-XMP-xmp:Label={item['xmp:Label']}")
        args.append(f"-XMP-xmp:Rating={item['xmp:Rating']}")
        args.append(f"-XMP-xmp:MetadataDate={item['xmp:MetadataDate']}")
        args.append(item["file"])
        code = run(args)
        if code != 0:
            print(f"warning: ExifTool exited {code} for {item['file']}", file=err)
    return True
