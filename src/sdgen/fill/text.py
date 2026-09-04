from __future__ import annotations

import copy
import re

from lxml import etree
from pptx.oxml.ns import qn
from pydantic import BaseModel, Field

from sdgen.inventory import walk_shapes

BULLET_RE = re.compile(r"^(\s*)[-*•]\s+(.*)$")
INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*)")
DEFAULT_INDENT_EMU = 342900
MAX_LEVEL = 8
RUN_TAGS = (qn("a:r"), qn("a:br"), qn("a:fld"))
BULLET_TAGS = (qn("a:buChar"), qn("a:buAutoNum"), qn("a:buNone"), qn("a:buBlip"))
PPR_TAIL_TAGS = (qn("a:tabLst"), qn("a:defRPr"), qn("a:extLst"))


class Span(BaseModel):
    text: str
    bold: bool = False
    italic: bool = False


class Block(BaseModel):
    spans: list[Span] = Field(default_factory=list)
    bullet: bool = False
    level: int = 0

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


def parse_blocks(value: str) -> list[Block]:
    blocks: list[Block] = []
    for raw in value.splitlines():
        if not raw.strip():
            continue
        match = BULLET_RE.match(raw)
        if match:
            indent = match.group(1).replace("\t", "  ")
            level = min(len(indent) // 2, MAX_LEVEL)
            blocks.append(Block(spans=parse_spans(match.group(2).strip()), bullet=True, level=level))
        else:
            blocks.append(Block(spans=parse_spans(raw.strip())))
    return blocks


def parse_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            spans.append(Span(text=part[2:-2], bold=True))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            spans.append(Span(text=part[1:-1], italic=True))
        else:
            spans.append(Span(text=part))
    return spans


def set_rich_text(target, value: str | list[Block], keep_prefix: str | None = None) -> None:
    blocks = parse_blocks(value) if isinstance(value, str) else [b.model_copy(deep=True) for b in value]
    tx_body = target.text_frame._txBody
    paragraphs = tx_body.findall(qn("a:p"))
    inherits_bullets = bool(getattr(target, "is_placeholder", False))

    base = _first(paragraphs, _has_text)
    if base is None:
        base = paragraphs[0]
    plain = _first(paragraphs, lambda p: _has_text(p) and not _has_bullet(p))
    if plain is None:
        plain = base
    bullet_templates = _bullet_templates(paragraphs)

    prefix_p = None
    prefix_rpr = None
    if keep_prefix:
        prefix_p, prefix_rpr = _split_prefix(paragraphs[0], keep_prefix)
        if prefix_p is None and blocks:
            blocks[0].spans.insert(0, Span(text=keep_prefix))

    new_paragraphs: list[etree._Element] = []
    for index, block in enumerate(blocks):
        if index == 0 and prefix_p is not None:
            _append_spans(prefix_p, block.spans, prefix_rpr)
            new_paragraphs.append(prefix_p)
            continue
        template = _pick_template(block, plain, bullet_templates)
        paragraph = _clone_paragraph(template)
        _apply_bullet(paragraph, block, _has_bullet(template), inherits_bullets)
        rpr = _run_rpr(template)
        _append_spans(paragraph, block.spans, rpr)
        _keep_style_when_empty(paragraph, rpr)
        new_paragraphs.append(paragraph)

    if not new_paragraphs:
        if prefix_p is None:
            prefix_p = _clone_paragraph(base)
            _keep_style_when_empty(prefix_p, _run_rpr(base))
        new_paragraphs.append(prefix_p)

    for paragraph in paragraphs:
        tx_body.remove(paragraph)
    for paragraph in new_paragraphs:
        tx_body.append(paragraph)


def replace_token(target, token: str, value: str, loose_spaces: bool = False) -> int:
    pattern = _loose_pattern(token) if loose_spaces else None
    return sum(_replace_in_paragraph(p._p, token, value, pattern) for p in target.text_frame.paragraphs)


def replace_literal_everywhere(prs, old: str, new: str, loose_spaces: bool = True) -> int:
    count = 0
    for slide in prs.slides:
        for shape in walk_shapes(slide.shapes):
            if shape.has_text_frame:
                count += replace_token(shape, old, new, loose_spaces)
            elif getattr(shape, "has_table", False):
                for cell in shape.table.iter_cells():
                    count += replace_token(cell, old, new, loose_spaces)
    return count


def _loose_pattern(token: str) -> re.Pattern | None:
    words = token.split()
    return re.compile(r"\s+".join(re.escape(w) for w in words)) if words else None


def _replace_in_paragraph(p: etree._Element, token: str, value: str, pattern: re.Pattern | None = None) -> int:
    count = 0
    search_from = 0
    while True:
        runs = [r for r in p if r.tag == qn("a:r")]
        texts = [_text_of(r) for r in runs]
        full = "".join(texts)
        if pattern is not None:
            match = pattern.search(full, search_from)
            start, end = (match.start(), match.end()) if match else (-1, -1)
        else:
            start = full.find(token, search_from)
            end = start + len(token)
        if start < 0:
            return count
        first = last = None
        local_start = local_end = 0
        offset = 0
        for i, text in enumerate(texts):
            if first is None and start < offset + len(text):
                first, local_start = i, start - offset
            if first is not None and end <= offset + len(text):
                last, local_end = i, end - offset
                break
            offset += len(text)
        if first is None or last is None:
            return count
        head = texts[first][:local_start]
        tail = texts[last][local_end:]
        _t(runs[first]).text = head + value + tail
        for run in runs[first + 1 : last + 1]:
            p.remove(run)
        count += 1
        search_from = start + len(value)


def _split_prefix(p: etree._Element, prefix: str):
    runs = [r for r in p if r.tag == qn("a:r")]
    if not "".join(_text_of(r) for r in runs).startswith(prefix):
        return None, None
    clone = _clone_paragraph(p)
    position = 0
    content_rpr = None
    for i, run in enumerate(runs):
        text = _text_of(run)
        kept = copy.deepcopy(run)
        if position + len(text) <= len(prefix):
            position += len(text)
            _insert_run(clone, kept)
            if position == len(prefix):
                source = runs[i + 1] if i + 1 < len(runs) else run
                content_rpr = _rpr_of(source)
                break
        else:
            _t(kept).text = text[: len(prefix) - position]
            _insert_run(clone, kept)
            content_rpr = _rpr_of(run)
            break
    return clone, content_rpr


def _pick_template(block: Block, plain: etree._Element, bullets: dict[int, etree._Element]) -> etree._Element:
    if not block.bullet or not bullets:
        return plain
    if block.level in bullets:
        return bullets[block.level]
    lower = [lvl for lvl in bullets if lvl <= block.level]
    return bullets[max(lower)] if lower else bullets[min(bullets)]


def _clone_paragraph(p: etree._Element) -> etree._Element:
    clone = copy.deepcopy(p)
    for child in list(clone):
        if child.tag in RUN_TAGS:
            clone.remove(child)
    return clone


def _apply_bullet(p: etree._Element, block: Block, template_has_bullet: bool, inherits: bool) -> None:
    ppr = p.find(qn("a:pPr"))
    if block.bullet:
        if ppr is None:
            ppr = etree.Element(qn("a:pPr"))
            p.insert(0, ppr)
        if block.level:
            ppr.set("lvl", str(block.level))
        else:
            ppr.attrib.pop("lvl", None)
        if not template_has_bullet and not inherits:
            _remove_bullet_elements(ppr)
            ppr.set("marL", str(DEFAULT_INDENT_EMU * (block.level + 1)))
            ppr.set("indent", str(-DEFAULT_INDENT_EMU))
            bullet = etree.Element(qn("a:buChar"))
            bullet.set("char", "•")
            _insert_in_ppr(ppr, bullet)
    elif template_has_bullet and ppr is not None:
        _remove_bullet_elements(ppr)
        ppr.attrib.pop("lvl", None)
        ppr.set("marL", "0")
        ppr.set("indent", "0")
        _insert_in_ppr(ppr, etree.Element(qn("a:buNone")))


def _append_spans(p: etree._Element, spans: list[Span], rpr: etree._Element | None) -> None:
    for span in spans:
        run = etree.Element(qn("a:r"))
        run_rpr = copy.deepcopy(rpr) if rpr is not None else None
        if run_rpr is None and (span.bold or span.italic):
            run_rpr = etree.Element(qn("a:rPr"))
        if run_rpr is not None:
            if span.bold:
                run_rpr.set("b", "1")
            if span.italic:
                run_rpr.set("i", "1")
            run.append(run_rpr)
        text = etree.SubElement(run, qn("a:t"))
        text.text = span.text
        _insert_run(p, run)


def _keep_style_when_empty(p: etree._Element, rpr: etree._Element | None) -> None:
    if rpr is None or p.find(qn("a:r")) is not None:
        return
    end = copy.deepcopy(rpr)
    end.tag = qn("a:endParaRPr")
    existing = p.find(qn("a:endParaRPr"))
    if existing is not None:
        p.replace(existing, end)
    else:
        p.append(end)


def _insert_run(p: etree._Element, run: etree._Element) -> None:
    end = p.find(qn("a:endParaRPr"))
    if end is not None:
        end.addprevious(run)
    else:
        p.append(run)


def _insert_in_ppr(ppr: etree._Element, element: etree._Element) -> None:
    for child in ppr:
        if child.tag in PPR_TAIL_TAGS:
            child.addprevious(element)
            return
    ppr.append(element)


def _remove_bullet_elements(ppr: etree._Element) -> None:
    for child in list(ppr):
        if child.tag in BULLET_TAGS:
            ppr.remove(child)


def _bullet_templates(paragraphs: list[etree._Element]) -> dict[int, etree._Element]:
    templates: dict[int, etree._Element] = {}
    for p in paragraphs:
        if _has_bullet(p):
            level = int(p.find(qn("a:pPr")).get("lvl", "0"))
            templates.setdefault(level, p)
    return templates


def _has_bullet(p: etree._Element) -> bool:
    ppr = p.find(qn("a:pPr"))
    return ppr is not None and (
        ppr.find(qn("a:buChar")) is not None or ppr.find(qn("a:buAutoNum")) is not None
    )


def _has_text(p: etree._Element) -> bool:
    return any((t.text or "").strip() for t in p.iter(qn("a:t")))


def _run_rpr(p: etree._Element) -> etree._Element | None:
    for run in p.findall(qn("a:r")):
        if _text_of(run).strip():
            return _rpr_of(run)
    end = p.find(qn("a:endParaRPr"))
    if end is not None:
        rpr = copy.deepcopy(end)
        rpr.tag = qn("a:rPr")
        return rpr
    return None


def _rpr_of(run: etree._Element) -> etree._Element | None:
    rpr = run.find(qn("a:rPr"))
    return copy.deepcopy(rpr) if rpr is not None else None


def _text_of(run: etree._Element) -> str:
    return run.findtext(qn("a:t")) or ""


def _t(run: etree._Element) -> etree._Element:
    t = run.find(qn("a:t"))
    if t is None:
        t = etree.SubElement(run, qn("a:t"))
    return t


def _first(items, predicate):
    return next((item for item in items if predicate(item)), None)
