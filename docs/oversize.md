# Oversize image proxies (`lam oversize`)

Some map images are too big for a virtual tabletop page (about 100 MB on
Roll20 Pro). `lam oversize` finds those images, classifies combined
overviews against the separate part maps in the same set, and can write a
smaller proxy. The original is sent to the Recycle Bin only when it is a
combined map with a full part set, and only after a zip of that original
has been checked.

pyvips is imported only when this command runs. The rest of lam works
without it. Install the image extra when you want the command:

```text
pip install -e ".[images]"
```

On Windows that pulls `pyvips` and `pyvips-binary`. If the import fails,
the command exits 3 and names `pip install pyvips-binary pyvips`.

## Commands

```text
lam oversize scan FOLDER [--min-mb 100] [--min-mp 89] [--max-dim 8000]
                         [--set-depth 1] [--register PATH]
                         [--record] [--apply]
                         [--zip {combined-with-parts,combined,all,none}]
                         [--proxy-suffix _max8000] [--quality 90] [--json]

lam oversize list [--register PATH] [--csv OUT.csv]
                  [--status planned|done|error] [--json]
```

`scan` with no write flags is a dry run. It walks the folder, reads
width and height from image headers, hashes each oversize file (SHA-256),
classifies it, and prints what `--apply` would do. It does not create the
register and it does not write a proxy, a zip, or anything else.

`--record` creates or updates the register and does no image work.
`--apply` implies `--record`: it writes proxies and, when the zip mode
says so, zips and recycles.

`list` only reads the register. `--csv` refuses to overwrite an existing
file.

No config file is required.

## What counts as oversize

An image is oversize when either test is true:

- file size in bytes is greater than `--min-mb` times 1024 times 1024
  (default 100), or
- `width * height` is greater than `--min-mp` times 1,000,000 (default 89).

Extensions: `.jpg` `.jpeg` `.png` `.webp` `.tif` `.tiff`.

Skipped: directories named `.git`, symlinks, junctions, `.zip` files,
files whose stem already ends with the proxy suffix, and any path the
register already lists as a proxy.

## The set

`--set-depth N` (default 1) picks the set folder: the ancestor N levels
below the folder you scanned. A file that sits directly in that folder
uses the scanned folder as its set. Part maps are searched everywhere
inside the set, including sibling folders.

## Register

One JSON file for the whole library. There is no per-image sidecar and no
per-folder file.

| Source | Path |
|---|---|
| `--register PATH` | that path |
| else `LAM_OVERSIZE_REGISTER` | that path |
| else | `%LOCALAPPDATA%\lam\oversize-register.json` |

Schema id: `lam-oversize-register/v1`
(`lam/schemas/lam-oversize-register.v1.schema.json`).

Writes are temp-file plus rename. The previous document is kept as
`<register>.prev` (one copy). A half-written register is not left in place.

One entry per original file, keyed by SHA-256. A moved file with the same
hash updates `original_path` and appends the new path to `paths_seen`.

| Field | Meaning |
|---|---|
| `original_path` | Current path of the original |
| `sha256` | Full-file SHA-256 |
| `bytes`, `mb` | File size |
| `width`, `height`, `megapixels` | Header pixels |
| `combined` | Name says combined, or at least one part was found |
| `combined_evidence` | Which rule fired, the part letters, and the area percent |
| `parts_found` | `yes`, `partial`, or `no` |
| `part_paths` | Part files in the same set |
| `parts_area_pct` | Part pixels as a percent of the overview |
| `proxy_path`, `proxy_width`, `proxy_height` | Proxy, once `--apply` has written it |
| `zip_path`, `zip_verified` | Zip of the original, after a successful check |
| `original_recycled` | True only after the Recycle Bin call |
| `flag` | Always `large, revisit for subdivision` |
| `status` | `planned`, `done`, or `error` |
| `error` | Reason when `status` is `error`, otherwise null |
| `first_seen`, `updated` | Local ISO time |
| `paths_seen` | Every path this hash has been seen at |

## Combined and parts

The stem is normalised before compare: lowercase, apostrophes and spaces
removed, repeated `_` collapsed, then split on `_`. These noise tokens are
dropped: `combined`, `variations`, `gridless`, `gridded`, `grid`, `hd`,
`a2`, `pta`, `ptb`, `online`.

