# SPDX-License-Identifier: AGPL-3.0-or-later
"""Locate name-matched part maps inside a combined image and plan missing cells.

Matching uses pyvips only. The combined image is thumbnailed (shrink-on-load)
before normalised correlation (``spcor``). A small full-resolution patch
refines the offset. The full combined image is cropped only when a missing
part is written or a preview is cropped.

``r`` is candidate pixels divided by combined pixels (linear). Parts of one
combined map share one ``r``.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from lam.oversize.images import IMAGE_EXTENSIONS, require_pyvips

# Coarse scale grid from 0.25 to 4 (geometric). Fine search is relative.
_COARSE_SCALES = (0.25, 0.3536, 0.5, 0.7071, 1.0, 1.4142, 2.0, 2.8284, 4.0)
_FINE_FACTORS = (0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02, 1.03, 1.04)
_COARSE_LONG = 192
_MID_LONG = 320
_PATCH = 160
_LOW_DEVIATE = 2.0
# pyvips 3.2 maxpos() is (value, x, y). On libvips 8.18 the spcor peak is the
# centre of the template: a known crop scores 1.0 there and ~0 at its top-left.
_PEAK_IS_CENTER = True


@dataclass
class Candidate:
    path: Path
    marker: str
    width: int
    height: int


@dataclass
class LocatedPart:
    path: str
    marker: str
    x: int
    y: int
    w: int
    h: int
    score: float
    margin: float
    index: int = -1


@dataclass
class Rejected:
    path: str
    reason: str
    score: float | None


@dataclass
class MissingCell:
    marker: str
    x: int
    y: int
    w: int
    h: int
    target_path: str | None = None
    target_width: int | None = None
    target_height: int | None = None
    preview_path: str | None = None
    index: int = -1


@dataclass
class Misnamed:
    path: str
    marker: str
    x: int
    y: int
    w: int
    h: int
    score: float


@dataclass
class RecreatePlan:
    marker: str
    x: int
    y: int
    w: int
    h: int
    target: Path
    width: int
    height: int
    png: bool
    index: int


@dataclass
class Analysis:
    scale: float | None = None
    coverage_pct: float | None = None
    padding_px: int = 0
    padding_note: str = "none"
    located: list[LocatedPart] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    missing: list[MissingCell] = field(default_factory=list)
    misnamed: list[Misnamed] = field(default_factory=list)
    plans: list[RecreatePlan] = field(default_factory=list)
    confidence: str | None = None
    parts_found: str = "no"
    combined: bool = False
    seconds: float = 0.0
    seeds: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


def family_key(tokens: list[str]) -> tuple[str, ...]:
    """Shared prefix of one map family. The last token is the variant."""
    if len(tokens) >= 2:
        return tuple(tokens[:-1])
    return tuple(tokens)


def collapse_markers(
    matched: list[tuple[Candidate, int]],
) -> tuple[list[Candidate], list[dict]]:
    """One candidate per marker: most pixels, then the shortest path.

    ``matched`` items are ``(candidate, pixel_count)``. Duplicates are the
    ones that lost.
    """
    best: dict[str, Candidate] = {}
    pixels: dict[str, int] = {}
    duplicates: list[dict] = []
    ordered = sorted(matched, key=lambda pair: str(pair[0].path).casefold())
    for candidate, count in ordered:
        current = best.get(candidate.marker)
        if current is None:
            best[candidate.marker] = candidate
            pixels[candidate.marker] = count
            continue
        current_len = len(str(current.path))
        new_len = len(str(candidate.path))
        take_new = count > pixels[candidate.marker] or (
            count == pixels[candidate.marker] and new_len < current_len
        )
        if take_new:
            duplicates.append(
                {
                    "path": str(current.path),
                    "marker": current.marker,
                    "kept_path": str(candidate.path),
                }
            )
            best[candidate.marker] = candidate
            pixels[candidate.marker] = count
        else:
            duplicates.append(
                {
                    "path": str(candidate.path),
                    "marker": candidate.marker,
                    "kept_path": str(current.path),
                }
            )
    kept = sorted(best.values(), key=lambda item: str(item.path).casefold())
    return kept, duplicates


def analyze_map(
    combined: Path,
    combined_w: int,
    combined_h: int,
    candidates: list[Candidate],
    *,
    name_combined: bool,
    min_score: float = 0.90,
    min_margin: float = 0.05,
    min_coverage: float = 0.97,
    scale_hint: float | None = None,
    position_seeds: dict[str, tuple[int, int, int, int]] | None = None,
    proxy_suffix: str = "_max8000",
) -> Analysis:
    """Locate *candidates* and, when the lattice is confident, plan missing cells."""
    started = time.perf_counter()
    result = Analysis(combined=name_combined)
    if not candidates:
        result.parts_found = "no"
        result.confidence = None
        result.seconds = time.perf_counter() - started
        return result
    try:
        _locate_all(
            result,
            combined=combined,
            combined_w=combined_w,
            combined_h=combined_h,
            candidates=candidates,
            min_score=min_score,
            min_margin=min_margin,
            scale_hint=scale_hint,
            position_seeds=position_seeds or {},
        )
    except Exception as exc:
        result.confidence = f"not confident: locate failed: {type(exc).__name__}"
        result.parts_found = "partial" if result.located else "no"
        result.combined = name_combined or bool(result.located)
        result.seconds = time.perf_counter() - started
        return result

    _finish_geometry(
        result,
        combined=combined,
        combined_w=combined_w,
        combined_h=combined_h,
        candidates=candidates,
        name_combined=name_combined,
        min_score=min_score,
        min_coverage=min_coverage,
        proxy_suffix=proxy_suffix,
    )
    result.seconds = time.perf_counter() - started
    return result


def _locate_all(
    result: Analysis,
    *,
    combined: Path,
    combined_w: int,
    combined_h: int,
    candidates: list[Candidate],
    min_score: float,
    min_margin: float,
    scale_hint: float | None,
    position_seeds: dict[str, tuple[int, int, int, int]],
) -> None:
    pyvips = require_pyvips()
    _no_cache(pyvips)
    thumb, sx, sy = _grey_file(pyvips, combined, combined_w, combined_h, _COARSE_LONG)
    anchor: _Hit | None = None
    if scale_hint is None:
        for candidate in candidates:
            hit = _search_scale(
                pyvips, thumb, sx, sy, candidate, combined_w, combined_h, min_margin
            )
            if hit is None or hit.score <= 0:
                continue
            if anchor is None or hit.score > anchor.score:
                anchor = hit
            if hit.score >= min_score:
                break
        scale = anchor.scale if anchor is not None else None
    else:
        scale = float(scale_hint)
    if scale is None or anchor is None and scale_hint is None:
        for candidate in candidates:
            result.rejected.append(
                Rejected(str(candidate.path.resolve()), "below min-score", 0.0)
            )
        return
    assert scale is not None
    result.scale = scale
    mid, mx, my = _grey_file(pyvips, combined, combined_w, combined_h, _MID_LONG)
    for candidate in candidates:
        seed = position_seeds.get(candidate.marker)
        hit = _match_at(pyvips, mid, mx, my, candidate, scale, combined_w, combined_h)
        if hit is None or hit.score < min_score or hit.margin < min_margin:
            # One map usually shares r. A part saved at another resolution
            # still has to be found, and the map scale stays the anchor.
            alt = _search_scale(
                pyvips, mid, mx, my, candidate, combined_w, combined_h, min_margin
            )
            if alt is not None and alt.score > 0 and (hit is None or _prefer(alt, hit, min_margin)):
                hit = alt
        if hit is None:
            result.rejected.append(
                Rejected(str(candidate.path.resolve()), "below min-score", 0.0)
            )
            continue
        used_scale = hit.scale if hit.scale else scale
        refined = _refine(
            pyvips,
            combined,
            combined_w,
            combined_h,
            candidate,
            used_scale,
            hit.x,
            hit.y,
        )
        score = hit.score
        x, y = hit.x, hit.y
        if refined is not None:
            refined_x, refined_y, refined_score = refined
            slop = refine_slop(combined_w, combined_h)
            near_guess = abs(refined_x - x) <= slop and abs(refined_y - y) <= slop
            if refined_score >= min_score and near_guess:
                x, y = refined_x, refined_y
                score = max(score, refined_score)
        if seed is not None:
            sx0, sy0, sw, sh = seed
            near = abs(x - sx0) <= max(4, int(0.03 * sw)) and abs(y - sy0) <= max(
                4, int(0.03 * sh)
            )
            if not near:
                local = _score_box(
                    pyvips,
                    combined,
                    combined_w,
                    combined_h,
                    candidate,
                    scale,
                    sx0,
                    sy0,
                    sw,
                    sh,
                )
                if local >= min_score:
                    x, y = sx0, sy0
                    score = local
        used = hit.scale if hit.scale else scale
        width = max(1, int(round(candidate.width / used)))
        height = max(1, int(round(candidate.height / used)))
        reason = None
        if score < min_score:
            reason = "below min-score"
        elif hit.margin < min_margin:
            reason = "below min-margin"
        if reason:
            result.rejected.append(
                Rejected(str(candidate.path.resolve()), reason, round(score, 4))
            )
            continue
        result.located.append(
            LocatedPart(
                path=str(candidate.path.resolve()),
                marker=candidate.marker,
                x=int(x),
                y=int(y),
                w=width,
                h=height,
                score=round(float(score), 4),
                margin=round(float(hit.margin), 4),
            )
        )
    del thumb
    del mid


def _finish_geometry(
    result: Analysis,
    *,
    combined: Path,
    combined_w: int,
    combined_h: int,
    candidates: list[Candidate],
    name_combined: bool,
    min_score: float,
    min_coverage: float,
    proxy_suffix: str,
) -> None:
    by_path = {str(item.path.resolve()): item for item in candidates}
    rects = list(result.located)
    padding_px, padding_note = (0, "none")
    if rects:
        padding_px, padding_note = _padding(combined, combined_w, combined_h, rects)
    result.padding_px = padding_px
    result.padding_note = padding_note
    content = max(1, combined_w * combined_h - padding_px)
    covered = _union_area(_clipped_rects(rects, combined_w, combined_h))
    ratio = min(1.0, covered / content) if rects else 0.0
    result.coverage_pct = round(100.0 * ratio, 3)
    result.combined = name_combined or bool(rects)

    if ratio >= min_coverage and rects:
        result.parts_found = "yes"
    elif rects:
        result.parts_found = "partial"
    else:
        result.parts_found = "no"
        result.confidence = "not confident: no part located"
        result.seeds = {}
        return

    lattice = _fit_lattice(rects, combined_w, combined_h)
    if lattice is None:
        result.confidence = "not confident: " + _lattice_reason
        result.missing = []
        result.seeds = {part.marker: (part.x, part.y, part.w, part.h) for part in rects}
        return

    cells, reason = lattice
    if reason:
        result.confidence = "not confident: " + reason
        result.seeds = {part.marker: (part.x, part.y, part.w, part.h) for part in rects}
        return

    known = {(part.x, part.y) for part in rects}
    # Match located parts to cells by nearest origin.
    used_markers = {part.marker for part in rects}
    missing: list[MissingCell] = []
    for cell in cells:
        if any(abs(part.x - cell.x) <= 2 and abs(part.y - cell.y) <= 2 for part in rects):
            for part in rects:
                if abs(part.x - cell.x) <= 2 and abs(part.y - cell.y) <= 2:
                    part.index = cell.index
            continue
        if cell.marker in used_markers:
            result.confidence = "not confident: predicted marker already used"
            result.seeds = {part.marker: (part.x, part.y, part.w, part.h) for part in rects}
            return
        missing.append(cell)

    misnamed_markers: set[str] = set()
    for cell in list(missing):
        found = _find_misnamed(
            combined=combined,
            combined_w=combined_w,
            combined_h=combined_h,
            cell=cell,
            located=rects,
            by_path=by_path,
            scale=result.scale or 1.0,
            min_score=min_score,
            proxy_suffix=proxy_suffix,
            known_paths={part.path for part in rects},
        )
        if found is None:
            continue
        result.misnamed.append(found)
        misnamed_markers.add(cell.marker)
        missing = [item for item in missing if item.marker != cell.marker]
        covered += cell.w * cell.h
    if misnamed_markers:
        content = max(1, combined_w * combined_h - padding_px)
        ratio = min(
            1.0,
            _union_area(
                _clipped_rects([*rects, *_misnamed_rects(result.misnamed)], combined_w, combined_h)
            )
            / content,
        )
        result.coverage_pct = round(100.0 * ratio, 3)
        if ratio >= min_coverage:
            result.parts_found = "yes"
        elif rects or result.misnamed:
            result.parts_found = "partial"

    result.missing = missing
    result.seeds = {part.marker: (part.x, part.y, part.w, part.h) for part in rects}
    for item in result.misnamed:
        result.seeds[item.marker] = (item.x, item.y, item.w, item.h)

    if missing and ratio < min_coverage:
        blocked = _plan_recreates(result, rects, by_path, missing)
        if blocked:
            result.confidence = "not confident: " + blocked
            result.plans = []
            result.parts_found = "partial"
            return
        result.confidence = "confident"
        result.parts_found = "partial"
        return

    result.missing = missing
    result.plans = []
    result.confidence = "confident"
    if ratio >= min_coverage:
        result.parts_found = "yes"
    _ = known


_lattice_reason = "irregular lattice"


def _misnamed_rects(items: list[Misnamed]) -> list[LocatedPart]:
    return [
        LocatedPart(item.path, item.marker, item.x, item.y, item.w, item.h, item.score, 1.0)
        for item in items
    ]


@dataclass
class _Hit:
    scale: float
    score: float
    margin: float
    x: int
    y: int


def refine_slop(full_w: int, full_h: int) -> int:
    """Pixels a full-resolution refine may move the thumbnail guess.

    One pixel on the 320px search is ``max(side) / 320`` full pixels. The old
    fixed gate of 8px kept that correction on a small fixture and threw it
    away on a sheet whose long side is about 17k (one pixel there is 54px).
    """
    step = int(math.ceil(max(int(full_w), int(full_h)) / float(_MID_LONG)))
    return max(8, step + 2)


def _prefer(hit: _Hit, best: _Hit, min_margin: float) -> bool:
    """A clear peak beats a higher score whose second peak is stuck to it."""
    hit_ok = hit.margin >= min_margin
    best_ok = best.margin >= min_margin
    if hit_ok != best_ok:
        return hit_ok
    return hit.score > best.score


def _search_scale(
    pyvips,
    thumb,
    sx: float,
    sy: float,
    candidate: Candidate,
    full_w: int,
    full_h: int,
    min_margin: float = 0.05,
) -> _Hit | None:
    best: _Hit | None = None
    for scale in _COARSE_SCALES:
        hit = _match_at(pyvips, thumb, sx, sy, candidate, scale, full_w, full_h)
        if hit is not None and (best is None or _prefer(hit, best, min_margin)):
            best = hit
    if best is None:
        return None
    # Fine steps are relative to the coarse winner. Multiplying the updated
    # best walks off the grid (1.4142 * 0.96 * 0.98 landed on 1.33047936).
    coarse_scale = best.scale
    for factor in _FINE_FACTORS:
        hit = _match_at(
            pyvips, thumb, sx, sy, candidate, coarse_scale * factor, full_w, full_h
        )
        if hit is not None and _prefer(hit, best, min_margin):
            best = hit
    return best


def _match_at(pyvips, search, sx: float, sy: float, candidate: Candidate, scale: float, full_w: int, full_h: int) -> _Hit | None:
    if scale <= 0:
        return None
    template = _template(pyvips, candidate, scale, sx, sy)
    if template is None:
        return None
    # spcor needs a template strictly smaller than the search. A left/right
    # half is the full height, so the true scale used to be rejected and the
    # search kept a larger, low-scoring scale. Crop at most 2px, which is
    # rounding or that full-bleed edge, and remember the inset.
    inset_x = 0
    inset_y = 0
    limit_w = search.width - 1
    limit_h = search.height - 1
    extra_w = template.width - limit_w
    extra_h = template.height - limit_h
    if extra_w > 0 or extra_h > 0:
        if extra_w > 2 or extra_h > 2:
            return None
        inset_x = max(0, extra_w) // 2
        inset_y = max(0, extra_h) // 2
        crop_w = template.width - max(0, extra_w)
        crop_h = template.height - max(0, extra_h)
        if crop_w < 8 or crop_h < 8:
            return None
        template = _mem(template.crop(inset_x, inset_y, crop_w, crop_h))
    if template.width >= search.width or template.height >= search.height:
        return None
    if template.width < 8 or template.height < 8:
        return None
    if float(template.deviate()) < _LOW_DEVIATE:
        return _Hit(scale=scale, score=0.0, margin=0.0, x=0, y=0)
    # spcor peak on libvips 8.18 is the template's top-left. fastcor's zero
    # border outscores a real crop, so the search uses normalised correlation.
    surface = _mem(search.spcor(template))
    score, margin, px, py = _peak_and_margin(surface, template.width, template.height)
    if _PEAK_IS_CENTER:
        left = px - template.width // 2
        top = py - template.height // 2
    else:
        left = px
        top = py
    full_x = int(round((left - inset_x) / sx)) if sx else 0
    full_y = int(round((top - inset_y) / sy)) if sy else 0
    del surface
    del template
    return _Hit(scale=scale, score=score, margin=margin, x=full_x, y=full_y)


def _mem(image):
    """Force a private memory image so PNG pipelines are not read twice."""
    return image.copy_memory()


def _no_cache(pyvips) -> None:
    """The operation cache re-reads a PNG from the middle and libvips errors."""
    pyvips.cache_set_max(0)


def _template(pyvips, candidate: Candidate, scale: float, sx: float, sy: float):
    desired_w = int(round(candidate.width / scale * sx))
    desired_h = int(round(candidate.height / scale * sy))
    if desired_w < 8 or desired_h < 8:
        return None
    long_side = max(desired_w, desired_h, 8)
    image = pyvips.Image.thumbnail(
        str(candidate.path),
        min(long_side, max(candidate.width, candidate.height)),
        height=min(long_side, max(candidate.width, candidate.height)),
        size="down",
    )
    image = _as_grey(image)
    if image.width < 1 or image.height < 1:
        return None
    image = image.resize(desired_w / image.width, vscale=desired_h / image.height)
    return _mem(image.cast("float"))


def _grey_file(pyvips, path: Path, full_w: int, full_h: int, long_side: int):
    image = pyvips.Image.thumbnail(str(path), long_side, height=long_side, size="down")
    image = _mem(_as_grey(image).cast("float"))
    sx = image.width / full_w if full_w else 1.0
    sy = image.height / full_h if full_h else 1.0
    return image, sx, sy


def _as_grey(image):
    if image.bands > 1:
        image = image.colourspace("b-w")
    return image


def _peak_and_margin(surface, template_w: int, template_h: int) -> tuple[float, float, int, int]:
    """Best score, margin, and the centre of the template.

    The rival peak is the best score outside a template-sized footprint, so a
    one-pixel shift of the same match does not count as a second location.
    """
    interior = _mem(surface)
    score, local_x, local_y = _maxpos(interior)
    rad_x = max(int(template_w), 4)
    rad_y = max(int(template_h), 4)
    left = max(0, local_x - rad_x // 2)
    top = max(0, local_y - rad_y // 2)
    box_w = min(max(1, interior.width - left), rad_x)
    box_h = min(max(1, interior.height - top), rad_y)
    blank = _zero_box(interior, left, top, box_w, box_h)
    second, _, _ = _maxpos(blank)
    return score, max(0.0, score - second), local_x, local_y


def _zero_box(image, left: int, top: int, width: int, height: int):
    """Zero a rectangle. ``draw_rect`` does not punch a hole in float images."""
    return _fill_box(image, left, top, width, height, 0.0)


def _fill_box(image, left: int, top: int, width: int, height: int, value: float):
    """Replace a rectangle. ``draw_rect`` does not punch a hole in float images."""
    left = int(max(0, min(left, image.width - 1)))
    top = int(max(0, min(top, image.height - 1)))
    width = int(min(max(1, width), image.width - left))
    height = int(min(max(1, height), image.height - top))
    patch = image.new_from_image(float(value)).crop(0, 0, width, height)
    return image.insert(patch, left, top)


def _maxpos(image) -> tuple[float, int, int]:
    """Return ``(value, x, y)``.

    pyvips 3.2 ``maxpos()`` is ``(value, x, y)``, not ``(x, y)``. Reading the
    first two fields treats a perfect score of 1.0 as column 1.
    """
    peaked = image.max(x=True, y=True)
    if isinstance(peaked, (list, tuple)) and len(peaked) >= 2 and isinstance(peaked[1], dict):
        return _finite(peaked[0]), int(peaked[1]["x"]), int(peaked[1]["y"])
    pos = image.maxpos()
    if len(pos) >= 3:
        return _finite(pos[0]), int(pos[1]), int(pos[2])
    return _finite(image.max()), int(pos[0]), int(pos[1])


def _finite(value) -> float:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def _refine(
    pyvips,
    combined: Path,
    full_w: int,
    full_h: int,
    candidate: Candidate,
    scale: float,
    guess_x: int,
    guess_y: int,
) -> tuple[int, int, float] | None:
    """Return full-resolution top-left and score, using a small patch."""
    cell_w = max(1, int(round(candidate.width / scale)))
    cell_h = max(1, int(round(candidate.height / scale)))
    part = pyvips.Image.new_from_file(str(candidate.path), access="random")
    patch_w = min(part.width, _PATCH)
    patch_h = min(part.height, _PATCH)
    if patch_w < 8 or patch_h < 8:
        return None
    origin_x = max(0, (part.width - patch_w) // 2)
    origin_y = max(0, (part.height - patch_h) // 2)
    patch = _mem(_as_grey(part.crop(origin_x, origin_y, patch_w, patch_h)).cast("float"))
    if float(patch.deviate()) < _LOW_DEVIATE:
        return None
    rel_x = origin_x / candidate.width * cell_w
    rel_y = origin_y / candidate.height * cell_h
    rel_w = patch_w / candidate.width * cell_w
    rel_h = patch_h / candidate.height * cell_h
    margin = max(8, int(max(full_w, full_h) / _COARSE_LONG) + 4)
    return _correlate_window(
        pyvips,
        combined,
        full_w,
        full_h,
        patch,
        guess_x + rel_x,
        guess_y + rel_y,
        rel_w,
        rel_h,
        margin,
        guess_x,
        guess_y,
        rel_x,
        rel_y,
    )


def _correlate_window(
    pyvips,
    combined: Path,
    full_w: int,
    full_h: int,
    patch,
    center_x: float,
    center_y: float,
    rel_w: float,
    rel_h: float,
    margin: int,
    guess_x: int,
    guess_y: int,
    rel_x: float,
    rel_y: float,
) -> tuple[int, int, float] | None:
    left = int(math.floor(center_x - margin))
    top = int(math.floor(center_y - margin))
    win_w = int(math.ceil(rel_w + 2 * margin))
    win_h = int(math.ceil(rel_h + 2 * margin))
    left = max(0, min(left, full_w - 2))
    top = max(0, min(top, full_h - 2))
    win_w = min(win_w, full_w - left)
    win_h = min(win_h, full_h - top)
    if win_w < 12 or win_h < 12:
        return None
    source = pyvips.Image.new_from_file(str(combined), access="random")
    window = _mem(_as_grey(source.crop(left, top, win_w, win_h)).cast("float"))
    target_w = max(8, int(round(rel_w)))
    target_h = max(8, int(round(rel_h)))
    if target_w >= window.width or target_h >= window.height:
        return None
    template = _mem(patch.resize(target_w / patch.width, vscale=target_h / patch.height))
    surface = _mem(window.spcor(template))
    score, _margin, px, py = _peak_and_margin(surface, template.width, template.height)
    if _PEAK_IS_CENTER:
        patch_left = px - template.width // 2
        patch_top = py - template.height // 2
    else:
        patch_left = px
        patch_top = py
    found_x = int(round(left + patch_left - rel_x))
    found_y = int(round(top + patch_top - rel_y))
    # If the peak sits on the window edge, the guess was outside the margin.
    edge = px < 2 or py < 2 or px > surface.width - 3 or py > surface.height - 3
    del source
    del surface
    if edge and margin < max(32, int(rel_w)):
        return _correlate_window(
            pyvips,
            combined,
            full_w,
            full_h,
            patch,
            center_x,
            center_y,
            rel_w,
            rel_h,
            margin * 3,
            guess_x,
            guess_y,
            rel_x,
            rel_y,
        )
    return found_x, found_y, score


def _score_box(
    pyvips,
    combined: Path,
    full_w: int,
    full_h: int,
    candidate: Candidate,
    scale: float,
    x: int,
    y: int,
    w: int,
    h: int,
) -> float:
    refined = _refine(pyvips, combined, full_w, full_h, candidate, scale, x, y)
    if refined is None:
        return 0.0
    found_x, found_y, score = refined
    if abs(found_x - x) > max(4, int(0.03 * w)) or abs(found_y - y) > max(4, int(0.03 * h)):
        return 0.0
    return score


def score_file_at(
    combined: Path,
    combined_w: int,
    combined_h: int,
    part: Path,
    part_w: int,
    part_h: int,
    scale: float,
    x: int,
    y: int,
    w: int,
    h: int,
) -> float:
    """Correlation of *part* against the combined image at one cell. 0 on failure."""
    pyvips = require_pyvips()
    candidate = Candidate(part, "", part_w, part_h)
    try:
        return _score_box(pyvips, combined, combined_w, combined_h, candidate, scale, x, y, w, h)
    except Exception:
        return 0.0


def _clipped_rects(rects: list[LocatedPart], width: int, height: int) -> list[LocatedPart]:
    """Drop the part of a box that hangs off the sheet. Coverage uses this.

    A one-pixel thumbnail error can place a box a few dozen pixels outside
    the sheet. Counting that overhang made a shifted grid look fully covered.
    """
    clipped: list[LocatedPart] = []
    for item in rects:
        x1 = max(0, int(item.x))
        y1 = max(0, int(item.y))
        x2 = min(int(width), int(item.x) + int(item.w))
        y2 = min(int(height), int(item.y) + int(item.h))
        if x2 <= x1 or y2 <= y1:
            continue
        clipped.append(
            LocatedPart(
                item.path,
                item.marker,
                x1,
                y1,
                x2 - x1,
                y2 - y1,
                item.score,
                item.margin,
                item.index,
            )
        )
    return clipped


def _union_area(rects: list[LocatedPart]) -> int:
    if not rects:
        return 0
    boxes = [(item.x, item.y, item.w, item.h) for item in rects]
    if not _any_overlap(boxes):
        return sum(max(0, w) * max(0, h) for _x, _y, w, h in boxes)
    pyvips = require_pyvips()
    max_x = max(x + w for x, _y, w, _h in boxes)
    max_y = max(y + h for _x, y, _w, h in boxes)
    longest = max(max_x, max_y, 1)
    scale = 1.0 if longest <= 600 else 600 / longest
    mw = max(1, int(round(max_x * scale)))
    mh = max(1, int(round(max_y * scale)))
    mask = pyvips.Image.black(mw, mh)
    for x, y, w, h in boxes:
        left = min(max(0, int(round(x * scale))), mw - 1)
        top = min(max(0, int(round(y * scale))), mh - 1)
        width = min(max(1, int(round(w * scale))), mw - left)
        height = min(max(1, int(round(h * scale))), mh - top)
        mask = mask.draw_rect([255], left, top, width, height, fill=True)
    count = _finite((mask > 0).avg()) / 255.0 * mw * mh
    return int(round(count / (scale * scale)))


def _any_overlap(boxes: list[tuple[int, int, int, int]]) -> bool:
    for index, (x, y, w, h) in enumerate(boxes):
        for other_x, other_y, other_w, other_h in boxes[index + 1 :]:
            if x < other_x + other_w and other_x < x + w and y < other_y + other_h and other_y < y + h:
                return True
    return False


def _padding(path: Path, full_w: int, full_h: int, rects: list[LocatedPart]) -> tuple[int, str]:
    min_x = min(item.x for item in rects)
    min_y = min(item.y for item in rects)
    max_x = max(item.x + item.w for item in rects)
    max_y = max(item.y + item.h for item in rects)
    pyvips = require_pyvips()
    _no_cache(pyvips)
    thumb = _mem(
        _as_grey(pyvips.Image.thumbnail(str(path), 160, height=160, size="down"))
    ).copy_memory()
    sx = thumb.width / full_w
    sy = thumb.height / full_h
    notes: list[str] = []
    pixels = 0

    def strip_is_flat(left: int, top: int, width: int, height: int) -> bool:
        if width < 1 or height < 1:
            return False
        left = min(max(0, left), thumb.width - 1)
        top = min(max(0, top), thumb.height - 1)
        width = min(width, thumb.width - left)
        height = min(height, thumb.height - top)
        if width < 1 or height < 1:
            return False
        return float(thumb.crop(left, top, width, height).deviate()) < _LOW_DEVIATE

    if min_y >= 2 and strip_is_flat(0, 0, thumb.width, max(1, int(round(min_y * sy)))):
        pixels += min_y * full_w
        notes.append(f"top {min_y}px")
    bottom = full_h - max_y
    if bottom >= 2 and strip_is_flat(0, int(round(max_y * sy)), thumb.width, max(1, thumb.height - int(round(max_y * sy)))):
        pixels += bottom * full_w
        notes.append(f"bottom {bottom}px")
    if min_x >= 2 and strip_is_flat(0, 0, max(1, int(round(min_x * sx))), thumb.height):
        pixels += min_x * full_h
        notes.append(f"left {min_x}px")
    right = full_w - max_x
    if right >= 2 and strip_is_flat(int(round(max_x * sx)), 0, max(1, thumb.width - int(round(max_x * sx))), thumb.height):
        pixels += right * full_h
        notes.append(f"right {right}px")
    # Corners were counted twice. Subtract the overlap rectangles.
    if min_x >= 2 and min_y >= 2 and "left" in " ".join(notes) and "top" in " ".join(notes):
        pixels -= min_x * min_y
    if right >= 2 and min_y >= 2 and any(note.startswith("right") for note in notes) and any(note.startswith("top") for note in notes):
        pixels -= right * min_y
    if min_x >= 2 and bottom >= 2 and any(note.startswith("left") for note in notes) and any(note.startswith("bottom") for note in notes):
        pixels -= min_x * bottom
    if right >= 2 and bottom >= 2 and any(note.startswith("right") for note in notes) and any(note.startswith("bottom") for note in notes):
        pixels -= right * bottom
    pixels = max(0, pixels)
    if not notes:
        return 0, "none"
    return pixels, ", ".join(notes)


def _fit_lattice(
    rects: list[LocatedPart], width: int, height: int
) -> tuple[list[MissingCell], str] | None:
    """Return ``(cells, reason)``. ``reason`` empty means the order is unique.

    ``None`` means the rectangles do not sit on one lattice.
    """
    global _lattice_reason
    if len({(item.w, item.h) for item in rects}) > 1:
        widths = [item.w for item in rects]
        heights = [item.h for item in rects]
        if _spread(widths) > 0.02 or _spread(heights) > 0.02:
            _lattice_reason = "cell sizes disagree"
            return None
    median_w = _median([item.w for item in rects])
    median_h = _median([item.h for item in rects])
    if _spread([item.w for item in rects]) > 0.02 or _spread([item.h for item in rects]) > 0.02:
        _lattice_reason = "cell sizes disagree"
        return None
    tol_x = max(2.0, 0.04 * median_w)
    tol_y = max(2.0, 0.04 * median_h)
    columns = _cluster([item.x for item in rects], tol_x)
    rows = _cluster([item.y for item in rects], tol_y)
    if columns is None or rows is None:
        _lattice_reason = "irregular lattice"
        return None
    step_x = _step(columns, tol_x)
    step_y = _step(rows, tol_y)
    if step_x == "irregular" or step_y == "irregular":
        _lattice_reason = "irregular lattice"
        return None
    if isinstance(step_x, float):
        columns = _extend(columns, step_x, median_w, width)
    if isinstance(step_y, float):
        rows = _extend(rows, step_y, median_h, height)
    placed: dict[tuple[int, int], LocatedPart] = {}
    for item in rects:
        col = _nearest(item.x, columns, tol_x)
        row = _nearest(item.y, rows, tol_y)
        if col is None or row is None:
            _lattice_reason = "part is off the lattice"
            return None
        placed[(col, row)] = item
    n_cols = len(columns)
    n_rows = len(rows)
    cells_in = []
    for (col, row), item in placed.items():
        cells_in.append((col, row, item.marker))
    fit = _unique_order(cells_in, n_cols, n_rows)
    if fit is None:
        return [], "letter order is ambiguous"
    out: list[MissingCell] = []
    for row_index, origin_y in enumerate(rows):
        for col_index, origin_x in enumerate(columns):
            marker = fit[(col_index, row_index)]
            out.append(
                MissingCell(
                    marker=marker,
                    x=int(round(origin_x)),
                    y=int(round(origin_y)),
                    w=int(round(median_w)),
                    h=int(round(median_h)),
                    index=_marker_index(marker),
                )
            )
    for (col, row), item in placed.items():
        item.index = _marker_index(item.marker)
    return out, ""


def _marker_index(marker: str) -> int:
    """Reading-order key. Letters are a=0. Digits keep their integer value."""
    if len(marker) == 1 and marker.isalpha():
        return ord(marker.lower()) - ord("a")
    if marker.isdigit():
        return int(marker)
    return -1


def _spread(values: list[int]) -> float:
    if not values:
        return 0.0
    mid = _median(values)
    if mid <= 0:
        return 0.0
    return max(abs(value - mid) / mid for value in values)


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _cluster(values: list[int], tol: float) -> list[float] | None:
    ordered = sorted(values)
    groups: list[list[int]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - (sum(groups[-1]) / len(groups[-1])) <= tol:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _step(centers: list[float], tol: float) -> float | None | str:
    if len(centers) < 2:
        return None
    gaps = [centers[index + 1] - centers[index] for index in range(len(centers) - 1)]
    mid = _median([int(round(gap)) for gap in gaps])
    if any(abs(gap - mid) > max(tol, 0.04 * mid) for gap in gaps):
        return "irregular"
    return float(mid)


def _extend(centers: list[float], step: float, size: float, limit: int) -> list[float]:
    if step <= 1:
        return centers
    out = list(centers)
    guard = 0
    while out[0] - step >= -1 and guard < 30:
        nxt = out[0] - step
        if nxt + size < 0:
            break
        if nxt < -1:
            break
        out.insert(0, nxt)
        guard += 1
    guard = 0
    while out[-1] + step + size <= limit + 1 and guard < 30:
        out.append(out[-1] + step)
        guard += 1
    return out


def _nearest(value: int, centers: list[float], tol: float) -> int | None:
    best = None
    best_dist = None
    for index, center in enumerate(centers):
        dist = abs(value - center)
        if best_dist is None or dist < best_dist:
            best = index
            best_dist = dist
    if best is None or best_dist is None or best_dist > tol:
        return None
    return best


def _unique_order(
    cells: list[tuple[int, int, str]], n_cols: int, n_rows: int
) -> dict[tuple[int, int], str] | None:
    markers = [marker for _c, _r, marker in cells]
    letters = all(len(item) == 1 and item.isalpha() for item in markers)
    digits = all(item.isdigit() for item in markers)
    fits: list[dict[tuple[int, int], str]] = []
    if letters:
        for order in ("row", "col"):
            fit = _try_order(cells, n_cols, n_rows, order, kind="letter")
            if fit is not None:
                fits.append(fit)
    if digits:
        for order in ("row", "col"):
            fit = _try_order(cells, n_cols, n_rows, order, kind="digit")
            if fit is not None:
                fits.append(fit)
    unique: list[dict[tuple[int, int], str]] = []
    for fit in fits:
        if all(fit == other or fit != other for other in unique):
            if fit not in unique:
                unique.append(fit)
    if len(fits) == 1:
        return fits[0]
    # A full row or column: both orders spell the same markers and every cell
    # is already filled, so there is nothing to recreate. A hole stays
    # ambiguous even when the two orders would spell the same missing names.
    if len(fits) > 1 and all(item == fits[0] for item in fits) and len(cells) == n_cols * n_rows:
        return fits[0]
    return None


def _try_order(
    cells: list[tuple[int, int, str]],
    n_cols: int,
    n_rows: int,
    order: str,
    *,
    kind: str,
) -> dict[tuple[int, int], str] | None:
    offsets: list[int] = []
    for col, row, marker in cells:
        index = row * n_cols + col if order == "row" else col * n_rows + row
        value = ord(marker) - ord("a") if kind == "letter" else int(marker)
        offsets.append(value - index)
    if len(set(offsets)) != 1:
        return None
    offset = offsets[0]
    predicted: dict[tuple[int, int], str] = {}
    for row in range(n_rows):
        for col in range(n_cols):
            index = row * n_cols + col if order == "row" else col * n_rows + row
            value = index + offset
            if kind == "letter":
                if not 0 <= value <= 25:
                    return None
                marker = chr(ord("a") + value)
            else:
                if not 1 <= value <= 99:
                    return None
                marker = str(value)
            predicted[(col, row)] = marker
    for col, row, marker in cells:
        if predicted[(col, row)] != marker:
            return None
    return predicted


def _plan_recreates(
    result: Analysis,
    located: list[LocatedPart],
    by_path: dict[str, Candidate],
    missing: list[MissingCell],
) -> str | None:
    """Fill ``result.plans``. Return a reason when the names cannot be planned."""
    if not located:
        return "no sibling to name the new part"
    plans: list[RecreatePlan] = []
    for cell in missing:
        sibling = _nearest_sibling(located, cell)
        if sibling is None:
            return "no sibling to name the new part"
        source = by_path.get(sibling.path)
        if source is None:
            return "sibling file is missing"
        try:
            target = _retarget(Path(sibling.path), sibling.marker, cell.marker)
        except ValueError as exc:
            return str(exc)
        if target.exists():
            return "target exists"
        png = Path(sibling.path).suffix.lower() == ".png" and all(
            Path(part.path).suffix.lower() == ".png" for part in located
        )
        if not png:
            target = target.with_suffix(".jpg")
        width = source.width
        height = source.height
        # Prefer the median sibling size. One sibling is enough.
        cell.target_path = str(target)
        cell.target_width = width
        cell.target_height = height
        plans.append(
            RecreatePlan(
                marker=cell.marker,
                x=cell.x,
                y=cell.y,
                w=cell.w,
                h=cell.h,
                target=target,
                width=width,
                height=height,
                png=png,
                index=cell.index,
            )
        )
    result.plans = plans
    result.missing = missing
    return None


def _nearest_sibling(located: list[LocatedPart], cell: MissingCell) -> LocatedPart | None:
    best = None
    best_dist = None
    for part in located:
        if part.index < 0:
            dist = abs(part.x - cell.x) + abs(part.y - cell.y)
        else:
            dist = abs(part.index - cell.index)
        if best is None or dist < best_dist:
            best = part
            best_dist = dist
    return best


def _token_is_marker(token: str, marker: str) -> bool:
    if len(marker) == 1 and marker.isalpha() and token.lower() == marker.lower():
        return True
    if len(token) == 1 and token.lower() == marker.lower():
        return True
    import re

    match = re.fullmatch(r"pt(\d+)", token, flags=re.IGNORECASE)
    return bool(match and match.group(1) == marker)


def _format_like(token: str, new_marker: str) -> str:
    import re

    match = re.fullmatch(r"(pt)(\d+)", token, flags=re.IGNORECASE)
    if match:
        return match.group(1) + new_marker
    if token.isupper():
        return new_marker.upper()
    if token.islower():
        return new_marker.lower()
    return new_marker


def _replace_token(text: str, old_marker: str, new_marker: str) -> tuple[str, bool]:
    tokens = text.split("_")
    changed = False
    out: list[str] = []
    for token in tokens:
        if _token_is_marker(token, old_marker):
            out.append(_format_like(token, new_marker))
            changed = True
        else:
            out.append(token)
    return "_".join(out), changed


def _retarget(sibling: Path, old_marker: str, new_marker: str) -> Path:
    new_stem, changed = _replace_token(sibling.stem, old_marker, new_marker)
    if not changed:
        raise ValueError("marker token not in sibling name")
    parts: list[str] = []
    for component in sibling.parent.parts:
        replaced, did = _replace_token(component, old_marker, new_marker)
        parts.append(replaced if did else component)
    return Path(*parts) / f"{new_stem}{sibling.suffix}"


def _find_misnamed(
    *,
    combined: Path,
    combined_w: int,
    combined_h: int,
    cell: MissingCell,
    located: list[LocatedPart],
    by_path: dict[str, Candidate],
    scale: float,
    min_score: float,
    proxy_suffix: str,
    known_paths: set[str],
) -> Misnamed | None:
    folders: list[Path] = []
    for part in located:
        source = Path(part.path)
        retargeted = _retarget_dir(source.parent, part.marker, cell.marker)
        if retargeted is not None:
            folders.append(retargeted)
        folders.append(source.parent)
    seen: set[str] = set()
    pyvips = require_pyvips()
    for folder in folders:
        key = os.path.normcase(str(folder))
        if key in seen or not folder.is_dir():
            continue
        seen.add(key)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            path = Path(entry.path)
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if path.stem.lower().endswith(proxy_suffix.lower()):
                continue
            resolved = str(path.resolve())
            if resolved in known_paths or resolved == str(combined.resolve()):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            try:
                image = pyvips.Image.new_from_file(str(path), access="sequential")
                width, height = int(image.width), int(image.height)
                del image
            except Exception:
                continue
            if width * height >= combined_w * combined_h:
                continue
            score = score_file_at(
                combined,
                combined_w,
                combined_h,
                path,
                width,
                height,
                scale,
                cell.x,
                cell.y,
                cell.w,
                cell.h,
            )
            if score >= min_score:
                return Misnamed(
                    path=resolved,
                    marker=cell.marker,
                    x=cell.x,
                    y=cell.y,
                    w=cell.w,
                    h=cell.h,
                    score=round(score, 4),
                )
    return None


def _retarget_dir(directory: Path, old_marker: str, new_marker: str) -> Path | None:
    parts: list[str] = []
    changed = False
    for component in directory.parts:
        replaced, did = _replace_token(component, old_marker, new_marker)
        parts.append(replaced if did else component)
        changed = changed or did
    if not changed:
        return None
    return Path(*parts)


def write_recreated_part(
    combined: Path,
    plan: RecreatePlan,
    *,
    quality: int,
) -> None:
    """Crop the cell at full resolution and resize to the sibling pixel size."""
    pyvips = require_pyvips()
    if plan.target.exists():
        raise FileExistsError(str(plan.target))
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    tmp = plan.target.with_name(f".{plan.target.name}.{uuid.uuid4().hex}.tmp")
    source = pyvips.Image.new_from_file(str(combined), access="random")
    try:
        left = max(0, plan.x)
        top = max(0, plan.y)
        width = min(plan.w, source.width - left)
        height = min(plan.h, source.height - top)
        tile = source.crop(left, top, width, height)
        if tile.width != plan.width or tile.height != plan.height:
            tile = tile.resize(plan.width / tile.width, vscale=plan.height / tile.height)
        if plan.png:
            tile.pngsave(str(tmp))
        else:
            if tile.bands == 1:
                tile = tile.bandjoin([tile, tile])
            elif tile.bands > 3:
                tile = tile.extract_band(0, n=3)
            tile.jpegsave(str(tmp), Q=int(quality))
        del tile
        if plan.target.exists():
            raise FileExistsError(str(plan.target))
        os.replace(tmp, plan.target)
    except Exception:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        raise
    finally:
        del source


def write_previews(
    *,
    preview_dir: Path,
    combined: Path,
    combined_w: int,
    combined_h: int,
    stem: str,
    located: list[LocatedPart],
    missing: list[MissingCell],
    standin: bool,
    standin_only: bool = False,
) -> dict[str, str]:
    """Write overview and per-cell previews. Return marker → preview path.

    The overview long side is at most 1600 px. Each cell preview is at most
    1024 px. Existing files are not overwritten; a free ``_2`` name is used.
    ``standin_only`` writes one unboxed preview and nothing else.
    """
    pyvips = require_pyvips()
    preview_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    safe = _safe_stem(stem)
    if standin_only:
        stand = pyvips.Image.thumbnail(str(combined), 1600, height=1600, size="down")
        dest = _free_name(preview_dir / f"{safe}_standin.jpg")
        if stand.bands == 1:
            stand = stand.bandjoin([stand, stand])
        elif stand.bands > 3:
            stand = stand.extract_band(0, n=3)
        stand.jpegsave(str(dest), Q=80)
        paths["standin"] = str(dest.resolve())
        return paths
    overview_path = _free_name(preview_dir / f"{safe}_overview.jpg")
    image = pyvips.Image.thumbnail(str(combined), 1600, height=1600, size="down")
    if image.bands == 1:
        image = image.bandjoin([image, image])
    elif image.bands > 3:
        image = image.extract_band(0, n=3)
    sx = image.width / combined_w
    sy = image.height / combined_h
    for part in located:
        image = _stroke(
            image,
            int(round(part.x * sx)),
            int(round(part.y * sy)),
            max(2, int(round(part.w * sx))),
            max(2, int(round(part.h * sy))),
            [0, 180, 0],
        )
        image = _try_label(image, part.marker, int(round(part.x * sx)) + 4, int(round(part.y * sy)) + 4)
    for cell in missing:
        image = _stroke(
            image,
            int(round(cell.x * sx)),
            int(round(cell.y * sy)),
            max(2, int(round(cell.w * sx))),
            max(2, int(round(cell.h * sy))),
            [220, 0, 0],
        )
        image = _try_label(image, cell.marker, int(round(cell.x * sx)) + 4, int(round(cell.y * sy)) + 4)
    if image.bands == 4:
        image = image.flatten(background=[255, 255, 255])
    if max(image.width, image.height) > 1600:
        image = image.thumbnail_image(1600, height=1600, size="down")
    image.jpegsave(str(overview_path), Q=80)
    paths["overview"] = str(overview_path.resolve())
    source = None
    for cell in missing:
        if source is None:
            source = pyvips.Image.new_from_file(str(combined), access="random")
        left = max(0, min(cell.x, source.width - 1))
        top = max(0, min(cell.y, source.height - 1))
        width = max(1, min(cell.w, source.width - left))
        height = max(1, min(cell.h, source.height - top))
        tile = source.crop(left, top, width, height)
        if max(tile.width, tile.height) > 1024:
            tile = tile.thumbnail_image(1024, height=1024, size="down")
        dest = _free_name(preview_dir / f"{safe}_recreate_{cell.marker}.jpg")
        if tile.bands == 1:
            tile = tile.bandjoin([tile, tile])
        tile.jpegsave(str(dest), Q=80)
        cell.preview_path = str(dest.resolve())
        paths[cell.marker] = cell.preview_path
        del tile
    if standin and not missing and not located:
        stand = pyvips.Image.thumbnail(str(combined), 1600, height=1600, size="down")
        dest = _free_name(preview_dir / f"{safe}_standin.jpg")
        if stand.bands == 1:
            stand = stand.bandjoin([stand, stand])
        stand.jpegsave(str(dest), Q=80)
        paths["standin"] = str(dest.resolve())
    return paths


def _stroke(image, left: int, top: int, width: int, height: int, color: list[int]):
    left = int(max(0, min(left, image.width - 1)))
    top = int(max(0, min(top, image.height - 1)))
    width = int(min(width, image.width - left))
    height = int(min(height, image.height - top))
    if width < 2 or height < 2:
        return image
    ink = [int(channel) for channel in color]
    if image.bands == 1:
        ink = [ink[0]]
    elif image.bands == 4 and len(ink) == 3:
        # A text label bandjoins an alpha plane. Later strokes must match.
        ink = [*ink, 255]
    elif len(ink) != image.bands:
        ink = (ink + [255, 255, 255, 255])[: image.bands]
    thickness = 2 if min(width, height) > 8 else 1
    # libvips draw_rect feeds the ink to linear, which rejects a 3-band vector.
    image = _paint(image, left, top, width, thickness, ink)
    image = _paint(image, left, top + height - thickness, width, thickness, ink)
    image = _paint(image, left, top, thickness, height, ink)
    image = _paint(image, left + width - thickness, top, thickness, height, ink)
    return image


def _paint(image, left: int, top: int, width: int, height: int, ink: list[int]):
    """Stamp a solid rectangle. ``insert`` keeps RGB inks that ``draw_rect`` rejects."""
    if width < 1 or height < 1:
        return image
    patch = image.new_from_image(ink).crop(0, 0, int(width), int(height))
    return image.insert(patch, int(left), int(top))


def _try_label(image, text: str, left: int, top: int):
    try:
        pyvips = require_pyvips()
        label = pyvips.Image.text(str(text), dpi=72)
    except Exception:
        return image
    try:
        if label.bands == 1:
            ink = label.new_from_image([255, 255, 0])
            ink = ink.bandjoin(label)
        else:
            return image
        # Keep the preview small; skip a label that would run past the edge.
        if left + ink.width >= image.width or top + ink.height >= image.height:
            return image
        if image.bands == 3:
            image = image.bandjoin(255)
        return image.composite(ink, "over", x=int(left), y=int(top))
    except Exception:
        return image


def _safe_stem(stem: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)
    return cleaned[:80] or "map"


def _free_name(preferred: Path) -> Path:
    if not preferred.exists():
        return preferred
    for number in range(2, 1000):
        candidate = preferred.with_name(f"{preferred.stem}_{number}{preferred.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(str(preferred))


def standin_size(width: int, height: int, max_dim: int) -> tuple[int, int]:
    long_side = max(width, height)
    if long_side <= max_dim:
        return width, height
    scale = max_dim / long_side
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))
