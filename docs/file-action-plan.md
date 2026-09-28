# JSON file-action format

`lam actions` is a separate opt-in runner. It is not part of
`scan → report → plan → apply`. A planner writes a JSON plan. The runner
validates it, dry-runs every pre-check by default, and executes only with
`--apply`.

Nothing is overwritten. Nothing is permanently deleted. `recycle` sends a
path to the Windows Recycle Bin. The only removals outside the Recycle Bin
are temp files the runner itself created during a copy, and `os.rmdir` of an
empty directory when undoing a `mkdir`.

Schema files:

- Plan: `lam/schemas/file-action-plan.v1.schema.json`
- Results: `lam/schemas/file-action-results.v1.schema.json`

The schema registry is `lam/schemas/registry.py`. Supported and deprecated
ids are also listed by `lam capabilities` and `lam capabilities --json`.

## Versioning

A new plan or results version is a new schema file plus a registry entry.
The entry's status is one of:

| Status | Behavior |
|---|---|
| `supported` | Validate and run. No warning. |
| `deprecated` | Validate and run. Print a warning. Record that warning in the results JSON. The registry entry carries a removal note (which release removes it). |
| `removed` | Do not run. Same failure as an unknown id. |

An old version stays readable while it is `supported`. It is then marked
`deprecated`, and removed in a later release (status `removed`). The results
schema is versioned the same way.

Dispatch uses the plan's `schema` string. Unknown or removed ids fail before
any item runs:

```text
unsupported plan schema file-action-plan/v9; supported: file-action-plan/v1
```

If any id is deprecated, the message also lists it (`; deprecated: ...`).
The process exits with code 2. No results file is written.

Today the registry has one supported plan id, `file-action-plan/v1`, and one
supported results id, `file-action-results/v1`. None are deprecated or removed.

The runner always writes the current supported results schema. That is
`file-action-results/v1`. A deprecated plan id is recorded in `plan_schema`;
it does not change the results schema id.

### Why results stayed v1

`plan_schema` and `warnings` are optional. Result files written before they
existed still validate. No field was removed or redefined, and neither new
field is required. The results schema id stays `file-action-results/v1`.

A consumer that saved the pre-1.1 schema text and rejects unknown properties
needs the updated schema file, not a new id. A breaking results change
(removed field, renamed status, new required field) would be a new schema
file, `file-action-results/v2`, and a registry entry.

Undo replays have no plan id. Their results use `plan_schema: null` and
`warnings: []`.

## Plan document

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes | Schema id. Today `file-action-plan/v1`. |
| `actions` | yes | Array of actions, run in order. May be empty. |
| `created` | no | When the plan was written (string). |
| `created_by` | no | Who or what wrote it. |
| `note` | no | Free text. Ignored by the runner. |

Paths in `src` and `dst` must be absolute. On Windows a path with no drive
letter is not absolute. Relative paths make the whole plan invalid (exit 2).

## Actions

Each action has `op` and the fields that op requires. Unknown properties are
rejected. Optional on every op: `id` (copied into results) and `note`
(ignored).

| Op | Required fields | What it does |
|---|---|---|
| `move` | `src`, `dst` | Move a file or directory onto `dst`. |
| `copy` | `src`, `dst` | Copy a file onto `dst`. Directories are not supported in v1. |
| `recycle` | `src` | Send a file or directory to the Recycle Bin. |
| `mkdir` | `dst` | Create a directory, including missing parents. |

`sha256` is optional on `move`, `copy`, and `recycle`. It is a 64-character
hex SHA-256. For a file, the runner hashes the bytes and refuses the item
when they differ (`sha256 mismatch`). A directory with `sha256` set is
refused (`sha256 not applicable to a directory`).

### Rules for every op

