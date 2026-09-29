# Layered sorting (`lam tag`)

`lam tag` sorts a folder in layers. One pass assigns broad bins and writes
`lam-tags/v1`. A later pass goes one level deeper inside a single bin, using
a sub-bin list you supply (`lam-subbins/v1`). Every pass also writes a
reviewable `file-action-plan/v1` and a `lam-tag-review/v1` list. Nothing in
this command moves, renames, recycles, or deletes a file. Moving happens
only if you later run `lam actions run PLAN.json --apply` yourself.

The fixed level-1 bins are `_Maps`, `_GameMods`, `_Installers`,
`_Documents`, `_ModelWeights`, `_Images`, `_Archives`, and `_Unknown`.
Prompts, rules, and labels do not name games, vendors, or creators.

## Commands

```text
lam tag scan FOLDER --out tags.json [--no-model] [--model NAME] [--model-url URL] [--xmp]
    [--level 1|2] [--bin BIN] [--subbins FILE]
    [--burst-window SECONDS] [--max-image-bytes N] [--weight-min-bytes N]

lam tag plan TAGS.json --dest ROOT --level 1 --out plan.json
lam tag plan TAGS.json --dest ROOT --level 2 --bin _Maps --subbins subbins.json --out plan.json
lam actions run plan.json
```

`scan` and `plan` do not take `--apply`. The plan level must match the
tags file. Level 2 does not reclassify on its own: run `scan --level 2`
on the folder that already holds that bin, then `plan --level 2`.

```text
lam tag scan D:\library\_Maps --level 2 --bin _Maps --subbins examples\subbins.sample.json --no-model --out tags-maps.json
lam tag plan tags-maps.json --dest D:\library --level 2 --bin _Maps --subbins examples\subbins.sample.json --out plan-maps.json
```

Level 1 moves land in `<dest>\<bin>\<filename>`. Level 2 moves land in
`<dest>\<bin>\<subbin>\<filename>`. Only the file name is kept, so two
different files with the same name are a destination collision and both
stay on the review list.

## Cascade and confidence

Rules run first (extension, name tokens, magic bytes, size):

| Signal | Bin | Confidence |
|---|---|---|
| `.pdf` `.docx` `.epub` `.txt` `.md` `.csv` `.rtf` `.odt` `.xlsx` `.pptx` | `_Documents` | high |
| `.safetensors` `.gguf` `.ckpt` | `_ModelWeights` | high |
| `.pt` or `.bin` at or above `--weight-min-bytes` (default 50 MiB) | `_ModelWeights` | high |
| smaller `.pt` or `.bin` | `_Unknown` | unsure |
| `.exe` or `.msi` with an `MZ` header (`.msi` also by extension) | `_Installers` | high |
| `.esp` `.esm` `.esl` | `_GameMods` | high |
| zip / 7z / rar / gzip magic, or an archive extension that is not a document container | `_Archives` | high |
| name contains `battlemap`, `grid-map`, or `dungeon-map` and looks like an image | `_Maps` | low |
| PNG, JPEG, GIF, WebP, BMP, TIFF magic or extension | `_Images` | low |
| anything else | `_Unknown` | unsure |

Image rules are low on purpose. They stay provisional until a model agrees.

The model runs only for images, or when the rule is `unsure`, and only
when you did not pass `--no-model`. The default model is `qwen3.5:9b`.
Any model name containing `qwen3.8` is refused. The URL must be localhost
(default `http://127.0.0.1:11434`). The chat request sets `think` to
false, `options.temperature` to 0, and a JSON schema whose `bin` enum is
the allowed labels plus `Unsure`.

Image bytes are sent only for a JPEG, PNG, or WebP at or under
`--max-image-bytes` (default 8 MiB). Larger images and other image types
are not sent. There is no thumbnail step without Pillow, which is not
installed. The rule result is kept and the file note says so.

Confidence when the model is consulted:

- **high** if the first answer matches a rule that was not `unsure`
  (agreement), or if a second call with a **reworded** prompt returns the
  same label. An identical prompt at temperature 0 would not be a second
  opinion, so the confirm prompt is different.
