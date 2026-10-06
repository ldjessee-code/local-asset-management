# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lam oversize scan`` and ``lam oversize list``."""

from __future__ import annotations

import json
import sys

from lam.log import finish_run, start_run
from lam.oversize.engine import format_table, run_oversize_scan
from lam.oversize.errors import OversizeError
from lam.oversize.register import export_csv, load_register, resolve_register_path


def add_oversize_parser(subparsers) -> None:
    oversize = subparsers.add_parser(
        "oversize",
        help="Find oversize images, write proxies, and keep one central register",
    )
    commands = oversize.add_subparsers(dest="oversize_cmd", required=True)
    scan = commands.add_parser(
        "scan",
        help="Dry-run by default. --record writes the register. --apply also builds proxies",
    )
    scan.add_argument("folder", help="Folder to walk. .git, symlinks, and junctions are skipped.")
    scan.add_argument("--min-mb", type=float, default=100, help="Oversize when file bytes exceed this many MiB (default 100)")
    scan.add_argument("--min-mp", type=float, default=89, help="Oversize when width*height exceeds this many megapixels (default 89)")
    scan.add_argument("--max-dim", type=int, default=8000, help="Proxy long side in pixels (default 8000). Never upscales.")
    scan.add_argument("--set-depth", type=int, default=1, help="Set folder is this many levels below FOLDER (default 1)")
    scan.add_argument("--register", default=None, help="Register JSON. Default: LAM_OVERSIZE_REGISTER or %%LOCALAPPDATA%%\\lam\\oversize-register.json")
    scan.add_argument("--record", action="store_true", help="Write the register only. No image files.")
    scan.add_argument("--apply", action="store_true", help="Build proxies (and zips). Implies --record.")
    scan.add_argument(
        "--zip",
        dest="zip_mode",
        choices=("combined-with-parts", "combined", "all", "none"),
        default="combined-with-parts",
        help="Which originals to zip and recycle after a verified zip (default combined-with-parts)",
    )
    scan.add_argument("--proxy-suffix", default="_max8000", help="Inserted before the proxy extension (default _max8000)")
    scan.add_argument("--quality", type=int, default=90, help="JPEG quality 1-100 (default 90)")
    scan.add_argument(
        "--part-scope",
        choices=("pack", "set"),
        default="pack",
        help="Where to look for part maps. pack searches the whole folder (default). set stays inside --set-depth",
    )
    scan.add_argument("--min-score", type=float, default=0.90, help="Minimum normalised correlation to accept a part (default 0.90)")
    scan.add_argument("--min-margin", type=float, default=0.05, help="Minimum gap between the best and second peak (default 0.05)")
    scan.add_argument("--min-coverage", type=float, default=0.97, help="Located area / combined area required for a complete set (default 0.97)")
    scan.add_argument("--preview-dir", default=None, help="Write check previews here. Allowed on a dry run. Must not be inside FOLDER")
    scan.add_argument("--preview-standins", action="store_true", help="Also write a stand-in preview for maps that are not combined")
    scan.add_argument("--plan-out", default=None, help="Write a plan CSV. Allowed on a dry run. Refuses to overwrite")
    scan.add_argument(
        "--zip-root",
        default=None,
        help=(
            "Folder for verified zips. Default: FOLDER\\_Originals_Zipped. "
            "Each zip mirrors the original's directory relative to this folder's parent. "
            "Parent folders are created only with --apply."
        ),
    )
    scan.add_argument("--json", action="store_true", help="Print JSON instead of a table")

    listing = commands.add_parser("list", help="Print or export the register. Does not scan.")
    listing.add_argument("--register", default=None, help="Register JSON. Same default as scan.")
    listing.add_argument("--csv", default=None, help="Export CSV. Refuses to overwrite an existing file.")
    listing.add_argument("--status", choices=("planned", "done", "error"), default=None, help="Keep only this status")
    listing.add_argument("--json", action="store_true", help="Print JSON")


def run_oversize_command(args) -> int:
    try:
        if args.oversize_cmd == "scan":
            payload, code = run_oversize_scan(
                args.folder,
                min_mb=args.min_mb,
                min_mp=args.min_mp,
                max_dim=args.max_dim,
                set_depth=args.set_depth,
                register=args.register,
                record=args.record,
                apply=args.apply,
                zip_mode=args.zip_mode,
                proxy_suffix=args.proxy_suffix,
                quality=args.quality,
                part_scope=args.part_scope,
                min_score=args.min_score,
                min_margin=args.min_margin,
                min_coverage=args.min_coverage,
                preview_dir=args.preview_dir,
                preview_standins=args.preview_standins,
                plan_out=args.plan_out,
                zip_root=args.zip_root,
            )
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(format_table(payload), end="")
            if code == 4:
                print(f"oversize: {payload['errors']} file(s) errored", file=sys.stderr)
            elif args.record or args.apply:
                print(f"register: {payload['register']}", file=sys.stderr)
            return code
        if args.oversize_cmd == "list":
            return _list_command(args)
    except OversizeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("unknown oversize command", file=sys.stderr)
    return 2


def _list_command(args) -> int:
    path = resolve_register_path(args.register)
    if not path.is_file():
        raise OversizeError(f"register not found: {path}")
    run = start_run("oversize-list", path.parent)
    ok = failed = 0
    try:
        document = load_register(path)
        entries = document["entries"]
        if args.status:
            entries = [entry for entry in entries if entry["status"] == args.status]
        if args.csv:
            export_csv(path=path_of(args.csv), entries=entries)
            print(str(path_of(args.csv).resolve()), file=sys.stderr)
        payload = {"schema": document["schema"], "entries": entries}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif not args.csv:
            print(f"entries={len(entries)} register={path.resolve()}")
        ok = len(entries)
        return 0
    finally:
        if sys.exc_info()[0] is not None:
            failed = 1
        finish_run(run, ok=ok, skipped=0, failed=failed)


def path_of(value: str):
    from pathlib import Path

    return Path(value)
