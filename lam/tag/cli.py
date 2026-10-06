# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lam tag scan`` and ``lam tag plan``. Neither command applies a plan."""

from __future__ import annotations

import sys
from pathlib import Path

from lam.log import finish_run, start_run
from lam.tag.bins import (
    DEFAULT_BURST_SECONDS,
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MODEL,
    DEFAULT_MODEL_URL,
    DEFAULT_WEIGHT_MIN_BYTES,
)
from lam.tag.errors import TagError
from lam.tag.model import OllamaTagger
from lam.tag.plan import run_tag_plan
from lam.tag.scan import run_tag_scan


def add_tag_parser(subparsers) -> None:
    tag = subparsers.add_parser(
        "tag",
        help="Layered sorter: scan files and write a reviewable plan (does not move files)",
    )
    commands = tag.add_subparsers(dest="tag_cmd", required=True)
    scan = commands.add_parser("scan", help="Walk a folder and write lam-tags/v1")
    scan.add_argument("folder", help="Folder to walk. .git, symlinks, and junctions are skipped.")
    scan.add_argument("--out", required=True, help="Tags JSON path (lam-tags/v1)")
    scan.add_argument("--no-model", action="store_true", help="Rules only; do not call Ollama")
    scan.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default {DEFAULT_MODEL})")
    scan.add_argument("--model-url", default=DEFAULT_MODEL_URL, help="Local Ollama base URL")
    scan.add_argument("--xmp", action="store_true", help="Write .xmp sidecars with ExifTool if it is on PATH")
    scan.add_argument("--level", type=int, default=1, help="1 = broad bins, 2 = one sub-bin pass")
    scan.add_argument("--bin", default=None, help="Level 2 parent bin, for example _Maps")
    scan.add_argument("--subbins", default=None, help="Level 2 lam-subbins/v1 file")
    scan.add_argument("--burst-window", type=int, default=DEFAULT_BURST_SECONDS, help="Burst window in seconds")
    scan.add_argument("--max-image-bytes", type=int, default=DEFAULT_MAX_IMAGE_BYTES)
    scan.add_argument("--weight-min-bytes", type=int, default=DEFAULT_WEIGHT_MIN_BYTES)

    plan = commands.add_parser("plan", help="Write a file-action-plan/v1 from tags (no --apply)")
    plan.add_argument("tags", help="Tags JSON from lam tag scan")
    plan.add_argument("--dest", required=True, help="Library root. Level 1 moves into <dest>\\<bin>\\")
    plan.add_argument("--level", type=int, required=True, help="1 or 2. Must match the tags file.")
    plan.add_argument("--out", required=True, help="Plan JSON path")
    plan.add_argument("--bin", default=None, help="Level 2 parent bin")
    plan.add_argument("--subbins", default=None, help="Level 2 lam-subbins/v1 file")


def run_tag_command(args) -> int:
    command = "tag-scan" if args.tag_cmd == "scan" else "tag-plan" if args.tag_cmd == "plan" else "tag"
    out = Path(getattr(args, "out", "") or ".")
    run = start_run(command, out.parent)
    ok = skipped = failed = 0
    try:
        if args.tag_cmd == "scan":
            tagger = None
            if not args.no_model:
                tagger = OllamaTagger(model=args.model, base_url=args.model_url)
            document = run_tag_scan(
                args.folder,
                out=args.out,
                no_model=args.no_model,
                tagger=tagger,
                model_url=args.model_url,
                level=args.level,
                bin_name=args.bin,
                subbins_path=args.subbins,
                burst_window=args.burst_window,
                max_image_bytes=args.max_image_bytes,
                weight_min_bytes=args.weight_min_bytes,
                xmp=args.xmp,
            )
            print(str(Path(args.out).resolve()))
            print(f"files={len(document['files'])} model={document['model']}", file=sys.stderr)
            ok = len(document["files"])
            return 0
        if args.tag_cmd == "plan":
            built = run_tag_plan(
                args.tags,
                dest=args.dest,
                level=args.level,
                out=args.out,
                bin_name=args.bin,
                subbins_path=args.subbins,
            )
            print(str(Path(built["plan_path"]).resolve()))
            moves = sum(1 for action in built["plan"]["actions"] if action["op"] == "move")
            actions = len(built["plan"]["actions"])
            print(
                f"actions={actions} moves={moves} review={len(built['review']['items'])}",
                file=sys.stderr,
            )
            ok = moves
            skipped = actions - moves
            return 0
        print("unknown tag command", file=sys.stderr)
        failed = 1
        return 2
    except TagError as exc:
        print(str(exc), file=sys.stderr)
        failed = 1
        return 2
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        failed = 1
        return 2
    finally:
        finish_run(run, ok=ok, skipped=skipped, failed=failed)
