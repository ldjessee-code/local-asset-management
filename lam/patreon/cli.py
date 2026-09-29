# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lam patreon validate-config|list|sync``."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from lam.patreon.config import load_creators_file
from lam.patreon.cookies import load_cookie
from lam.patreon.dates import local_zone, now_local, resolve_window
from lam.patreon.engine import run_list, run_sync
from lam.patreon.errors import (
    EXIT_DEPS,
    EXIT_USAGE,
    GATE_DEPS,
    GATE_DEPS_STUB,
    PatreonError,
    PatreonUsageError,
    schema_gate,
)
from lam.patreon.http_source import FixtureSource, HttpPatreonSource
from lam.patreon.paths import check_staging_root


def add_patreon_parser(sub: argparse._SubParsersAction) -> None:
    patreon = sub.add_parser(
        "patreon",
        help="List or stage Patreon posts your membership already includes",
    )
    commands = patreon.add_subparsers(dest="patreon_cmd", required=True)

    validate = commands.add_parser("validate-config", help="Check a patreon-creators JSON file offline")
    validate.add_argument("path", help="Path to a patreon-creators/v1 JSON file")

    listing = commands.add_parser("list", help="List posts in a date window (read-only)")
    _common(listing)
    listing.add_argument("--format", choices=("text", "json"), default="text")

    sync = commands.add_parser("sync", help="Stage new files (dry-run unless --apply)")
    _common(sync)
    mode = sync.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="List what would be fetched (the default)")
    mode.add_argument("--apply", action="store_true", help="Download into the staging inbox")
    sync.add_argument("--csv", action="store_true", help="Also write a CSV next to the manifest")


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--creators", required=True, help="patreon-creators/v1 JSON file")
    parser.add_argument("--creator", help="Only this slug")
    parser.add_argument("--since", help="Inclusive local start date YYYY-MM-DD")
    parser.add_argument("--until", help="Exclusive local end date YYYY-MM-DD")
    parser.add_argument("--last-months", type=int, help="Window starts N calendar months ago at 00:00 local")
    parser.add_argument("--max-posts", type=int, help="Stop after this many posts in the window")
    parser.add_argument("--results", help="Write the JSON report here")
    parser.add_argument("--fixture-dir", help="Read recorded fixtures instead of the network")
    parser.add_argument(
        "--backend",
        choices=("http", "patreon-dl"),
        default="http",
        help="http uses the logged-in web API (default). patreon-dl is not enabled in v1.",
    )


def _print_warnings(warnings) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _date_arg(value: str | None, flag: str) -> str | None:
    if value is None:
        return None
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise PatreonUsageError(schema_gate(f"{flag} must be YYYY-MM-DD"))
    return value


def _window_from_args(args: argparse.Namespace):
    since = _date_arg(getattr(args, "since", None), "--since")
    until = _date_arg(getattr(args, "until", None), "--until")
    last = getattr(args, "last_months", None)
    if since and last is not None:
        raise PatreonUsageError(schema_gate("pass only one of --since and --last-months"))
    if last is not None and last < 0:
        raise PatreonUsageError(schema_gate("--last-months must be >= 0"))
    max_posts = getattr(args, "max_posts", None)
    if max_posts is not None and max_posts < 1:
        raise PatreonUsageError(schema_gate("--max-posts must be >= 1"))
    zone = local_zone()
    try:
        return resolve_window(now=now_local(zone), since=since, until=until, last_months=last, zone=zone)
    except (TypeError, ValueError):
        raise PatreonUsageError(schema_gate("could not read the date window")) from None


def _backend_gate(name: str) -> int | None:
    if name != "patreon-dl":
        return None
    if shutil.which("node") is None or shutil.which("patreon-dl") is None:
        print(GATE_DEPS, file=sys.stderr)
        return EXIT_DEPS
    print(GATE_DEPS_STUB, file=sys.stderr)
    return EXIT_DEPS


def _source(args: argparse.Namespace):
    fixture = getattr(args, "fixture_dir", None)
    if fixture:
        return FixtureSource(Path(fixture))
    loaded = load_cookie()
    print(f"cookie source: {loaded.source}", file=sys.stderr)
    for warning in loaded.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return HttpPatreonSource(loaded.header, min_delay=0.4, retry_cap=5.0)


def _validate(args: argparse.Namespace) -> int:
    loaded = load_creators_file(Path(args.path))
    _print_warnings(loaded.warnings)
    print(f"{loaded.schema}: {len(loaded.creators)} creators")
    return 0


def _list(args: argparse.Namespace) -> int:
    loaded = load_creators_file(Path(args.creators))
    _print_warnings(loaded.warnings)
    gated = _backend_gate(args.backend)
    if gated is not None:
        return gated
    window = _window_from_args(args)
    source = _source(args)
    return run_list(
        loaded,
        source,
        window=window,
        only_slug=args.creator,
        max_posts=args.max_posts,
        fmt=args.format,
        results_path=Path(args.results) if args.results else None,
    )


def _sync(args: argparse.Namespace) -> int:
    loaded = load_creators_file(Path(args.creators))
    _print_warnings(loaded.warnings)
    gated = _backend_gate(args.backend)
    if gated is not None:
        return gated
    check_staging_root(Path(loaded.staging_root))
    window = _window_from_args(args)
    source = _source(args)
    return run_sync(
        loaded,
        source,
        window=window,
        only_slug=args.creator,
        max_posts=args.max_posts,
        apply=bool(args.apply),
        results_path=Path(args.results) if args.results else None,
        csv=bool(args.csv),
    )


def run_patreon_command(args: argparse.Namespace) -> int:
    try:
        if args.patreon_cmd == "validate-config":
            return _validate(args)
        if args.patreon_cmd == "list":
            return _list(args)
        if args.patreon_cmd == "sync":
            return _sync(args)
    except PatreonError as exc:
        print(str(exc), file=sys.stderr)
        return int(exc.exit_code)
    return EXIT_USAGE
