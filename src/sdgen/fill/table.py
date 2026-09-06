from __future__ import annotations

import copy
from typing import Sequence

from pptx.oxml.ns import qn
from pptx.table import _Cell

from sdgen.fill.text import set_rich_text
from sdgen.styles import resolve_font, resolve_spacing, theme_fonts
from sdgen.textmetrics import line_height_pt, wrapped_lines

Row = Sequence[str] | dict[str, str]
EMU_PER_PT = 12700
CELL_INSETS = (91440, 45720, 91440, 45720)  # left, top, right, bottom
TABLE_FONT_PT = 18.0


def fill_table(
    graphic_frame,
    rows: Sequence[Row],
    header_rows: int = 1,
    keep_last_row_if: str | None = None,
    columns: Sequence[str] | None = None,
    settle: bool = True,
) -> None:
    """Replaces the body rows; with `settle` the rows grow to what their text needs (off for marker text)."""
    tbl = graphic_frame.table._tbl
    tr_list = tbl.tr_lst
    header = tr_list[:header_rows]
    body = tr_list[header_rows:]
    footer = None
    if keep_last_row_if and body and _first_cell_text(body[-1]) == keep_last_row_if:
        footer = body[-1]
        body = body[:-1]

    template = body[0] if body else tr_list[-1]
    names = list(columns) if columns else [_cell_text(tc) for tc in header[0].tc_lst] if header else []
    width = len(template.tc_lst)

    new_rows = []
    for row in rows or [[]]:
        values = _values(row, names, width)
        tr = copy.deepcopy(template)
        for tc, value in zip(tr.tc_lst, values):
            set_rich_text(_Cell(tc, tbl), value)
        new_rows.append(tr)

    for tr in body:
        tbl.remove(tr)
    for tr in new_rows:
        if footer is not None:
            footer.addprevious(tr)
        else:
            tbl.append(tr)
    if settle:
        settle_heights(graphic_frame)
    else:
        graphic_frame.height = sum(tr.h for tr in tbl.tr_lst)


def append_rows(
    graphic_frame,
    rows: Sequence[Row],
    header_rows: int = 1,
    keep_last_row_if: str | None = None,
    columns: Sequence[str] | None = None,
) -> None:
    """Adds rows after the existing body, formatted like the last body row."""
    tbl = graphic_frame.table._tbl
    tr_list = tbl.tr_lst
    body = tr_list[header_rows:]
    footer = None
    if keep_last_row_if and body and _first_cell_text(body[-1]) == keep_last_row_if:
        footer = body[-1]
        body = body[:-1]
    template = body[-1] if body else tr_list[-1]
    names = list(columns) if columns else [_cell_text(tc) for tc in tr_list[0].tc_lst] if tr_list else []
    for row in rows:
        tr = copy.deepcopy(template)
        for tc, value in zip(tr.tc_lst, _values(row, names, len(template.tc_lst))):
            set_rich_text(_Cell(tc, tbl), value)
        if footer is not None:
            footer.addprevious(tr)
        else:
            tbl.append(tr)
    settle_heights(graphic_frame)


def has_footer(graphic_frame, keep_last_row_if: str | None, header_rows: int = 1) -> bool:
    tr_list = graphic_frame.table._tbl.tr_lst
    return bool(keep_last_row_if) and len(tr_list) > header_rows and _first_cell_text(tr_list[-1]) == keep_last_row_if


def settle_heights(graphic_frame) -> None:
    """Rows grow to what their wrapped text needs, as PowerPoint does when it opens the file."""
    tbl = graphic_frame.table._tbl
    for tr, needed in zip(tbl.tr_lst, row_heights(graphic_frame)):
        if needed > tr.h:
            tr.h = needed
    graphic_frame.height = sum(tr.h for tr in tbl.tr_lst)


def row_heights(graphic_frame, theme: tuple[str, str] | None = None, rows: Sequence[Row] | None = None, header_rows: int = 1, columns: Sequence[str] | None = None) -> list[int]:
    """Height each row needs in EMU: the row's own height or its tallest wrapped cell.

    With `rows`, the heights are estimated for those values as if written into the template body row.
    """
    tbl = graphic_frame.table._tbl
    theme = theme or theme_fonts(graphic_frame.part)
    widths = [gc.w for gc in tbl.tblGrid.gridCol_lst]
    if rows is None:
        return [_row_height(graphic_frame, tr, [_cell_text(tc) for tc in tr.tc_lst], widths, theme) for tr in tbl.tr_lst]
    tr_list = tbl.tr_lst
    body = tr_list[header_rows:]
    template = body[0] if body else tr_list[-1]
    names = list(columns) if columns else [_cell_text(tc) for tc in tr_list[0].tc_lst] if tr_list else []
    return [_row_height(graphic_frame, template, _values(row, names, len(template.tc_lst)), widths, theme) for row in rows]


def _row_height(frame, tr, values: list[str], widths: list[int], theme: tuple[str, str]) -> int:
    needed = 0
    for index, (tc, text) in enumerate(zip(tr.tc_lst, values)):
        width = widths[index] if index < len(widths) else (widths[-1] if widths else 0)
        needed = max(needed, _cell_height(frame, tc, text, width, theme))
    return max(tr.h, needed)


def _cell_height(frame, tc, text: str, width_emu: int, theme: tuple[str, str]) -> int:
    left, top, right, bottom = CELL_INSETS
    tcpr = tc.find(qn("a:tcPr"))
    if tcpr is not None:
        left, top, right, bottom = (int(tcpr.get(name, default)) for name, default in zip(("marL", "marT", "marR", "marB"), CELL_INSETS))
    body = tc.find(qn("a:txBody"))
    p = body.find(qn("a:p")) if body is not None else None
    if p is None:
        return 0
    spec = resolve_font(frame, p, TABLE_FONT_PT, theme)
    pct, before, after = resolve_spacing(frame, p, spec.size_pt)
    usable = (width_emu - left - right) / EMU_PER_PT
    lines = 0
    for paragraph in str(text).split("\n"):
        lines += wrapped_lines(" ".join(paragraph.split()), usable, spec) if paragraph.strip() else 1
    return int((max(1, lines) * line_height_pt(spec, pct) + before + after) * EMU_PER_PT) + top + bottom


def clear_table_body(graphic_frame, header_rows: int = 1) -> None:
    """Empties the body cells but keeps every row, so merged layouts stay intact."""
    tbl = graphic_frame.table._tbl
    for tr in tbl.tr_lst[header_rows:]:
        for tc in tr.tc_lst:
            set_rich_text(_Cell(tc, tbl), "")


def _values(row: Row, names: list[str], width: int) -> list[str]:
    if isinstance(row, dict):
        lookup = {_norm(k): v for k, v in row.items()}
        values = [str(lookup.get(_norm(name), "") or "") for name in names]
    else:
        values = [str(v or "") for v in row]
    values = values[:width]
    return values + [""] * (width - len(values))


def _cell_text(tc) -> str:
    return " ".join("".join(t.text or "" for t in tc.iter(qn("a:t"))).split())


def _first_cell_text(tr) -> str:
    return _cell_text(tr.tc_lst[0]) if tr.tc_lst else ""


def _norm(text: str) -> str:
    return " ".join(str(text).split()).lower()
