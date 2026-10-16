# Court staging (`lam stage`, `lam place`, `lam publish`)

Finished files are prepared outside Dropbox, then copied in. These commands
are not part of `scan → report → plan → apply`. No config file is required.
Nothing here permanently deletes a file.

## `lam stage new`

```text
lam stage new --owner OWNER --job JOB --target TARGET [--root ROOT] [--plan PATH] [--kind KIND] [--byte-cap N]
```

Creates `<root>\<owner>\<job>_yyyyMMdd_hhmm\{in,work,out,logs}` and
`job.json`. A second job in the same minute gets `_2`, `_3`, and so on.

Root resolution:

1. `--root` if you passed it.
2. Else `LAM_STAGING_ROOT`.
3. Else `C:\CourtStaging` when that drive exists.
4. Else `F:\CourtStaging`.

The root is refused when it is inside Dropbox (`LAM_DROPBOX_ROOT` or
`F:\Dropbox`), inside OneDrive (`OneDrive`, `OneDriveConsumer`,
`OneDriveCommercial`, or a folder named `OneDrive`), or under `C:\Example`.
`F:\DropboxOld` is not Dropbox. The target may be inside Dropbox. That is
the folder `lam place` copies into.

`owner` and `job` are one path segment each.

`job.json` is `lam-stage-job/v1` (see `lam/schemas/registry.py`):

| Field | Meaning |
|---|---|
| owner, job, target, plan | Who, which job, copy destination, optional plan path (`plan` may be null) |
| status | `new`, `placed`, `failed`, or `unplaced` |
| created, placed, cleaned | Timestamps. `placed` and `cleaned` are null until those steps |
| kind | `generic`, `game-book`, `fiction`, `review`, `image`, or `map` |
| byte_cap | File-size cap in bytes, or null. Kind `map` stores `100000000` unless you pass `--byte-cap` |
| stale | Set by `lam stage clean --apply` for old failed or unplaced jobs |
| job_dir, staging_root | Filled in by `stage new` |

## `lam place`

```text
lam place JOBDIR [--dry-run] [--strict] [--on-conflict stop|suffix] [--replace] [--jobs-dir DIR] [--kind KIND] [--byte-cap N]
```

`out\` mirrors the target tree. Place copies. It does not move. `--dry-run`
writes nothing: not the target, not `manifest.json`, not `work\`, not
`job.json`, and it does not ring the doorbell.

If `manifest.json` is missing, a real place builds `lam-place-manifest/v1`
from `out\` (after any stand-in and zip are written under `work\`). If it
exists, every `out\` file must be an entry `src` or an entry `original`, and
every entry's size and sha256 must match. A mismatch exits 2. Nothing is
written and the doorbell does not ring.

Each entry has `src`, `dest`, `sha256` (lowercase hex), `size`, `role`
(`file`, `stand-in`, or `original-zip`), and `original` (path or null).

Copy step for every file that is not a duplicate:

1. Write `<dest>.~place.tmp` in the destination folder.
2. SHA-256 the temp.
3. `os.replace` onto `dest`. `PermissionError` or Windows error 5 retries
   up to 6 attempts, sleeping 1, 2, 4, 8, then 16 seconds. The sleep is
   injectable in tests.
4. SHA-256 `dest`.
5. Append `logs\undo.csv` with the same header as `lam actions`:
   `action,source,destination,size,sha256,timestamp`.

Conflicts, compared before anything is written in the target:

| Dest | Result |
|---|---|
| Missing | Copy |
| Same sha256 | Skip, log `dup`, leave the bytes |
| Different hash, default | Copy nothing, status `unplaced`, list the paths, exit 1 |
| `--on-conflict=suffix` | `<stem>_v2`, then `_v3`, … |
| `--replace` | Recycle the old file, then copy. Do not pass this together with `suffix` |

`--strict` turns any name warning into the same unplaced exit 1, with
nothing written in the target.

Exit codes: 0 placed (including all duplicates) or a clean dry-run; 1
conflict, strict lint, or a failed copy; 2 bad arguments, a refused path,
a bad schema, or a manifest mismatch; 3 the byte cap needs pyvips and it
is not installed. Exit 2 does not ring. Exit 1 rings FAIL. Success rings
PASS. A ring that fails is logged (`doorbell failed`) and does not undo a
verified copy.

The ring is `pwsh -NoProfile -File court-run.ps1 -Name <job> -Requester <owner> -- pwsh -NoProfile -Command "exit 0"` (or `exit 1`). The script defaults to `C:\Users\DougJ\Documents\GitHub\court-local-stack\scripts\court-run.ps1` and can be overridden with `LAM_COURT_RUN`. Place does not pass `-JobsRoot`. Tests never start that script.

After a real run, small text (`.json`, `.csv`, `.md`, `.txt`, `.log`, at
most 1 MiB) from `job.json`, `manifest.json`, and `logs\` is copied to
`--jobs-dir`, or to `LAM_PLACE_JOBS_DIR\<job>`, or to
`F:\Dropbox\TheCourt\research\_jobs\<job>`.

### Names

Warnings only, unless `--strict`. Universal: spaces, `<>:"/\|?*`, control
characters, trailing dots or spaces, reserved device names (`CON`, `PRN`,
`AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`).

| kind | Pattern |
|---|---|
| generic | Universal rules only |
| game-book | `System_Edition_Title(_Subtitle).pdf`, or `Fan` as the second token |
| fiction | `Author_Year_Title(_Series_NN)(_coauth_Name)` with extension pdf, epub, mobi, azw, azw3, djvu, or txt |
| review | `notBot_hint_yyyyMMdd[_hhmm].ext` |
| image, map | Original names stay. A resized copy ends in `_8k` or `_web`, not `_4k` or `_max8000` |

Reading an edition out of a PDF is out of scope.

### Map byte cap

Kind `map` defaults to `100000000` decimal bytes (not mebibytes). A larger
image gets a stand-in at or under the cap, placed at the original relative
path, and a zip of the original at
`<target>\_Originals_Zipped\<same folders>\<name>.zip`. Both rows are in
the manifest (`stand-in` and `original-zip`). The file in `out\` stays
until `lam stage clean`. Non-images over the cap fail the place.

This reuses `write_standin_under_bytes` and `write_verified_zip` in
`lam/oversize`. `lam oversize scan` is unchanged: it is still "bytes or
pixels", and its proxy is still an 8000 px long side.

## `lam publish`

```text
lam publish FILE DEST [--dry-run] [--on-conflict stop|suffix] [--replace] [--undo-log PATH]
```

One file, same temp, retry, and verify. No manifest and no doorbell. The
undo CSV defaults to `<dest>.publish-undo.csv` and is written only when a
real copy happens.

## `lam stage clean`

```text
lam stage clean [--root ROOT] [--apply]
```

Dry-run is the default and writes nothing. `--apply`:

- `placed` and `placed` at least 14 days ago: send `in`, `work`, `out`, and
  `logs` to the Recycle Bin. Keep `job.json` and `manifest.json`. Set
  `cleaned`. `work` is included so stand-ins do not sit forever.
- `failed` or `unplaced` and `created` at least 30 days ago: set `stale` to
  true. Do not delete them.
- `new` jobs are not deleted and not marked stale.

Recycle uses `lam.actions.recycle_path`. If the Recycle Bin is unavailable,
the tree is left in place.
