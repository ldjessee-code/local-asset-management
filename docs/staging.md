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

## Single-file `lam stage FILE` and `lam place STAGED DEST`

```text
lam stage FILE [--root ROOT] [--json]
lam place STAGED DEST (--expect-sha256 HEX | --new) [--dry-run] [--backup-dir DIR] [--root ROOT] [--grant PREFIX] [--json]
```

This form sits beside `stage new` / `stage clean` and beside `place JOBDIR`.
A bare path uses the file form. `new` and `clean` stay the job commands.
`place` with one path is still the job. `place` with two paths is the file.
Pass `--replace` only on the job form. That flag still recycles before the
copy. The file form does not take it.

`--json` on either file command prints one `place/v1` object. `tool` is
`lam` (the same schema allows `soay`). `trash_path` is null. lam does not
return a Recycle Bin location. Without `--json`, stage prints the staged
path and place prints the dest path.

`lam stage FILE` copies the file to `<root>\single\<sha256>\<name>`. It does
not move or delete the source. Status is `staged`, `dest` is null, `sha256`
is the staged file sha, `size` is that file's size, and `message` is
`staged`.

### What `--root` does

On `lam stage` (`FILE`, `new`, and `clean`), `--root` is the staging root.
Omitted, the root is `LAM_STAGING_ROOT` when that environment variable is
set, otherwise `C:\CourtStaging` when the `C:` drive exists, otherwise
`F:\CourtStaging`. That lookup does not create either directory.
`lam stage FILE` creates `<root>\single\<sha256>\` and copies the file
there. The root is refused when it is inside Dropbox (`LAM_DROPBOX_ROOT`,
or `F:\Dropbox`), inside OneDrive (the `OneDrive`, `OneDriveConsumer`, or
`OneDriveCommercial` environment variables, or a path component named
`OneDrive`), under `C:\Example`, or under a `.git` directory. Refusal is
exit 1, status `refused`, and writes nothing.

On `lam place STAGED DEST`, `--root` is optional and does not choose the
destination. Omitted, the staged file is not required to sit under a
staging root. Passed, the root must pass those same staging-root checks,
and the staged file must be that directory or a file under it
(case-insensitive, whole path components). Otherwise exit 1, status
`refused`, message `refused: staged file is outside --root`, or the
staging-root refusal if the root itself is blocked. Place does not create
`--root`.

`--grant` is separate. Dest must be that prefix or a file under it. The
grant path is a write target: refused under `C:\Example` or `.git`. A grant
inside Dropbox is allowed. Dest is always refused under `C:\Example` or
`.git`, even when `--grant` is omitted.

### Place checks and exit codes

`lam place` requires exactly one of `--expect-sha256` and `--new`. Neither
or both is exit 2 and does not print `place/v1`. Argparse errors are exit 2.
Passing the same path for the staged file and dest is exit 2.

`--expect-sha256` must be 64 hex characters. `A-F` is accepted and compared
in lowercase. Any other text is exit 1, status `refused`, message
`refused: bad hex`. A missing staged file is exit 1, status `refused`,
message `refused: missing staged file`. Those two are not usage errors.

| Case | Result |
|---|---|
| `--new` and dest missing | Copy, verify, no backup, no recycle. Exit 0. Message `placed` |
| `--new` and dest exists, including with `--dry-run` | Exit 1. Status `conflict`. Message `conflict: dest exists`. Dest untouched |
| `--expect-sha256` matches | Backup, copy, verify, then recycle the old file. Exit 0. Message `placed` |
| `--expect-sha256` does not match the dest hash | Exit 1. Status `sha-mismatch`. Message `sha-mismatch: dest hash does not match`. No backup |
| Dest missing with `--expect-sha256` | Exit 1. Status `refused`. Message `refused: dest does not exist` |
| `--dry-run` after the check would pass | Exit 0. Status `dry-run`. Message `dry-run`. Writes nothing |
| Blocked path (`--root`, `--grant`, `C:\Example`, `.git`) | Exit 1. Status `refused`. Message `refused: <reason>` |

Exit 1 does not promise that dest is unchanged. A failed recycle keeps the
new dest. Callers must hash dest again after any exit 1.

`sha256` is null on `refused`, `conflict`, and `sha-mismatch`. On `dry-run`
and `staged` it is the staged file sha. On `placed` it is the final dest
sha. `old_sha256` is the dest file's current hash on `sha-mismatch`, on a
replace dry-run (the expected sha matches an existing dest), and on a
successful replace. `size` is the size of the staged or placed file. On
`dry-run`, `conflict`, and `sha-mismatch` it is the staged file size. It is
null when the staged file is missing or the hex is bad.

### Backup path

The backup is a copy that is kept. The name is not stamped with a time.

The default folder is the dest file's parent. `--backup-dir` uses that
folder instead and creates it when the place writes a backup.
`--backup-dir` is refused under `C:\Example` or `.git`.

The file name is `<dest.name>.<oldsha>.bak`. `<oldsha>` is the lowercase
SHA-256 of the current dest bytes. If that file already exists, the name
is `<dest.name>.<oldsha>.2.bak`, then `.3.bak`, through `.999.bak`. The
first free name is used. An existing backup is not overwritten. If none of
those names is free, the place stops with exit 1 before it renames dest.

Dry-run, conflict, and sha-mismatch do not write a backup (`backup_path`
is null).

### Write order when the old sha matches

1. Copy the old dest to the backup path above. The backup sha must match. The backup is kept.
2. Rename the old dest to `<dest>.~place.old` so those bytes still exist as a file. If that hold path already exists, exit 1, status `refused`, and write nothing.
3. Copy the staged file to dest through `<dest>.~place.tmp` and `os.replace`.
4. SHA-256 the dest. It must equal the staged sha.
5. Only then send `<dest>.~place.old` through `lam.actions.recycle_path` (send2trash, or on Windows `SHFileOperationW` with the undo flag). `trash_path` stays null.

If step 4 fails, dest is restored from `<dest>.~place.old` and nothing is recycled. The backup from step 1 is left in place. Status `failed`, message `failed: <detail>`. If step 5 fails, the new dest stays, the backup stays, and `<dest>.~place.old` stays. Exit 1. The JSON `sha256` is the new dest sha.

`place/v1` fields: `schema`, `tool`, `status`, `dest`, `staged`, `sha256`, `old_sha256`, `backup_path`, `trash_path`, `size`, `message`.

Statuses: `placed`, `dry-run`, `staged`, `conflict`, `sha-mismatch`, `refused`, `failed`.

Messages: `placed`; `dry-run`; `staged`; `conflict: dest exists`; `sha-mismatch: dest hash does not match`; `refused: <reason>`; `failed: <detail>`.

Exit codes for this form: 0 ok, 1 for refused, conflict, sha-mismatch, or failed, 2 for usage only.

Schema file: `lam/schemas/place.v1.schema.json`. The job manifest stays `lam-place-manifest/v1`.

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
