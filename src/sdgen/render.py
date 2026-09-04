from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pptx import Presentation
from pydantic import BaseModel, Field

from sdgen.content import Content, ImageValue, images_of, parse_pipe_table
from sdgen.fill.image import replace_picture
from sdgen.fill.slides import clone_slide, remove_slide
from sdgen.fill.table import fill_table
from sdgen.fill.text import Block, parse_blocks, replace_literal_everywhere, replace_token, set_rich_text
from sdgen.inventory import find_shape, walk_shapes
from sdgen.manifest import Binding, FieldSpec, Manifest

CONTINUATION_SUFFIX = " (cont.)"
MissingMode = Literal["keep", "blank", "placeholder"]
PLACEHOLDER_ROW = "[To be completed]"


def placeholder_text(label: str) -> str:
    return f"[To be completed: {label}]"


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
    missing: MissingMode = "placeholder",
    continue_on: set[int] | list[int] | None = None,
    field_modes: dict[str, MissingMode] | None = None,
) -> RenderResult:
    prs = Presentation(str(template))
    slides = list(prs.slides)
    issues: list[RenderIssue] = []
    prototypes = set(manifest.slides.prototypes.values())
    if continue_on is not None:
        prototypes |= set(continue_on)

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
        mode = (field_modes or {}).get(spec.key)
        if mode == "keep":
            issues.append(RenderIssue(level="info", field=spec.key, message="template content kept"))
            continue
        if mode == "blank":
            value = None
        mode = mode or missing
        if value is None or value == "" or value == []:
            if mode == "keep" or spec.kind == "image":
                level = "info" if spec.kind == "image" else "warning"
                issues.append(RenderIssue(level=level, field=spec.key, message="no value; template content left in place"))
                continue
            if mode == "blank":
                value = [] if spec.kind == "table" else ""
            else:
                value = [[PLACEHOLDER_ROW]] if spec.kind == "table" else placeholder_text(spec.label)
                issues.append(RenderIssue(level="info", field=spec.key, message="no value; placeholder shown"))
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
                allow = binding.slide in prototypes or (continue_on is None and shape.is_placeholder)
                _apply(prs, slide, shape, spec, binding, value, issues, allow)
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
        images = images_of(value)
        missing = [i.path for i in images if not Path(i.path).is_file()]
        if missing:
            raise FileNotFoundError(f"image not found: {missing[0]}")
        if not images:
            return
        picture = replace_picture(slide, shape, images[0].path, binding.fit)
        current = slide
        for image in images[1:]:
            current = clone_slide(prs, current)
            _mark_continuation(current)
            target = next((s for s in walk_shapes(current.shapes) if s.name == picture.name), None)
            if target is None:
                issues.append(RenderIssue(field=spec.key, slide=binding.slide, message="could not place an extra image"))
                break
            picture = replace_picture(current, target, image.path, binding.fit)
        if len(images) > 1:
            issues.append(RenderIssue(level="info", field=spec.key, slide=binding.slide, message=f"{len(images) - 1} extra slide(s) for images"))
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
        if prototype:
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


def _as_rows(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, ImageValue):
        return []
    return parse_pipe_table(str(value))
