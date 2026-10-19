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
from lam.stage.single import place_file, stage_file


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
        help="Stage one file, or create/clean a job outside Dropbox",
    )
    stage.add_argument(
        "head",
        nargs="?",
        help="FILE to stage, or the subcommand new / clean",
    )
    stage.add_argument("--owner", default=None, help="stage new: path segment for the owner")
    stage.add_argument("--job", default=None, help="stage new: path segment for the job name")
    stage.add_argument("--target", default=None, help="stage new: folder place will copy into")
    stage.add_argument("--root", default=None, help="Staging root. Default: C:\\CourtStaging, else F:\\CourtStaging")
    stage.add_argument("--plan", default=None, help="stage new: optional plan path stored on the job")
    stage.add_argument("--kind", choices=KINDS, default=None, help="stage new: naming pattern. map uses a 100000000 byte cap")
    stage.add_argument("--byte-cap", type=int, default=None, help="stage new: override the file-size cap in bytes")
    stage.add_argument("--apply", action="store_true", help="stage clean: recycle expired payload and flag stale jobs")
    stage.add_argument("--json", action="store_true", help="stage FILE: print a lam-place/v1 document")


def add_place_parser(subparsers) -> None:
    place = subparsers.add_parser(
        "place",
        help="Copy a job out folder, or one staged file, into a target (never move)",
    )
    place.add_argument("jobdir", help="Job folder, or the staged file when DEST is also given")
    place.add_argument("file_dest", nargs="?", default=None, help="Single-file dest. Omit for a job folder")
    place.add_argument("--expect-sha256", default=None, help="Single-file: required old dest sha, or pass --new")
    place.add_argument("--new", action="store_true", help="Single-file: dest must not exist yet")
    place.add_argument("--backup-dir", default=None, help="Single-file: directory for the kept backup")
    place.add_argument("--root", default=None, help="Single-file: staged file must sit under this root")
    place.add_argument("--grant", default=None, help="Single-file: dest must sit under this prefix")
    place.add_argument("--json", action="store_true", help="Single-file: print a lam-place/v1 document")
    place.add_argument("--strict", action="store_true", help="Job place: block when a file name warns")
    place.add_argument("--jobs-dir", default=None, help="Job place: copy small text logs here")
    place.add_argument("--kind", choices=KINDS, default=None, help="Job place: override job.json kind for this run")
    place.add_argument("--byte-cap", type=int, default=None, help="Job place: override the file-size cap in bytes")
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


def _stage_usage() -> int:
    print(
        "usage: lam stage FILE [--root ROOT] [--json]\n"
        "   or: lam stage new --owner OWNER --job JOB --target TARGET [--root ROOT]\n"
        "   or: lam stage clean [--root ROOT] [--apply]",
        file=sys.stderr,
    )
    return 2


def _stage_job_flags(args) -> bool:
    return bool(
        args.owner or args.job or args.target or args.plan or args.kind or args.byte_cap is not None or args.apply
    )


def run_stage_command(args) -> int:
    head = args.head
    try:
        if head == "new":
            if args.json or args.apply:
                print("lam stage new does not take --json or --apply", file=sys.stderr)
                return 2
            if not args.owner or not args.job or not args.target:
                print("stage new requires --owner, --job, and --target", file=sys.stderr)
                return 2
            folder = create_job(
                owner=args.owner,
                job=args.job,
                target=args.target,
                root=args.root,
                plan=args.plan,
                kind=args.kind or "generic",
                byte_cap=args.byte_cap,
            )
            print(folder)
            return 0
        if head == "clean":
            if args.json or args.owner or args.job or args.target or args.plan or args.kind or args.byte_cap is not None:
                print("lam stage clean only takes --root and --apply", file=sys.stderr)
                return 2
            return clean_staging(root=args.root, apply=args.apply)
        if not head:
            return _stage_usage()
        if _stage_job_flags(args):
            print("lam stage FILE only takes --root and --json", file=sys.stderr)
            return 2
        return stage_file(head, root=args.root, json_out=args.json)
    except StageError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _single_file_place(args) -> bool:
    return bool(
        args.file_dest
        or args.expect_sha256
        or args.new
        or args.backup_dir
        or args.root
        or args.grant
        or args.json
    )


def _job_place_flags(args) -> bool:
    if args.strict or args.replace or args.jobs_dir or args.kind or args.byte_cap is not None:
        return True
    return args.on_conflict != "stop"


def run_place_command(args) -> int:
    if _single_file_place(args):
        if not args.file_dest:
            print(
                "usage: lam place STAGED DEST (--expect-sha256 HEX | --new) [--dry-run] "
                "[--backup-dir DIR] [--root ROOT] [--grant PREFIX] [--json]",
                file=sys.stderr,
            )
            return 2
        if _job_place_flags(args):
            print(
                "single-file place does not take --replace, --strict, --on-conflict, "
                "--jobs-dir, --kind, or --byte-cap",
                file=sys.stderr,
            )
            return 2
        try:
            return place_file(
                args.jobdir,
                args.file_dest,
                expect_sha256=args.expect_sha256,
                is_new=args.new,
                dry_run=args.dry_run,
                backup_dir=args.backup_dir,
                root=args.root,
                grant=args.grant,
                json_out=args.json,
            )
        except StageError as exc:
            print(str(exc), file=sys.stderr)
            return exc.code
        except OSError as exc:
            print(str(exc), file=sys.stderr)
            return 1
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
