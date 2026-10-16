# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lam stage``, ``lam place``, and ``lam publish``."""

from __future__ import annotations

import sys
from pathlib import Path

from lam.oversize.errors import MissingPyvips
from lam.stage.clean import clean_staging
from lam.stage.errors import StageError
from lam.stage.job import KINDS, create_job
from lam.stage.paths import default_jobs_dir
from lam.stage.place import place_job
from lam.stage.publish import publish_file


def _add_conflict_flags(parser) -> None:
    parser.add_argument(
        "--on-conflict",
        choices=("stop", "suffix"),
        default="stop",
        help="Different hash: stop (default) or write name_v2, name_v3, ...",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Send the existing dest to the Recycle Bin, then copy. Never a hard delete.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write nothing")


def add_stage_parser(subparsers) -> None:
    stage = subparsers.add_parser(
        "stage",
        help="Create or clean a staging job outside Dropbox",
    )
    commands = stage.add_subparsers(dest="stage_cmd", required=True)
    new = commands.add_parser("new", help="Create owner/job_timestamp with in, work, out, logs, and job.json")
    new.add_argument("--owner", required=True, help="Path segment for the owner")
    new.add_argument("--job", required=True, help="Path segment for the job name")
    new.add_argument("--target", required=True, help="Dropbox (or other) folder place will copy into")
    new.add_argument("--root", default=None, help="Staging root. Default: C:\\CourtStaging, else F:\\CourtStaging")
    new.add_argument("--plan", default=None, help="Optional plan path stored on the job")
    new.add_argument("--kind", choices=KINDS, default="generic", help="Naming pattern. map uses a 100000000 byte cap")
    new.add_argument("--byte-cap", type=int, default=None, help="Override the file-size cap in bytes")
    clean = commands.add_parser("clean", help="Recycle old placed payload. Dry-run unless --apply")
    clean.add_argument("--root", default=None, help="Staging root. Same default as stage new")
    clean.add_argument("--apply", action="store_true", help="Recycle expired payload and flag stale jobs")


def add_place_parser(subparsers) -> None:
    place = subparsers.add_parser(
        "place",
        help="Copy a staging out folder into its target (never move)",
    )
    place.add_argument("jobdir", help="Job folder that contains job.json")
    place.add_argument("--strict", action="store_true", help="Block when a file name warns")
    place.add_argument("--jobs-dir", default=None, help="Copy small text logs here. Default: court _jobs/<job>")
    place.add_argument("--kind", choices=KINDS, default=None, help="Override job.json kind for this run")
    place.add_argument("--byte-cap", type=int, default=None, help="Override the file-size cap in bytes")
    _add_conflict_flags(place)


def add_publish_parser(subparsers) -> None:
    publish = subparsers.add_parser(
        "publish",
        help="Copy one live file via temp, rename retry, and SHA-256 verify",
    )
    publish.add_argument("file", help="Source file")
    publish.add_argument("dest", help="Destination file")
    publish.add_argument("--undo-log", default=None, help="Undo CSV. Default: <dest>.publish-undo.csv")
    _add_conflict_flags(publish)


def run_stage_command(args) -> int:
    try:
        if args.stage_cmd == "new":
            folder = create_job(
                owner=args.owner,
                job=args.job,
                target=args.target,
                root=args.root,
                plan=args.plan,
                kind=args.kind,
                byte_cap=args.byte_cap,
            )
            print(folder)
            return 0
        if args.stage_cmd == "clean":
            return clean_staging(root=args.root, apply=args.apply)
    except StageError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("unknown stage command", file=sys.stderr)
    return 2


def run_place_command(args) -> int:
    try:
        jobs_dir = args.jobs_dir
        if jobs_dir is None:
            # Resolved after load inside place when omitted: use the flag path here
            # only when the caller left it empty. place_job loads the job name.
            from lam.stage.job import load_job

            job = load_job(Path(args.jobdir) / "job.json")
            jobs_dir = str(default_jobs_dir(str(job["job"])))
        return place_job(
            args.jobdir,
            dry_run=args.dry_run,
            strict=args.strict,
            on_conflict=args.on_conflict,
            replace=args.replace,
            jobs_dir=jobs_dir,
            kind=args.kind,
            byte_cap=args.byte_cap,
        )
    except StageError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except MissingPyvips as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def run_publish_command(args) -> int:
    try:
        return publish_file(
            args.file,
            args.dest,
            dry_run=args.dry_run,
            on_conflict=args.on_conflict,
            replace=args.replace,
            undo_log=args.undo_log,
        )
    except StageError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2
