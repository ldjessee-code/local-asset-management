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

`lam tag` (`lam.tag`) is another opt-in planner. `scan` writes `lam-tags/v1`
and a sidecar. `plan` writes one `file-action-plan/v1` plus a review list.
It does not move files. See [layered-sorting.md](layered-sorting.md).

`lam oversize` (`lam.oversize`) finds images that are too large for a VTT
page, writes one central register (`lam-oversize-register/v1`), and on
`--apply` builds a proxy. A verified zip is the only path to the Recycle
Bin. See [oversize.md](oversize.md).

`lam stage`, `lam place`, and `lam publish` (`lam.stage`) are not part of
scan, report, plan, and apply. A job lives outside Dropbox
(`lam-stage-job/v1`). `lam place` copies finished files into the target and
records them in `lam-place-manifest/v1`. Single-file `lam stage FILE` and
`lam place STAGED DEST` print `lam-place/v1` and recycle the old file only
after the new sha verifies. Kind `map` uses a 100000000 byte
cap. See [staging.md](staging.md).

`lam pack` and `lam unpack` (`lam.archive`) build or extract a zip or
tar.gz. Dry-run is the default. `--apply` never overwrites and never
deletes. See [archive.md](archive.md).

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
| `lam.schemas.registry` | Plan, results, site-profiles, patreon, tag, oversize, and staging schema ids (`supported` / `deprecated` / `removed`) |
| `lam.schema_lite` | stdlib JSON Schema subset for those plans |
| `lam.report` | markdown + JSON |
| `lam.web.app` | FastAPI |
| `lam.capabilities` | inventory for humans and AIs |
| `lam.cli` | `lam` entry |
| `lam.token` | Site session cookies via a persistent browser profile (`lam token`) |
| `lam.patreon` | `lam patreon list`, `sync`, `validate-config` (inbox + manifest only) |
| `lam.tag` | `lam tag scan` / `lam tag plan` layered sorter (writes a plan; does not move files) |
| `lam.oversize` | `lam oversize scan` / `lam oversize list` proxies and one register |
| `lam.stage` | `lam stage`, `lam place`, `lam publish` (copy into Dropbox; not part of the pipeline) |
| `lam.archive` | `lam pack` / `lam unpack` zip and tar.gz (dry-run, manifest, never overwrite) |

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