- **unsure** if the answer is `Unsure`.
- **low** otherwise (the two answers disagree, or a single non-matching
  answer is all that came back). The stored bin stays the rule's bin.

Each model call is tried at most three times (one try plus two retries).
If the server cannot be reached, the run warns once, sets `model` to
`unavailable`, and finishes on rules only.

## Batches and copies

Each file gets one batch key. Precedence:

1. **Zip pair** (`zip:<folder>/<stem>`). The archive, a file in the same
   folder with the same stem, and files under a sibling directory of that
   stem.
2. **Download burst** (`burst:<folder>:<n>`). In one directory, files whose
   consecutive mtimes fall within `--burst-window` seconds (default 300,
   inclusive). A burst needs two files.
3. **Parent folder** (`dir:<folder>`). Everything in that directory not
   claimed above shares one batch, even if the mtimes are far apart.

A batch is moved only when every member is high-confidence for the same
bin (level 2: the same sub-bin). Otherwise the whole batch is listed in
the review file and left in place.

Exact copies share a `copy_group` when SHA-256 and size match (`cg-` plus
the first 12 hex digits). The keeper is the shortest absolute path, then
the oldest mtime, then the path text. Other copies become `recycle`
actions only when that keeper is actually moved. If the keeper stays for
review, the extras stay too.

An existing destination, or two moves that would share one destination,
goes to review (`destination exists` or `destination collision in plan`).
Paths with a `.git` part are never actions.

## Level 2 sub-bins

`lam-subbins/v1` is a small file you write. Sample:
`examples/subbins.sample.json` (names `SubA` and `SubB` only).

```json
{
  "schema": "lam-subbins/v1",
  "bin": "_Maps",
  "subbins": [
    {"name": "SubA", "description": "Neutral placeholder group A", "hints": ["alpha"]}
  ]
}
```

`--bin` must be one of the eight level-1 bins and must match `bin` in the
file. Hints are case-insensitive substrings of the **file name** (not the
folder). One matching sub-bin is high confidence (`hint-<name>`). Several
matches, or none, are `unsure` and the model may be asked. The model enum
is the supplied names plus `Unsure`.

At plan time, only files already under `<dest>\<bin>\` are eligible.

## Sidecar fields

Each scan writes `<out-stem>.meta.json` next to `--out`
(`lam-tag-sidecar/v1`).

| Field | Meaning |
|---|---|
| `dc:subject` | Bin, then sub-bin when there is one |
| `xmp:Label` | The level-1 bin |
| `lam:confidence` | `high`, `low`, or `unsure` |
| `xmp:Rating` | 5 high, 2 low, 0 unsure |
| `lam:rule` | Rule id that fired |
| `lam:model` | `disabled`, `unavailable`, `<model>=<label>`, or `<model>:not-called` |
| `lam:batch` | Batch key |
| `lam:copy_group` | Copy-group id, or null |
| `xmp:MetadataDate` | When this scan wrote the metadata (not the file mtime) |
| `file` | Absolute path of the source file |

`--xmp` asks ExifTool, if it is on PATH, to write `.xmp` files under
`<out-stem>.xmp\`. The arguments are a list. `-o` points at that folder.
Source files are not modified. If ExifTool is missing, the command warns
and skips. It is not on this machine's PATH. Pillow is also not installed,
so there is no thumbnail fallback.

## Review list and exit codes

Low-confidence, `Unsure`, held batches, collisions, and `.git` paths are
not actions. They are written to `<plan-stem>.review.json`
(`lam-tag-review/v1`) because `file-action-plan/v1` has no review array.
That schema is unchanged.

JSON is written with a temp file in the same directory, then `os.rename`.
An existing different file is refused. Identical bytes are left alone.

| Code | When |
|---|---|
| 0 | Tags or plan written. A missing model is a warning and still exit 0 (rules only). |
| 2 | Bad arguments, unknown or removed schema id, refusing to overwrite, model name refused, folder missing. |

`lam tag` does not need `library.jsonc`.