Another image in the same set is a **part** when it is a different file,
it has fewer pixels, and its token list equals the overview's token list
after removing exactly one extra token. That extra token is a single
letter or digit (`a`, `f`, `1`) or `pt` plus digits (`pt1`).

`combined` is true when the file or folder path contains `combined`
(any case), or when at least one part was found.

`parts_found`:

- `no` when there are no parts
- `yes` when the part pixels add up to at least 80% of the overview, or
  the distinct part letters or digits (deduplicated) are a contiguous run
  starting at `a` or `1` with two or more values
- `partial` otherwise

A2-print parts are often lower resolution than the HD overview, so a
complete `a…` or `1, 2, …` run counts as `yes` even when the area is under
80%. `f` and `g` alone do not, because the run does not start at `a`.

## `--apply`

The proxy is `pyvips.Image.thumbnail(src, max_dim, height=max_dim, size="down")`.
When the source's long side is larger than `--max-dim`, the proxy's long
side is `--max-dim` and the aspect ratio stays within one pixel. A source
that is already smaller is copied at its own size (never upscaled).

The proxy is written next to the original as `<stem><proxy-suffix><ext>`.
`.jpg` and `.jpeg` stay JPEG at `--quality` (default 90). `.png` stays PNG.
`.tif`, `.tiff`, and `.webp` are written as `.jpg`.

An existing file is never overwritten. If the preferred proxy or zip name
is already taken, and it is not this file's registered proxy or zip, the
command uses `<name>_2`, then `_3`, and so on.

Zip (default `--zip combined-with-parts`):

| Mode | What gets zipped |
|---|---|
| `combined-with-parts` | `combined` and `parts_found` is `yes` |
| `combined` | every combined file, including `partial` |
| `all` | every oversize file |
| `none` | nothing |

The zip is `<original filename>.zip` beside the original, `ZIP_STORED`,
one member named as the original file. JPEG pixels do not compress, so
store avoids a useless pass. The command reopens the zip, runs `testzip()`,
streams the member, and compares its SHA-256 to the hash from the scan.
Only a match calls the Recycle Bin (`lam.actions.recycle_path`, or
`send2trash` when that package is installed). There is no hard delete.

On a mismatch or any other error the original stays put, that entry is
`status=error` with the reason, and the command continues with the next
file. If any file errored during `--apply`, the process exits 4.

A later `--apply` skips a `done` entry whose proxy still exists and, when
a zip was required, whose verified zip still exists and whose original was
already recycled.

`--zip none`, a `partial` set, and a map with no parts keep the original
loose. The proxy is still written. That is the right default for a map
Doug may still cut into submaps, and for a combined image whose parts do
not cover the sheet (Aztec Zone2 has only F and G; Citadel has only A).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Finished. Dry run, `--record`, and a clean `--apply` all use 0. |
| 2 | Bad arguments, or the register is missing, unreadable, or the wrong schema. |
| 3 | pyvips could not be imported. |
| 4 | `--apply` hit an error on one or more files. The others were still processed. |

## Worked example

Dry run, then apply, using a register outside the map tree. This does not
run by itself; you pass `--apply` when the dry-run table looks right.

```text
set LAM_OVERSIZE_REGISTER=%LOCALAPPDATA%\lam\oversize-register.json
lam oversize scan D:\maps\Party_of_Two
lam oversize scan D:\maps\Party_of_Two --apply
lam oversize list --status done
lam oversize list --csv D:\maps\oversize.csv
```

A combined file with parts A–D prints `parts=yes` and, on `--apply`, gains
a `*_max8000.jpg` proxy, a stored zip, and a Recycle Bin entry for the
loose original. The part files are not moved. A single huge map with no
lettered parts prints `parts=no` and keeps its original next to the proxy.

## Safety

- Dry run is the default. Image files change only with `--apply`.
- The original is recycled only after the zip's SHA-256 matches.
- Recycle is the Windows Recycle Bin (recoverable), never a permanent delete.
- Existing proxies and zips are not overwritten.
- `.git`, symlinks, and junctions are not walked.
