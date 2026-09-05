from __future__ import annotations

import copy
from typing import Sequence

from pptx.oxml.ns import qn
from pptx.table import _Cell

from sdgen.fill.text import set_rich_text

Row = Sequence[str] | dict[str, str]


def fill_table(
    graphic_frame,
    rows: Sequence[Row],
    header_rows: int = 1,
    keep_last_row_if: str | None = None,
    columns: Sequence[str] | None = None,
) -> None:
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
    graphic_frame.height = sum(tr.h for tr in tbl.tr_lst)


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
