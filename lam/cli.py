# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command-line entry: ``lam`` or ``python -m lam``.

``scan`` / ``report`` / ``plan`` / ``apply`` need a config (default:
``library.jsonc`` in the current directory). ``serve`` and ``capabilities``
do not.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from lam.version import __version__
from lam.apply import run_apply
from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.config import ConfigError, find_default_config, load_config
from lam.jobs import human_progress
from lam.plan import run_plan
from lam.report import write_reports
from lam.scan import run_scan

SOURCE_URL = "https://github.com/ldjessee-code/local-asset-management"
NO_CONFIG_HINT = (
    "No library.jsonc in the current directory.\n"
    "  • Run `lam serve` and set sources + destination in the UI, or\n"
    "  • Put library.jsonc here (not inside a source or the destination), or\n"
    "  • Pass --config PATH, or set LAM_CONFIG."
)


def _progress(phase: str, payload: dict) -> None:
    print(human_progress(phase, payload), file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lam",
        description="Local Asset Management — scan, report, plan, apply, serve.",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Config file. Default: library.jsonc (or library.json) in the current directory, or LAM_CONFIG.",
    )
    p.add_argument("--version", action="version", version=f"lam {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="Walk sources, hash collisions, index zips")
    sub.add_parser("report", help="Write reports/summary.md and exceptions.md")
    sub.add_parser("plan", help="Build copy/quarantine plan from the last scan")
    ap = sub.add_parser("apply", help="Execute the last plan (never deletes in v0.1)")
    ap.add_argument("--yes", action="store_true", help="Required to actually copy")

    serve = sub.add_parser("serve", help="Open the local / LAN web UI (setup works with no config file)")
    serve.add_argument("--host", help="Bind address (default from config or 127.0.0.1)")
    serve.add_argument("--port", type=int, help="Bind port (default 8765)")
    serve.add_argument("--token", help="UI token (default LAM_TOKEN or a generated value)")
    serve.add_argument("--no-browser", action="store_true", help="Do not open a browser")

    caps = sub.add_parser("capabilities", help="What this program can do (for humans and AIs)")
    caps.add_argument("--json", action="store_true", help="Print JSON instead of text")
    return p


def resolve_config_path(explicit: str | None, *, required: bool) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigError(f"config not found: {p}")
        return p.resolve()
    found = find_default_config()
    if found:
        return found
    if required:
        raise ConfigError(NO_CONFIG_HINT)
    return None


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        cfg_path = resolve_config_path(args.config, required=(args.cmd not in {"serve", "capabilities"}))
        cfg = load_config(cfg_path) if cfg_path else None
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc

    if args.cmd == "scan":
        stats = run_scan(cfg, progress=_progress)
        print(json.dumps(stats, indent=2))
        return
    if args.cmd == "report":
        data = write_reports(cfg)
        print(json.dumps({k: data[k] for k in ("files", "bytes", "wasted_bytes", "summary_path", "exceptions_path") if k in data}, indent=2))
        return
    if args.cmd == "plan":
        summary = run_plan(cfg, progress=_progress)
        print(json.dumps(summary, indent=2))
        return
    if args.cmd == "apply":
        if not args.yes:
            raise SystemExit("apply is a write. Re-run with --yes after reviewing `lam plan`.")
        stats = run_apply(cfg, yes=True, progress=_progress)
        print(json.dumps(stats, indent=2))
        return
    if args.cmd == "serve":
        try:
            _serve(
                cfg,
                host=args.host,
                port=args.port,
                token=args.token,
                open_browser=not args.no_browser,
            )
        except KeyboardInterrupt:
            print("\nStopped.", file=sys.stderr)
        return
    if args.cmd == "capabilities":
        data = describe_capabilities()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(format_capabilities_text(data), end="")
        return
    raise SystemExit(f"unknown command {args.cmd}")


def _serve(
    cfg,
    host: str | None,
    port: int | None,
    token: str | None,
    *,
    open_browser: bool = True,
) -> None:
    import threading
    import webbrowser

    import uvicorn

    from lam.web.app import create_app

    bind_host = host or (cfg.serve.host if cfg else "127.0.0.1")
    bind_port = int(port or (cfg.serve.port if cfg else 8765))
    tok = token or os.environ.get("LAM_TOKEN") or secrets.token_urlsafe(18)
    app = create_app(cfg, token=tok)
    browse_host = "127.0.0.1" if bind_host in {"0.0.0.0", "::"} else bind_host
    url = f"http://{browse_host}:{bind_port}/?token={tok}"

    print(f"Local Asset Management {__version__}", file=sys.stderr)
    if cfg:
        print(f"Config: {cfg.config_path}", file=sys.stderr)
    else:
        print("Config: (none) — set sources and destination in the UI", file=sys.stderr)
    print(f"UI:     {url}", file=sys.stderr)
    if bind_host in {"0.0.0.0", "::"}:
        print("Bound on all interfaces — use from the home LAN, not the public internet.", file=sys.stderr)
    print(file=sys.stderr)
    print(f"Token:  {tok}", file=sys.stderr)
    print(file=sys.stderr)
    print("Type q and press Enter to stop. Or use Stop in the UI.", file=sys.stderr)
    print("Keep this window open while you use the app.", file=sys.stderr)
    print(f"License AGPL-3.0-or-later. Source: {SOURCE_URL}", file=sys.stderr)

    config = uvicorn.Config(app, host=bind_host, port=bind_port, reload=False)
    server = uvicorn.Server(config)
    app.state.server = server

    def watch_quit() -> None:
        try:
            if not sys.stdin or not sys.stdin.isatty():
                return
        except OSError:
            return
        while not server.should_exit:
            try:
                line = sys.stdin.readline()
            except (OSError, KeyboardInterrupt):
                return
            if not line:
                return
            if line.strip().lower() in {"q", "quit", "exit", "stop"}:
                server.should_exit = True
                return

    threading.Thread(target=watch_quit, daemon=True, name="lam-quit").start()
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    server.run()
    print("\nStopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