- Absolute paths only.
- Never overwrite. If `dst` already exists, the item fails (`destination exists`) and the existing bytes stay.
- Any path with a `.git` component, any case, is refused (`refused: path under .git`).
- Any `src` or `dst` that is `C:\Example` or under it is refused (`refused: example path`). The check is case-insensitive, on the normalized absolute path, and uses the whole path component. `C:\Examples` and `C:\ExampleFoo` are not matches. A `\\?\` prefix is stripped first, so an extended path is the same path. This applies to dry-run, `--apply`, and undo.
- `src` and `dst` must not be the same path (`source and destination are the same path`).
- `dst` must not be inside `src` (`destination is inside source`).
- The parent of `dst` must already exist, or an earlier `mkdir` in the same plan must have created it. The dry-run simulates earlier items so the order matches `--apply`.
- A missing `src` fails (`source does not exist`). The runner does not create it.
- `--stop-on-error` marks every later item `skipped` (`stopped after earlier error`).

### `move`

Same-volume files and directories are renamed. The destination is never
replaced.

A cross-volume **directory** move fails (`cross-volume directory move not supported in v1`). Nothing is copied and nothing is removed.

A cross-volume **file** move copies to a temp name in the destination
folder, checks SHA-256, renames the temp onto `dst`, checks SHA-256 again,
then sends `src` to the Recycle Bin with the same recycle function the
`recycle` op uses. There is no `os.remove` of the source.

If that recycle is unavailable or fails, the item fails. The verified copy
stays at `dst`. The source stays where it was. The reason is:

```text
verified copy exists at the destination and the source was left in place: <detail>
```

`<detail>` is `no recycle bin available`, `recycle did not remove source`, or
the recycle error text. Both files are left in place so no data is lost.

That half-finished item still gets an undo-log row: action `COPY`, source
the original path, destination the verified copy. Undo of a `COPY` row
recycles the destination, so undo can remove the extra copy and leave the
original source alone. The item's own status stays `failed`.

### `copy`

Files only, opened so the destination cannot already exist. After the copy
the runner checks SHA-256. A failed copy deletes only the destination file
this run created.

### `recycle`

Recycle Bin only. If no recycle implementation is available, the item fails
(`no recycle bin available`) and the path stays. There is no permanent-delete
fallback.

### `mkdir`

Creates `dst` and any missing parents. If `dst` is already a directory, the
item is `ok` / `already present` and is not written to the undo log. If `dst`
is a file, the item fails (`destination is a file`).

## Results document

Written on every run that gets past plan validation, including a dry-run.
An existing results path is never overwritten.

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes | Results schema id. The runner writes `file-action-results/v1`. |
| `plan_schema` | no | Plan schema id that was run. `null` on an undo replay. |
| `warnings` | no | Strings. Empty when the plan schema is supported. A deprecated plan id adds one warning. |
| `plan` | yes | Absolute path of the plan file, or of the undo CSV for an undo replay. |
| `mode` | yes | `dry-run` or `apply`. |
| `started`, `finished` | yes | Local ISO-8601 timestamps. |
| `summary` | yes | Counts: `total`, `ok`, `would_ok`, `failed`, `would_fail`, `skipped`. |
| `items` | yes | One object per action, in plan order. |

Item fields: `index`, `id` (or null), `op`, `src`, `dst`, `status`
(`ok`, `would_ok`, `failed`, `would_fail`, `skipped`), `reason`, `size`,
`sha256` (lowercase hex, or null).

A deprecation warning is printed before the items as `warning: <text>` and
stored in `warnings` without the `warning:` prefix. The text looks like
`plan schema <id> is deprecated; <removal note>`.

## Undo log

`--apply` writes a CSV next to the results file (or at `--undo-log`). Every
field is quoted. Header:

```text
action,source,destination,size,sha256,timestamp
```

`action` is uppercase (`MOVE`, `COPY`, `RECYCLE`, `MKDIR`). `sha256` is
uppercase hex. One row is flushed per successful apply item, and also for a
cross-volume move whose copy landed but whose source recycle failed (`COPY`,
as described above). A `mkdir` that was already present is not logged.

`lam actions undo UNDO.csv` replays the log in reverse. Dry-run is the
default. `--apply` executes.

| Logged action | Undo |
|---|---|
| `MOVE` | Move the destination back to the source, with the same checks. |
| `COPY` | Recycle the destination copy. |
| `RECYCLE` | Skipped. Reason: `restore manually from the Recycle Bin`. |
| `MKDIR` | Remove the directory only if it is empty (`os.rmdir`). A directory that is not empty is skipped. |

Example paths and `.git` paths are refused on undo as well. Undo does not
write a second undo log. If undoing a move is itself a cross-volume move and
the source recycle fails, both files stay and that undo item fails with the
same "verified copy exists... source was left in place" reason.

## Exit codes

| Code | When |
|---|---|
| 0 | Every item is `ok`, `would_ok`, or `skipped`. |
| 1 | Any item is `failed` or `would_fail`. |
| 2 | The plan or undo log is invalid, the schema id is unknown or removed, the plan file is missing, or the results path already exists. Printed to stderr. |

## Example plan

This is `examples/file-action-plan.sample.json`. Every path is under
`C:\Example`, so a dry-run or an `--apply` reports `refused: example path`
for each item and does not create or move anything.

```json
{
  "schema": "file-action-plan/v1",
  "created": "2026-09-28T13:26:00-04:00",
  "created_by": "example",
  "note": "Documentation only. Every path is under C:\\Example, which the runner refuses (refused: example path) on dry-run, --apply, and undo.",
  "actions": [
    {
      "id": "mkdir-inbox",
      "op": "mkdir",
      "dst": "C:\\Example\\Library\\Inbox"
    },
    {
      "id": "move-csv",
      "op": "move",
      "src": "C:\\Example\\Inbox\\route_table.csv",
      "dst": "C:\\Example\\Library\\Inbox\\route_table.csv",
      "sha256": "edab39699751c5b21265402ab7f2bbc879821c4f2a9db283064e4ede23d4a346"
    },
    {
      "id": "copy-readme",
      "op": "copy",
      "src": "C:\\Example\\Inbox\\README.txt",
      "dst": "C:\\Example\\Library\\Inbox\\README.txt"
    },
    {
      "id": "recycle-scratch",
      "op": "recycle",
      "src": "C:\\Example\\Inbox\\scratch.tmp"
    }
  ]
}
```

Sample results for that dry-run: `examples/file-action-results.sample.json`.

```powershell
.venv\Scripts\python.exe -m lam actions run examples\file-action-plan.sample.json
.venv\Scripts\python.exe -m lam actions run plan.json --apply --results results.json --undo-log undo.csv
.venv\Scripts\python.exe -m lam actions undo undo.csv
.venv\Scripts\python.exe -m lam actions undo undo.csv --apply
```
