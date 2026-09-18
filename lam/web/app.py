# SPDX-License-Identifier: AGPL-3.0-or-later
"""LAN web UI. The engine does the I/O; the browser only drives jobs."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from lam.version import __version__
from lam.apply import run_apply
from lam.capabilities import describe_capabilities
from lam.config import (
    LAYOUT_NAMES,
    ConfigError,
    LibraryConfig,
    find_default_config,
    load_config,
    setup_from_form,
)
from lam.jobs import JobBusy, JobManager
from lam.library_view import browse, compare_grid, overview, zip_contents
from lam.plan import list_plan, run_plan
from lam.report import collect_report, read_report_file, write_reports
from lam.scan import run_deep_scan, run_scan

STATIC = Path(__file__).resolve().parent / "static"
SOURCE_URL = "https://github.com/ldjessee-code/local-asset-management"


class NeedsSetup(Exception):
    pass


def create_app(cfg: LibraryConfig | None, token: str) -> FastAPI:
    """Build the LAN UI. ``cfg`` may be None; the setup screen writes a config."""
    app = FastAPI(
        title="Local Asset Management",
        version=__version__,
        description=(
            "Python engine for inventory, exact-dedupe, layout, and quarantine. "
            "The browser only drives jobs. See GET /api/capabilities."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    jobs = JobManager()
    app.state.cfg = cfg
    app.state.token = token
    app.state.jobs = jobs
    app.state.server = None

    @app.exception_handler(NeedsSetup)
    async def _needs_setup(_request: Request, _exc: NeedsSetup):
        return JSONResponse(
            {"error": "Set up sources and destination first.", "needs_setup": True},
            status_code=409,
        )

    @app.exception_handler(ConfigError)
    async def _config_error(_request: Request, exc: ConfigError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        path = request.url.path
        public = {
            "/",
            "/favicon.ico",
            "/license",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/capabilities",
        }
        if path in public or path.startswith("/static") or path.startswith("/docs"):
            return await call_next(request)
        if path.startswith("/api/"):
            got = (
                request.headers.get("x-lam-token")
                or _bearer(request.headers.get("authorization"))
                or request.query_params.get("token")
            )
            if token and got != token:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/license")
    def license_text():
        root = Path(__file__).resolve().parents[2]
        lic = root / "LICENSE"
        if lic.is_file():
            return PlainTextResponse(lic.read_text(encoding="utf-8"))
        return PlainTextResponse("AGPL-3.0-or-later\n" + SOURCE_URL + "\n")

    @app.get("/api/capabilities")
    def api_capabilities():
        """Machine-readable inventory: commands, layouts, HTTP API, safety."""
        return describe_capabilities()

    @app.get("/api/health")
    def health(request: Request):
        """Version, license, whether setup is still needed, and the current job."""
        snap = jobs.snapshot()
        current = request.app.state.cfg
        return {
            "ok": True,
            "version": __version__,
            "license": "AGPL-3.0-or-later",
            "source": SOURCE_URL,
            "needs_setup": current is None,
            "job": {k: snap[k] for k in ("state", "command", "error") if k in snap},
        }

    @app.get("/api/setup/defaults")
    def setup_defaults(request: Request):
        cwd = Path.cwd()
        found = None
        try:
            found = find_default_config(cwd)
        except ConfigError:
            found = None
        current = request.app.state.cfg
        return {
            "cwd": str(cwd),
            "default_config_path": str((found or (cwd / "library.jsonc")).resolve()),
            "layouts": list(LAYOUT_NAMES),
            "needs_setup": current is None,
            "hint": (
                "Save library.jsonc in this program folder (or any folder that is "
                "not a source and not the destination). Then add source folders "
                "and an output folder. Paths are checked before anything is copied."
            ),
        }

    @app.post("/api/setup/check")
    async def setup_check(request: Request):
        body = await _json_body(request)
        cfg = setup_from_form(body, save=False)
        return {"ok": True, "config": cfg.as_public_dict()}

    @app.post("/api/setup")
    async def setup_save(request: Request):
        body = await _json_body(request)
        cfg = setup_from_form(body, save=True)
        request.app.state.cfg = cfg
        return {"ok": True, "config": cfg.as_public_dict()}

    @app.post("/api/setup/load")
    async def setup_load(request: Request):
        body = await _json_body(request)
        path = body.get("path")
        if not path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        cfg = load_config(path)
        request.app.state.cfg = cfg
        return {"ok": True, "config": cfg.as_public_dict()}

    @app.post("/api/paths")
    async def api_paths(request: Request):
        """Update destination, quarantine, and/or sources; rewrite library.jsonc."""
        current = _require_cfg(request)
        body = await _json_body(request)
        raw = current.as_save_dict()
        raw["config_path"] = str(current.config_path)
        if body.get("library_root"):
            raw["library_root"] = body["library_root"]
        if body.get("quarantine"):
            raw["quarantine"] = body["quarantine"]
        if body.get("sources"):
            raw["sources"] = body["sources"]
        cfg = setup_from_form(raw, save=True)
        request.app.state.cfg = cfg
        return {"ok": True, "config": cfg.as_public_dict()}

    @app.get("/api/config")
    def api_config(request: Request):
        current = request.app.state.cfg
        if current is None:
            return {"needs_setup": True, "sources": [], "layout_names": list(LAYOUT_NAMES)}
        data = current.as_public_dict()
        data["needs_setup"] = False
        return data

    @app.get("/api/status")
    def api_status():
        return jobs.snapshot()

    @app.post("/api/stop")
    def api_stop(request: Request):
        """Stop the engine process. Prefer this over Ctrl+C in the terminal."""
        server = getattr(request.app.state, "server", None)
        if server is not None:
            server.should_exit = True
        return {"ok": True, "stopping": True}

    def _start(command: str, fn):
        def wrapped():
            fn(jobs.progress)

        try:
            return jobs.start(command, wrapped)
        except JobBusy as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.post("/api/scan")
    def api_scan(request: Request):
        current = _require_cfg(request)

        def fn(progress):
            run_scan(current, progress=progress, deep=False)
            write_reports(current)

        return _start("scan", fn)

    @app.post("/api/deep-scan")
    def api_deep_scan(request: Request):
        current = _require_cfg(request)

        def fn(progress):
            run_deep_scan(current, progress=progress)
            write_reports(current)

        return _start("deep-scan", fn)

    @app.get("/api/overview")
    def api_overview(request: Request):
        return overview(_require_cfg(request))

    @app.get("/api/browse")
    def api_browse(request: Request, kind: str = "dest", source_id: int | None = None, rel: str = ""):
        try:
            return browse(_require_cfg(request), kind=kind, source_id=source_id, rel=rel)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/zip/{file_id}")
    def api_zip(request: Request, file_id: int):
        try:
            return zip_contents(_require_cfg(request), file_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/compare")
    def api_compare(request: Request, sources: str = ""):
        ids = [int(x) for x in sources.split(",") if x.strip().isdigit()] if sources else None
        return compare_grid(_require_cfg(request), ids)

    @app.post("/api/report")
    def api_report(request: Request):
        current = _require_cfg(request)

        def fn(progress):
            data = write_reports(current)
            progress("report", {"files": data.get("files"), "path": data.get("summary_path")})

        return _start("report", fn)

    @app.post("/api/plan")
    def api_plan(request: Request):
        current = _require_cfg(request)

        def fn(progress):
            run_plan(current, progress=progress)

        return _start("plan", fn)

    @app.post("/api/apply")
    async def api_apply(request: Request):
        current = _require_cfg(request)
        body = await _json_body(request)
        if not (isinstance(body, dict) and body.get("confirm") is True):
            return JSONResponse(
                {"error": "apply requires JSON {\"confirm\": true} after reviewing the plan"},
                status_code=400,
            )

        def fn(progress):
            run_apply(current, yes=True, progress=progress)

        return _start("apply", fn)

    @app.get("/api/summary")
    def api_summary(request: Request):
        return collect_report(_require_cfg(request))

    @app.get("/api/duplicates")
    def api_duplicates(request: Request):
        data = collect_report(_require_cfg(request))
        return {"groups": data["duplicate_groups"], "wasted_bytes": data["wasted_bytes"]}

    @app.get("/api/plan-rows")
    def api_plan_rows(request: Request):
        return {"rows": list_plan(_require_cfg(request))}

    @app.get("/api/reports/{name}")
    def api_report_file(request: Request, name: str):
        try:
            text = read_report_file(_require_cfg(request), name)
        except ValueError:
            return JSONResponse({"error": "unknown report"}, status_code=404)
        return PlainTextResponse(text)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _require_cfg(request: Request) -> LibraryConfig:
    cfg = request.app.state.cfg
    if cfg is None:
        raise NeedsSetup()
    return cfg


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None
