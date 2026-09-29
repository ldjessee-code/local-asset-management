# Architecture

Local Asset Management is one Python engine with two faces: a CLI (`lam`)
and a LAN web UI (`lam serve`). Both call the same functions.

```text
sources on disk
      │
      ▼
  lam.scan.run_scan     → SQLite index + hash cache
      │
      ▼
  lam.report.write_reports → reports/summary.md, exceptions.md
      │
      ▼
  lam.plan.run_plan     → plan_rows (dest + reason)
      │
      ▼
  lam.apply.run_apply   → copy/hardlink into library_root / quarantine
                          (needs user approval; never deletes in v0.1)
```

`lam actions` is a **separate** opt-in JSON runner (`lam.actions`), not a
stage of this pipeline. It reads a registered plan schema (today
`file-action-plan/v1`; see `lam/schemas/registry.py`), dry-runs by
default, and executes only with `--apply`. Field reference:
[file-action-plan.md](file-action-plan.md).

The browser talks JSON to FastAPI. Jobs run in a background thread on the
machine that can see the disks.

## Package map

| Module | Job |
|---|---|
| `lam.config` | JSONC, path checks, setup form |
| `lam.walk` | `scandir` walk |
| `lam.hashing` | size → 64 KB prefix → SHA-256 |
| `lam.zips` | zip listing, nested zip in memory |
| `lam.scan` | orchestrates walk + zip + hash + dupe groups |
| `lam.layouts` | named dest templates |
| `lam.plan` / `lam.apply` | destinations, then copy |
| `lam.actions` | JSON file-action runner (dry-run / `--apply` / undo) |
| `lam.schemas.registry` | Plan, results, site-profiles, and patreon schema ids (`supported` / `deprecated` / `removed`) |
| `lam.schema_lite` | stdlib JSON Schema subset for those plans |
| `lam.report` | markdown + JSON |
| `lam.web.app` | FastAPI |
| `lam.capabilities` | inventory for humans and AIs |
| `lam.cli` | `lam` entry |
| `lam.token` | Site session cookies via a persistent browser profile (`lam token`) |
| `lam.patreon` | `lam patreon list`, `sync`, `validate-config` (inbox + manifest only) |

Public imports are listed in `lam/__init__.py`. Prefer those over private
helpers (`_log_jsonl`, SQL strings, …).

## Data

SQLite at `index_db`. File rows are rebuilt on each scan. `hash_cache`
(dev, inode, size, mtime → sha256) is kept so the next scan is cheaper.

Paths are stored with `/` even on Windows.

## Web

`create_app(cfg, token)` — `cfg` may be `None` until setup. Token is
required for job APIs. `GET /api/capabilities`, `/docs`, and
`/openapi.json` are public so a client can learn the surface without a
token.
