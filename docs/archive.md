# Pack and unpack

`lam pack` writes a new `.zip` or `.tar.gz`. `lam unpack` extracts one.
Both commands are dry-run unless you pass `--apply`. They never overwrite
an existing file and they never delete a file.

7z and rar are not implemented. `pyproject.toml` lists optional extras
`archive-7z` and `archive-rar`. `py7zr` is not installed.

## Pack

```text
lam pack SRC_DIR ARCHIVE.zip
lam pack SRC_DIR ARCHIVE.tar.gz --apply
lam pack SRC_DIR ARCHIVE.zip --exclude *.log --exclude skip/* --apply
lam pack SRC_DIR out.tgz --format tar.gz --no-manifest --apply
lam pack SRC_DIR ARCHIVE.zip --json
```

The format is `--format zip` or `--format tar.gz`. When you omit it, the
name decides: `.zip`, `.tar.gz`, or `.tgz`.

A dry run prints the files, sizes, exclude hits, and the target path. The
only write is lam's log.

`--apply` refuses when the archive path already exists. It writes a sibling
temp, then renames that temp onto the archive only after a re-open check.
The check hashes every file member and compares it to `manifest.tsv`. A
failed check leaves the temp in place and does not create the archive.

`manifest.tsv` is at the archive root. Lines are
`relpath<TAB>size<TAB>sha256`, sorted by relative path, UTF-8, and the
manifest does not list itself. `--no-manifest` omits it. A source file
named `manifest.tsv` is reserved while the manifest is on.

`.git` directories are left out. Symlinks and junctions are not followed.
A path under `.git`, `C:\Example`, or a configured `protect_paths` root
(outside the library and quarantine) is refused.

## Unpack

```text
lam unpack ARCHIVE.zip TARGET
lam unpack ARCHIVE.tar.gz TARGET --apply
lam unpack ARCHIVE.zip TARGET --on-conflict backup --apply
lam unpack ARCHIVE.zip TARGET --manifest C:\lists\manifest.tsv --apply
```

Any unsafe member fails the run before a file is written. Unsafe means an
absolute path, a drive letter or colon, a `..` part, a tar symlink,
hardlink, or device member, or a name that lands outside `TARGET`.

| Existing file | Result |
|---|---|
| Same SHA-256 | `unchanged` (left as it is) |
| Different, default | `conflict-skip` |
| Different, `--on-conflict backup` | moved to `TARGET/_prev/<yyyyMMdd_HHmmss>/<relpath>`, then the archive copy is written |
| Not in the archive | `extra` (left in place) |

Extract writes a sibling temp and renames it into place.

The hash list is `--manifest` when you pass it. Otherwise it is
`manifest.tsv` inside the archive, when that member exists. Extracted and
unchanged files must match it. A missing row or a bad hash fails the run
before extract. With no manifest at all, unpack extracts and does not
check hashes. The `manifest.tsv` member itself is not extracted.

## Exit and log

Each run appends a JSONL log through `lam/log.py` and prints one stderr line:

```text
FINAL run=<id> command=pack|unpack ok=<n> skipped=<n> failed=<n> log=<path>
```

`failed=0` and exit 0 is a pass. A failure is a non-zero exit and `failed`
greater than 0. `--json` prints the plan on stdout and leaves that FINAL
line on stderr.
