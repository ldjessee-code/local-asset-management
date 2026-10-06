# Oversize image proxies (`lam oversize`)

Some map images are too big for a virtual tabletop page (about 100 MB on
Roll20 Pro). `lam oversize` finds those images, classifies combined
overviews against the separate part maps, and can write a smaller proxy.
The default search is the whole scanned folder. A combined map is complete
when the located parts cover it. The original is sent to the Recycle Bin
only for a complete combined map, and only after a zip of that original
has been checked. An incomplete set can be filled by cropping the missing
cells from the combined original, and only when each cell's place and name
are confident.

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
                         [--set-depth 1] [--part-scope {pack,set}]
                         [--min-score 0.90] [--min-margin 0.05]
                         [--min-coverage 0.97]
                         [--preview-dir DIR] [--preview-standins]
                         [--plan-out PATH.csv]
                         [--register PATH]
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
Doug may still cut into submaps, and for a combined image whose located
parts do not cover the sheet.

## Part search and location

`--part-scope pack` (the default) name-matches parts anywhere under the
scanned folder. `--part-scope set` keeps the phase-1 rule: only the set
folder (`--set-depth`), and it does not run pixel location. Two files
with the same marker collapse to one. The keeper is the file with the
most pixels, then the shortest path. The others are recorded as
`duplicate_candidates`.

Pack scope then locates each same-variant candidate inside the combined
image (`lam/oversize/locate.py`).

`r` is candidate pixels divided by combined pixels, on one axis. Parts of
one map share one `r`. The search tries a geometric grid from 0.25 to 4
on a greyscale thumbnail whose long side is 192 px, then multiplies the
best step by 0.96–1.04. The other parts are tried at that scale on a
320 px thumbnail. A part that misses the score or the margin at the
shared scale is searched across the grid again. The stored `scale` stays
the anchor. That part's box uses the scale that actually matched, so a
print-resolution sibling can still sit on an HD combined map.

The score is `spcor` (normalised correlation). On this libvips the peak
is the centre of the template. pyvips 3.2 `maxpos()` returns
`(value, x, y)`. The top-left of the part is the peak minus half the
template. A full-resolution patch of at most 160 px then refines the
origin. Tests require that origin within 2 px of the true crop, and `r`
within 1%.

The margin is the best score minus the best score outside a
template-sized neighbourhood. `draw_rect` does not clear a float image,
so that neighbourhood is replaced with zeros. Defaults, both required:
`--min-score 0.90` and `--min-margin 0.05`. A miss is listed in
`rejected_candidates` with `below min-score` or `below min-margin`. A
template whose grey standard deviation is under 2 is below min-score and
is not correlated. A name match that does not locate is not a part.

Positions found for one variant can seed the other variants of the same
family (every name token except the last). Each seeded part still has to
pass `--min-score` at that place.

Coverage is the area of the union of the located rectangles divided by
the combined area. An edge strip that no part covers, and whose grey
thumbnail standard deviation is under 2, is padding. `padding_px` is that
area, and it is left out of the denominator. `parts_found` is `yes` when
coverage is at least `--min-coverage` (default 0.97).

Otherwise the located rectangles have to share one size (within 2%) and
sit on a lattice. Column and row origins are clustered with a tolerance
of `max(2 px, 4% of the cell)`. Overlap is allowed. A missing cell inside
the sheet gets a marker only when exactly one reading order fits every
located part: row-major or column-major, letters `a`–`z` or digits 1–99,
with one offset. Two orders are not confident even when they would spell
the same markers. The map stays `partial`, nothing is recreated, and
`confidence` is `not confident:` plus the reason.

Before a recreate, the sibling folder and an analogous per-letter folder
are checked for an image whose name lacks the marker. One that locates
on that cell is recorded as `misnamed_parts` and is not recreated.

## Recreate a missing part

