from __future__ import annotations

import copy
from typing import Sequence

from pptx.oxml.ns import qn
from pptx.table import _Cell

from sdgen.fill.text import set_rich_text
from sdgen.styles import resolve_font, resolve_spacing, theme_fonts
from sdgen.textmetrics import line_height_pt, text_width_pt, wrapped_lines

Row = Sequence[str] | dict[str, str]
EMU_PER_PT = 12700
CELL_INSETS = (91440, 45720, 91440, 45720)  # left, top, right, bottom
TABLE_FONT_PT = 18.0
NARROW_WEIGHT, WIDE_WEIGHT = 4, 36  # a column's share of the width follows its longest text, within these bounds
COLUMN_SLACK = 137160  # 0.15 in beyond the header word, so it never breaks


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
    base = _base_height(body, template)
    names = list(columns) if columns else [_cell_text(tc) for tc in header[0].tc_lst] if header else []
    width = len(template.tc_lst)

    new_rows = []
    for row in rows or [[]]:
        values = _values(row, names, width)
        tr = copy.deepcopy(template)
        tr.h = base
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
    base = _base_height(body, template)
    names = list(columns) if columns else [_cell_text(tc) for tc in tr_list[0].tc_lst] if tr_list else []
    for row in rows:
        tr = copy.deepcopy(template)
        tr.h = base
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
    base = _base_height(body, template)
    names = list(columns) if columns else [_cell_text(tc) for tc in tr_list[0].tc_lst] if tr_list else []
    return [_row_height(graphic_frame, template, _values(row, names, len(template.tc_lst)), widths, theme, base) for row in rows]


def _base_height(body, template) -> int:
    """The design's row height: rows only ever grow, so the shortest body row is the one nothing settled."""
    return min((tr.h for tr in body), default=template.h)


def _row_height(frame, tr, values: list[str], widths: list[int], theme: tuple[str, str], base: int | None = None) -> int:
    needed = 0
    for index, (tc, text) in enumerate(zip(tr.tc_lst, values)):
        width = widths[index] if index < len(widths) else (widths[-1] if widths else 0)
        needed = max(needed, _cell_height(frame, tc, text, width, theme))
    return max(tr.h if base is None else base, needed)


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


def resize_columns(graphic_frame, count: int) -> None:
    """Gives the table `count` columns of equal width over the same total width; merged cells cannot be resized."""
    tbl = graphic_frame.table._tbl
    grid = tbl.tblGrid
    columns = grid.gridCol_lst
    if count < 1 or count == len(columns):
        return
    for tr in tbl.tr_lst:
        for tc in tr.tc_lst:
            if any(tc.get(name) for name in ("gridSpan", "rowSpan", "hMerge", "vMerge")):
                raise ValueError("merged cells")
    total = sum(gc.w for gc in columns)
    if count < len(columns):
        for gc in columns[count:]:
            grid.remove(gc)
        for tr in tbl.tr_lst:
            for tc in tr.tc_lst[count:]:
                tr.remove(tc)
    else:
        for _ in range(count - len(columns)):
            gc = copy.deepcopy(grid.gridCol_lst[-1])
            _strip_ext(gc)
            _append_before_ext(grid, gc)
            for tr in tbl.tr_lst:
                tc = copy.deepcopy(tr.tc_lst[-1])
                _strip_ext(tc)
                _append_before_ext(tr, tc)
                set_rich_text(_Cell(tc, tbl), "")
    width = total // count
    for index, gc in enumerate(grid.gridCol_lst):
        gc.w = width if index < count - 1 else total - width * (count - 1)


def column_weights(columns: Sequence[str], rows: Sequence[Row]) -> list[float]:
    """Relative widths from the longest line each column carries, so a numbering column stays narrow and a note column wide."""
    names = list(columns)
    weights = []
    for index, name in enumerate(names):
        longest = len(name)
        for row in rows:
            value = _values(row, names, len(names))[index]
            longest = max(longest, max((len(line) for line in value.splitlines()), default=0))
        weights.append(float(min(max(longest, NARROW_WEIGHT), WIDE_WEIGHT)))
    return weights


def fit_columns(graphic_frame, columns: Sequence[str], rows: Sequence[Row], theme: tuple[str, str] | None = None) -> None:
    """Shares the table's width by the text each column carries, never narrower than its header's longest word."""
    tbl = graphic_frame.table._tbl
    grid = tbl.tblGrid.gridCol_lst
    names = list(columns)
    if len(names) != len(grid) or not tbl.tr_lst:
        return
    theme = theme or theme_fonts(graphic_frame.part)
    total = sum(gc.w for gc in grid)
    weights = column_weights(names, rows)
    minimums = [_word_width(graphic_frame, tc, max(name.split(), key=len, default=""), theme) for name, tc in zip(names, tbl.tr_lst[0].tc_lst)]
    if sum(minimums) >= total:
        widths = [total * m / sum(minimums) for m in minimums]
    else:
        widths = [max(total * w / sum(weights), m) for w, m in zip(weights, minimums)]
        excess = sum(widths) - total
        room = [w - m for w, m in zip(widths, minimums)]
        if excess > 0 and sum(room) > 0:
            widths = [w - excess * r / sum(room) for w, r in zip(widths, room)]
    assigned = 0
    for index, gc in enumerate(grid):
        gc.w = int(widths[index]) if index < len(grid) - 1 else total - assigned
        assigned += gc.w


def _word_width(frame, tc, word: str, theme: tuple[str, str]) -> int:
    left, _, right, _ = CELL_INSETS
    tcpr = tc.find(qn("a:tcPr"))
    if tcpr is not None:
        left, right = int(tcpr.get("marL", left)), int(tcpr.get("marR", right))
    body = tc.find(qn("a:txBody"))
    p = body.find(qn("a:p")) if body is not None else None
    spec = resolve_font(frame, p, TABLE_FONT_PT, theme) if p is not None else None
    width = text_width_pt(word, spec) * EMU_PER_PT if spec is not None else len(word) * TABLE_FONT_PT * 0.6 * EMU_PER_PT
    return int(width) + left + right + COLUMN_SLACK


def header_texts(graphic_frame) -> list[str]:
    return [_cell_text(tc) for tc in graphic_frame.table._tbl.tr_lst[0].tc_lst]


def _strip_ext(element) -> None:
    for ext in element.findall(qn("a:extLst")):
        element.remove(ext)


def _append_before_ext(parent, child) -> None:
    ext = parent.find(qn("a:extLst"))
    if ext is not None:
        ext.addprevious(child)
    else:
        parent.append(child)


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
