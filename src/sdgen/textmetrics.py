from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
FONT_FILES = {
    "arial": ("arial.ttf", "arialbd.ttf"),
    "calibri": ("calibri.ttf", "calibrib.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf"),
    "segoe ui": ("segoeui.ttf", "segoeuib.ttf"),
    "verdana": ("verdana.ttf", "verdanab.ttf"),
    "tahoma": ("tahoma.ttf", "tahomabd.ttf"),
    "times new roman": ("times.ttf", "timesbd.ttf"),
}
FALLBACK_FAMILY = "arial"
REFERENCE_PX = 100
HEURISTIC_CHAR_EM = 0.55
HEURISTIC_LINE_EM = 1.3
CAPACITY_FILL = 0.8
SAMPLE = "The quick brown fox jumps over the lazy dog, and integration files arrive daily from three banks."


@dataclass(frozen=True)
class FontSpec:
    family: str = "Arial"
    size_pt: float = 14.0
    bold: bool = False


def load_font(family: str, bold: bool = False):
    """The font file for a family, or None when none is available; the heuristics apply then."""
    return _load(family.strip().lower(), bold, str(FONT_DIR))


@lru_cache(maxsize=64)
def _load(family: str, bold: bool, font_dir: str):
    from PIL import ImageFont

    files = FONT_FILES.get(family) or FONT_FILES[FALLBACK_FAMILY]
    for name in (files[1] if bold else files[0], files[0]):
        path = Path(font_dir) / name
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), REFERENCE_PX)
            except OSError:
                return None
    return None


def text_width_pt(text: str, spec: FontSpec) -> float:
    font = load_font(spec.family, spec.bold)
    if font is None:
        return len(text) * spec.size_pt * HEURISTIC_CHAR_EM
    return font.getlength(text) / REFERENCE_PX * spec.size_pt


def line_height_pt(spec: FontSpec, spacing_pct: float = 100.0) -> float:
    font = load_font(spec.family, spec.bold)
    em = HEURISTIC_LINE_EM if font is None else font.font.height / REFERENCE_PX
    return em * spec.size_pt * spacing_pct / 100.0


def avg_char_em(spec: FontSpec) -> float:
    font = load_font(spec.family, spec.bold)
    if font is None:
        return HEURISTIC_CHAR_EM
    return font.getlength(SAMPLE) / REFERENCE_PX / len(SAMPLE)


def wrapped_lines(text: str, width_pt: float, spec: FontSpec) -> int:
    """Lines a paragraph takes with greedy word wrapping; a word wider than the box breaks over lines."""
    words = text.split()
    if not words or width_pt <= 0:
        return 1
    space = text_width_pt(" ", spec)
    lines, used = 1, 0.0
    for word in words:
        width = text_width_pt(word, spec)
        if width > width_pt:
            lines += (0 if used == 0.0 else 1) + int(width // width_pt)
            used = width % width_pt
        elif used == 0.0:
            used = width
        elif used + space + width <= width_pt:
            used += space + width
        else:
            lines += 1
            used = width
    return lines


def line_chars(width_pt: float, spec: FontSpec) -> int:
    return max(1, int(width_pt / (spec.size_pt * avg_char_em(spec))))


def capacity_chars(
    width_pt: float,
    height_pt: float,
    spec: FontSpec,
    spacing_pct: float = 100.0,
    space_after_pt: float = 0.0,
    prefix_len: int = 0,
    fill: float = CAPACITY_FILL,
) -> int:
    """Characters that fit comfortably: full lines times characters per line, with room for ragged ends."""
    line = line_height_pt(spec, spacing_pct) + space_after_pt / 4
    if line <= 0 or width_pt <= 0 or height_pt <= 0:
        return 0
    rows = int(height_pt / line)
    estimate = int(rows * line_chars(width_pt, spec) * fill) - prefix_len
    return max(0, estimate // 10 * 10)