`--apply` recreates only when `confidence` is `confident`, the zip mode
is not `none`, and coverage is still under `--min-coverage`. The order
for that map is: write the missing parts, verify them, write the 8K
overview, zip the original, check the SHA-256, then recycle the original.
A partial map that is not confident gets only the stand-in.

The crop is taken from the full-resolution original (`access=random`)
and resized by `1/r`, so the new file matches its siblings within 1 px.
The file is JPEG at `--quality` (default 90) unless every sibling is
PNG. The name is the nearest sibling in reading order, with that
sibling's marker token replaced and the rest kept (prefix, case,
separators, variant, suffix). The folder is the sibling's folder. When
the siblings use one folder per letter, the command creates the matching
folder and nothing else. An existing target is not overwritten: the map
is flagged `not confident: target exists`, `--apply` writes nothing for
it, and the process exits 4.

After the write, the new file is located again. The score at that cell
must be at least 0.98. A miss keeps the new file (there is no delete),
still writes the stand-in, and does not zip the original.

## Previews and the plan CSV

A dry run still does not change the scanned tree and does not write the
register unless you pass `--record`.

`--preview-dir DIR` is allowed on a dry run. The directory must not be
inside the scanned folder; that is exit 2, before the walk. For each
combined map it writes an overview JPEG (long side at most 1600) with
located parts outlined in green and missing cells outlined in red, plus
a preview JPEG (long side at most 1024) of each cell that would be
recreated. An existing preview name is kept and a `_2` suffix is used.
Marker text is drawn when the text renderer is available. A failure to
draw a label skips the label. `--preview-standins` also writes one
unboxed preview for a map that is not combined. That flag is off by
default.

`--plan-out PATH.csv` is allowed on a dry run and never overwrites an
existing file. One row per oversize file: path, size in MB, pixel size,
whether it is combined, `parts_found`, confidence, action, recreate
markers, recreate target paths and sizes, stand-in pixel size, estimated
stand-in MB, a note that the stand-in size is an estimate from the pixel
ratio, the zip path, and the expected zip MB. The zip is `ZIP_STORED`,
so the expected size is the original size. The text table and `--json`
use the same action strings: `stand-in only`, `overview + zip`, and
`recreate N parts + overview + zip`.

## Register fields

`lam-oversize-register/v1` grew these optional fields. A phase-1
register still loads. A new write always includes them.

| Field | Meaning |
|---|---|
| `part_scope` | `pack` or `set` |
| `scale` | Shared `r`, or null when location did not run |
| `coverage_pct` | Located area as a percent of the sheet, after padding |
| `padding_px` | Padding area in combined pixels |
| `located_parts` | Path, marker, x, y, w, h in combined pixels, score, margin |
| `rejected_candidates` | Path, reason, score |
| `duplicate_candidates` | Path, marker, and the path that was kept |
| `missing_cells` | Marker, x, y, w, h, target path, target size, preview path |
| `recreated_parts` | Path, marker, sha256, size, source box, verify score, preview path |
| `misnamed_parts` | An existing file that located on a missing cell |
| `confidence` | `confident`, `not confident: <reason>`, or null when location did not run |
| `action` | The same string the table prints |

`part_paths` stays the name-match list after duplicate markers are
collapsed. `parts_area_pct` stays the name-match pixel percent. With
`--part-scope set`, `confidence` is null and `parts_found` is the
phase-1 letter-run or area result.

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

- Dry run is the default. Image files in the scanned tree change only with `--apply`.
- `--preview-dir` and `--plan-out` may write outside that tree on a dry run. A preview directory inside the tree is refused. A plan CSV is never overwritten.
- The original is recycled only after the zip's SHA-256 matches.
- Recycle is the Windows Recycle Bin (recoverable), never a permanent delete.
- Existing proxies, zips, previews, and recreated parts are not overwritten.
- A recreated part that fails the 0.98 check is left on disk. It is not deleted.
- `.git`, symlinks, and junctions are not walked.
