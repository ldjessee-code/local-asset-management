# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lam token login|get|status`` subcommands."""

from __future__ import annotations

import argparse
import sys

from lam.token.errors import EXIT_AUTH, EXIT_OK, TokenError, gate
from lam.token.session import fetch_cookies, load_active_profiles, login, site_status


def add_token_parser(sub: argparse._SubParsersAction) -> None:
    token = sub.add_parser(
        "token",
        help="Fetch a site session cookie from a persistent browser profile",
    )
    tsub = token.add_subparsers(dest="token_cmd", required=True)

    login_p = tsub.add_parser("login", help="Open a visible browser so you can log in by hand")
    login_p.add_argument("site", help="Site id (for example patreon)")
    login_p.add_argument("--profiles", help="Optional lam-site-profiles/v1 JSON file")

    get_p = tsub.add_parser("get", help="Read the saved session and write the cookie header to the secrets file")
    get_p.add_argument("site", help="Site id (for example patreon)")
    get_p.add_argument(
        "--set-user-env",
        action="store_true",
        help="Also set the User environment variable named in the site profile",
    )
    get_p.add_argument("--profiles", help="Optional lam-site-profiles/v1 JSON file")

    status_p = tsub.add_parser("status", help="Show whether a saved login exists (offline unless --check)")
    status_p.add_argument("site", nargs="?", help="Site id (default: every enabled site)")
    status_p.add_argument(
        "--check",
        action="store_true",
        help="Make one who-am-I request (needs a saved login)",
    )
    status_p.add_argument("--profiles", help="Optional lam-site-profiles/v1 JSON file")


def _profiles(args: argparse.Namespace):
    path = getattr(args, "profiles", None)
    return load_active_profiles(path)


def _print_warnings(profiles) -> None:
    for warning in profiles.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _print_selection(site_id: str, selection) -> None:
    expiry = "session"
    if selection.earliest_expiry is not None:
        expiry = selection.earliest_expiry.isoformat()
    print(f"{site_id}: cookie header ready")
    print(f"  names: {', '.join(selection.names)}")
    print(f"  count: {selection.count}")
    print(f"  length: {selection.total_length}")
    print(f"  earliest expiry: {expiry}")
    print("  required: present" if selection.required_present else "  required: missing")


def _print_status_row(row: dict) -> None:
    site = row["site"]
    if not row.get("profile_exists"):
        print(f"{site}: no saved login")
        print(f"  run: lam token login {site}")
        return
    print(f"{site}: {row.get('message') or 'saved login'}")
    names = row.get("cookie_names") or []
    if names:
        print(f"  names: {', '.join(names)}")
    if row.get("cookie_count") is not None:
        print(f"  count: {row['cookie_count']}")
    if row.get("header_length") is not None:
        print(f"  length: {row['header_length']}")
    if row.get("earliest_expiry"):
        print(f"  earliest expiry: {row['earliest_expiry']}")
    else:
        print("  earliest expiry: session or unknown")
    required = row.get("required_present")
    if required is True:
        print("  required: present")
    elif required is False:
        print("  required: missing")
    else:
        print("  required: unknown")
    if row.get("secrets_mtime"):
        print(f"  secrets file: {row['secrets_mtime']}")
    else:
        print("  secrets file: (none)")
    last = row.get("last_check")
    if last:
        print(f"  last check: {last}")
    else:
        print("  last check: (none)")


def run_token_command(args: argparse.Namespace) -> int:
    try:
        profiles = _profiles(args)
        _print_warnings(profiles)
        cmd = args.token_cmd
        if cmd == "login":
            login(args.site, profiles=profiles)
            print(f"{args.site}: login window closed; session stored in the persistent profile")
            return EXIT_OK
        if cmd == "get":
            site_profile, selection = fetch_cookies(
                args.site,
                write_secrets=True,
                set_user_env=bool(getattr(args, "set_user_env", False)),
                profiles=profiles,
            )
            _print_selection(site_profile.id, selection)
            print("  wrote secrets file: yes")
            return EXIT_OK
        if cmd == "status":
            rows = site_status(args.site, check=bool(getattr(args, "check", False)), profiles=profiles)
            if not rows:
                print("no enabled sites", file=sys.stderr)
                return EXIT_AUTH
            for row in rows:
                _print_status_row(row)
            if any(not row.get("profile_exists") for row in rows):
                missing = [row["site"] for row in rows if not row.get("profile_exists")]
                print(
                    gate(
                        "auth",
                        f"no saved login for {missing[0]} - run: lam token login {missing[0]}",
                    ),
                    file=sys.stderr,
                )
                return EXIT_AUTH
            return EXIT_OK
        raise TokenError(f"GATE usage: unknown token command {cmd}")
    except TokenError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
