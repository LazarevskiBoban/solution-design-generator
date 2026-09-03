from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pptx import Presentation
from pydantic import BaseModel, Field

from sdgen.content import Content, ImageValue, parse_pipe_table
from sdgen.fill.image import replace_picture
from sdgen.fill.slides import clone_slide, remove_slide
from sdgen.fill.table import fill_table
from sdgen.fill.text import Block, parse_blocks, replace_literal_everywhere, replace_token, set_rich_text
from sdgen.inventory import find_shape, walk_shapes
from sdgen.manifest import Binding, FieldSpec, Manifest

CONTINUATION_SUFFIX = " (cont.)"


class RenderIssue(BaseModel):
    level: Literal["info", "warning", "error"] = "warning"
    field: str | None = None
    slide: int | None = None
    message: str

    def __str__(self) -> str:
        where = f" [slide {self.slide}]" if self.slide else ""
        who = f" {self.field}:" if self.field else ""
        return f"{self.level}{where}{who} {self.message}"


class RenderResult(BaseModel):
    output: str
    slides: int
    issues: list[RenderIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[RenderIssue]:
        return [i for i in self.issues if i.level == "error"]


def render(
    template: str | Path,
    manifest: Manifest,
    content: Content,
    output: str | Path,
    blank_missing: bool = False,
) -> RenderResult:
    prs = Presentation(str(template))
    slides = list(prs.slides)
    issues: list[RenderIssue] = []
    prototypes = set(manifest.slides.prototypes.values())

    for spec in manifest.globals:
        value = content.globals.get(spec.key, "")
        if not value:
            issues.append(RenderIssue(field=spec.key, message=f"no value; '{spec.replaces}' left in place"))
            continue
        hits = replace_literal_everywhere(prs, spec.replaces, value)
        if hits == 0:
            issues.append(RenderIssue(field=spec.key, message=f"'{spec.replaces}' not found in the template"))

    for spec in manifest.fields:
        value = content.fields.get(spec.key)
        if value is None or value == "" or value == []:
            if not blank_missing or spec.kind == "image":
                issues.append(RenderIssue(field=spec.key, message="no value; template content left in place"))
                continue
            value = [] if spec.kind == "table" else ""
        for binding in spec.bindings:
            if not 1 <= binding.slide <= len(slides):
                issues.append(RenderIssue(level="error", field=spec.key, slide=binding.slide, message="slide does not exist"))
                continue
            slide = slides[binding.slide - 1]
            shape = _locate(slide, binding)
            if shape is None:
                issues.append(RenderIssue(level="error", field=spec.key, slide=binding.slide, message=f"shape {binding.shape.id} ({binding.shape.name}) not found"))
                continue
            try:
                _apply(prs, slide, shape, spec, binding, value, issues, binding.slide in prototypes)
            except Exception as exc:  # keep rendering the rest of the document
                issues.append(RenderIssue(level="error", field=spec.key, slide=binding.slide, message=str(exc)))

    for index in sorted(set(manifest.slides.exclude), reverse=True):
        if 1 <= index <= len(slides):
            remove_slide(prs, slides[index - 1])
        else:
            issues.append(RenderIssue(slide=index, message="excluded slide does not exist"))

    prs.save(str(output))
    return RenderResult(output=str(output), slides=len(prs.slides), issues=issues)


def _locate(slide, binding: Binding):
    shape = find_shape(slide, binding.shape.id)
    if shape is not None and (not binding.shape.name or shape.name == binding.shape.name):
        return shape
    if binding.shape.name:
        by_name = [s for s in walk_shapes(slide.shapes) if s.name == binding.shape.name]
        if len(by_name) == 1:
            return by_name[0]
    return shape


def _apply(prs, slide, shape, spec: FieldSpec, binding: Binding, value: Any, issues: list[RenderIssue], prototype: bool) -> None:
    if spec.kind == "table":
        if not getattr(shape, "has_table", False):
            raise ValueError("bound shape is not a table")
        rows = _as_rows(value)
        fill_table(shape, rows, header_rows=binding.header_rows, keep_last_row_if=binding.keep_last_row_if, columns=spec.columns or None)
        return
    if spec.kind == "image":
        path = value.path if isinstance(value, ImageValue) else str(value)
        if not Path(path).is_file():
            raise FileNotFoundError(f"image not found: {path}")
        replace_picture(slide, shape, path, binding.fit)
        return
    if not shape.has_text_frame:
        raise ValueError("bound shape has no text")
    text = _as_text(value)
    if binding.mode == "token":
        if not binding.token or replace_token(shape, binding.token, " ".join(text.split())) == 0:
            issues.append(RenderIssue(field=spec.key, slide=binding.slide, message=f"placeholder {binding.token} not found"))
        return

    blocks = parse_blocks(text)
    chunks = [blocks]
    if binding.max_chars and len(text) > binding.max_chars:
        if prototype or shape.is_placeholder:
            chunks = _split_blocks(blocks, binding.max_chars)
            issues.append(RenderIssue(level="info", field=spec.key, slide=binding.slide, message=f"continued on {len(chunks) - 1} extra slide(s)"))
        else:
            issues.append(RenderIssue(field=spec.key, slide=binding.slide, message=f"text is {len(text)} characters, about {binding.max_chars} fit"))

    set_rich_text(shape, chunks[0], keep_prefix=binding.keep_prefix)
    current = slide
    for chunk in chunks[1:]:
        current = clone_slide(prs, current)
        _mark_continuation(current)
        target = find_shape(current, shape.shape_id)
        set_rich_text(target, chunk, keep_prefix=binding.keep_prefix)


def _split_blocks(blocks: list[Block], max_chars: int) -> list[list[Block]]:
    chunks: list[list[Block]] = [[]]
    used = 0
    for block in blocks:
        size = len(block.text) + 1
        if chunks[-1] and used + size > max_chars:
            chunks.append([])
            used = 0
        chunks[-1].append(block)
        used += size
    return chunks


def _mark_continuation(slide) -> None:
    title = slide.shapes.title
    if title is None or title.text.endswith(CONTINUATION_SUFFIX):
        return
    paragraph = title.text_frame.paragraphs[-1]
    if paragraph.runs:
        paragraph.runs[-1].text += CONTINUATION_SUFFIX
    else:
        paragraph.text = paragraph.text + CONTINUATION_SUFFIX


def _as_text(value: Any) -> str:
    if isinstance(value, ImageValue):
        return value.path
    if isinstance(value, list):
        return "\n".join(" | ".join(str(v) for v in row.values()) for row in value)
    return str(value)


def _as_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, ImageValue):
        return []
    return parse_pipe_table(str(value))
