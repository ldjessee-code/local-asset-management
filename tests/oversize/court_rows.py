# SPDX-License-Identifier: AGPL-3.0-or-later
"""The 35 oversize names from the court's 2026-10-06 classification.

Pixel sizes are the real header sizes. Part paths are the court's
part_files column. Tests scale the pixels down; they do not read the
maps on disk.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CourtRow:
    rel: str
    width: int
    height: int
    combined: bool
    parts_found: str
    parts: tuple[str, ...]


def _row(
    rel: str,
    width: int,
    height: int,
    combined: bool,
    parts_found: str,
    parts: list[str] | tuple[str, ...] = (),
) -> CourtRow:
    return CourtRow(rel, width, height, combined, parts_found, tuple(parts))


def _aztec(variant: str) -> CourtRow:
    base = r"5_Aztec_Ext_Set04\A2_Jpeg_Aztec"
    return _row(
        rf"{base}\Aztec_Zone2_Variations_{variant}.jpg",
        24803,
        21039,
        True,
        "partial",
        [rf"{base}\A2_Aztec_Zone2_{letter}_{variant}.jpg" for letter in "FG"],
    )


def _dwarven(part_variant: str, combined_variant: str) -> CourtRow:
    base = r"5_DwarvenStronghold_Set07"
    return _row(
        rf"{base}\Combined High res\DwarvenInterior_Lvl2_PtA_{combined_variant}.jpg",
        14883,
        15325,
        True,
        "yes",
        [
            rf"{base}\A2_Jpeg\A2_DwarvenInterior_Lvl2_pt1_{part_variant}.jpg",
            rf"{base}\A2_Jpeg\A2_DwarvenInterior_Lvl2_pt2_{part_variant}.jpg",
        ],
    )


def _pilgrim(middle: str) -> CourtRow:
    return _row(
        rf"5_Hordelands_Set5\A2\Pilgrim'sPeak_89x153\Jpeg\Gridless\Pilgrim'sPeak_{middle}_Gridless.jpg",
        15412,
        27185,
        False,
        "no",
    )


def _entrance(variant: str) -> CourtRow:
    base = r"5_MalatraRuins_Set6\A2"
    return _row(
        rf"{base}\MalatranTombEntrance\Jpeg\Gridless\MalatranTombEntrance_{variant}_Gridless.jpg",
        13529,
        17278,
        True,
        "yes",
        [
            rf"{base}\MalatranTombEntrance_{letter}\Jpeg\Gridless\MalatranTombEntrance_{letter}_{variant}_Gridless.jpg"
            for letter in "ABCD"
        ],
    )


def _citadel(variant: str) -> CourtRow:
    base = r"5_RadiantCitadel_Set01"
    return _row(
        rf"{base}\HD_Combined\Citadel_Combined_{variant}_Gridless.jpg",
        28780,
        20433,
        True,
        "partial",
        [rf"{base}\HD\Citadel_A\Gridless\Citadel_A_{variant}_Gridless.jpg"],
    )


def _slither(variant: str, letters: str) -> CourtRow:
    base = r"5_Undermountain_Set14"
    return _row(
        rf"{base}\HD_Combined\Combined_SlitherSwamp\Gridless\SlitherSwamp_{variant}_Gridless.jpg",
        10200,
        21338,
        True,
        "yes",
        [
            rf"{base}\A2 (HD Jpegs)\SlitherSwamp_{letter}\Gridless\SlitherSwamp_{letter}_{variant}_Gridless.jpg"
            for letter in letters
        ],
    )


def _core(variant: str) -> CourtRow:
    base = r"5_Undermountain_Set15"
    return _row(
        rf"{base}\HD_Combined\Combined_dweomercore\Gridless\Combined_Dwarvencore_{variant}_Gridless.jpg",
        12035,
        21338,
        True,
        "yes",
        [
            rf"{base}\A2 (HD Jpegs)\Dweomercore_{letter}\Gridless\Dwarvencore_{letter}_{variant}_Gridless.jpg"
            for letter in "ABCDEF"
        ],
    )


ROWS: tuple[CourtRow, ...] = (
    _aztec("Day"),
    _aztec("Sunset"),
    _dwarven("Inferno", "Inferno"),
    _dwarven("Ore Vein", "OreVein"),
    _dwarven("Snow", "Snow"),
    _dwarven("Volcano", "Volcano"),
    _pilgrim("Day Effect"),
    _pilgrim("Day"),
    _pilgrim("Effect"),
    _pilgrim("Fire"),
    _pilgrim("Fog"),
    _pilgrim("Night"),
    _row(
        r"5_MalatraRuins_Set4\A2\MalatranTombComplex\Jpeg\Gridless\MalatranTombComplex_Night Unlit_Gridless.jpg",
        9816,
        12192,
        True,
        "yes",
        [
            r"5_MalatraRuins_Set4\A2\MalatranTombComplex_A\Jpeg\Gridless\MalatranTombComplex_A_Night Unlit_Gridless.jpg",
            r"5_MalatraRuins_Set4\A2\MalatranTombComplex_B\Jpeg\Gridless\MalatranTombComplex_B_Night Unlit_Gridless.jpg",
        ],
    ),
    _entrance("Day"),
    _entrance("Fey"),
    _entrance("Night Light"),
    _entrance("Night"),
    _citadel("Day_Eth Plane"),
    _citadel("Day"),
    _citadel("Night_Eth Plane"),
    _citadel("Night"),
    _row(
        r"5_RadiantCitadel_Set06\HD_Combined\Grotto_Combined\Gridless\Grotto_Combined_Night_Gridless.jpg",
        14538,
        16355,
        True,
        "yes",
        [
            rf"5_RadiantCitadel_Set06\A2\A2_Grotto_{letter}\Gridless\A2_Grotto_{letter}_Night_Gridless.jpg"
            for letter in "ABCD"
        ],
    ),
    _row(
        r"5_RadiantCitadel_Set11\A2\CourtofWhispers_55x67\HD Jpeg\Gridless\Court of Whispers_Day_Gridless.jpg",
        15437,
        18572,
        False,
        "no",
    ),
    _row(
        r"5_RadiantCitadel_Set15\A2\Temple_57x80\Jpeg\Gridless\Temple_BrightNight_Gridless.jpg",
        16016,
        22450,
        False,
        "no",
    ),
    _row(
        r"5_RadiantCitadel_Set15\A2\Temple_57x80\Jpeg\Gridless\Temple_Day_Gridless.jpg",
        16016,
        22450,
        False,
        "no",
    ),
    _row(
        r"5_RadiantCitadel_Set15\A2\Temple_57x80\Jpeg\Gridless\Temple_Night_Gridless.jpg",
        16016,
        22450,
        False,
        "no",
    ),
    _slither("Day", "ABCDEF"),
    _slither("Day_Unlit", "ABDEF"),
    _slither("PoisonSwamp", "ABCDEF"),
    _slither("PoisonSwamp_Unlit", "ABCDEF"),
    _core("Bronze"),
    _core("Day"),
    _core("Night"),
    _row(r"Map_PortTown\PortTown_Final_Day.jpg", 21346, 21882, False, "no"),
    _row(r"Map_PortTown\PortTown_Final_Night.jpg", 21346, 21882, False, "no"),
)


# Neighbours that must not become parts: a misnamed SlitherSwamp file, a
# PortTown copy, and online exports whose tokens match only after noise
# removal (no extra letter or pt token).
DECOYS: tuple[str, ...] = (
    r"5_Undermountain_Set14\A2 (HD Jpegs)\SlitherSwamp_C\Gridless\SlitherSwamp_Day_Unlit_Gridless.jpg",
    r"Map_PortTown\PortTown_Final_Day - Copy.jpg",
    r"5_Aztec_Ext_Set04\Online\Online_Aztec\Gridless\Online_Aztec_Zone2_Day_Gridless.jpg",
    r"5_Aztec_Ext_Set04\Online\Online_Aztec\Gridless\Online_Aztec_Zone2_Sunset_Gridless.jpg",
    r"5_DwarvenStronghold_Set07\Online\Online_DwarvenInterior_Lvl2_A\Gridless\Online_DwarvenInterior_Lvl2_Inferno_Gridless.jpg",
)
