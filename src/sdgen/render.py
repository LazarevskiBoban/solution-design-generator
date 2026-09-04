from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pptx import Presentation
from pydantic import BaseModel, Field

from sdgen.content import Content, ImageValue, images_of, parse_pipe_table
from sdgen.diagrams import remove_shapes
from sdgen.fill.image import replace_picture
from sdgen.flow import FlowSpec, draw_flow
from sdgen.fill.slides import clone_slide, move_slide, remove_slide
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


class ExtraSlide(BaseModel):
    """A slide that does not exist in the template: a clone of a prototype slide with one filled field."""

    key: str
    title: str
    spec: FieldSpec  # bound to the prototype slide and the shape to fill
    value: Any = None
    before: int = 0  # template slide number to insert in front of; 0 appends at the end


class RenderResult(BaseModel):
    output: str
    slides: int
    issues: list[RenderIssue] = Field(default_factory=list)
    slide_map: list[int] = Field(default_factory=list)  # template slide number behind each output slide
    slide_keys: list[str] = Field(default_factory=list)  # extra-slide key per output slide, empty for template slides

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
    hidden: list[int] | set[int] | None = None,
    order: list[int] | None = None,
    titles: dict[int, str] | None = None,
    extras: list[ExtraSlide] | None = None,
    flows: dict[str, FlowSpec] | None = None,
) -> RenderResult:
    prs = Presentation(str(template))
    slides = list(prs.slides)
    issues: list[RenderIssue] = []
    hidden_slides = {n for n in (hidden or []) if 1 <= n <= len(slides)}
    numbers = {slide.slide_id: n for n, slide in enumerate(slides, 1)}
    _reorder(prs, slides, order)
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

    subject = str(content.globals.get("subject", "") or "")
    for number, title in (titles or {}).items():
        if not 1 <= number <= len(slides) or not title.strip():
            continue
        shape = slides[number - 1].shapes.title
        if shape is None:
            issues.append(RenderIssue(slide=number, message="no title placeholder to retitle"))
            continue
        current = shape.text_frame.text
        set_rich_text(shape, f"{title.strip()}: {subject}" if subject and subject in current else title.strip())

    drawn = _draw_flows(slides, manifest, content, flows or {}, hidden_slides, issues)
    for spec in manifest.fields:
        if spec.key in drawn:
            continue
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
            if binding.slide in hidden_slides:
                continue
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

    extra_ids = _add_extras(prs, slides, extras or [], subject, issues)

    for index in sorted(set(manifest.slides.exclude) | hidden_slides, reverse=True):
        if 1 <= index <= len(slides):
            remove_slide(prs, slides[index - 1])
        else:
            issues.append(RenderIssue(slide=index, message="excluded slide does not exist"))

    prs.save(str(output))
    return RenderResult(
        output=str(output),
        slides=len(prs.slides),
        issues=issues,
        slide_map=_slide_map(prs, numbers),
        slide_keys=[extra_ids.get(slide.slide_id, "") for slide in prs.slides],
    )


def _draw_flows(slides: list, manifest: Manifest, content: Content, flows: dict[str, FlowSpec], hidden: set[int], issues: list[RenderIssue]) -> set[str]:
    """Draws each flow into its image slot unless an uploaded image fills that slot."""
    drawn: set[str] = set()
    for key, flow in flows.items():
        spec = manifest.field(key)
        if spec is None or spec.kind != "image":
            issues.append(RenderIssue(field=key, message="flow refers to a field that is not an image slot"))
            continue
        if content.fields.get(key) not in (None, "", []) or not flow.nodes:
            continue
        for binding in spec.bindings:
            if binding.slide in hidden or not 1 <= binding.slide <= len(slides):
                continue
            slide = slides[binding.slide - 1]
            shape = _locate(slide, binding)
            if shape is None:
                issues.append(RenderIssue(field=key, slide=binding.slide, message="image slot not found for the flow"))
                continue
            draw_flow(slide, (shape.left, shape.top, shape.width, shape.height), flow, prefix=f"Flow {key}")
            remove_shapes(slide, [shape])
            issues.append(RenderIssue(level="info", field=key, slide=binding.slide, message=f"diagram drawn from the brief ({len(flow.nodes)} nodes)"))
            drawn.add(key)
    return drawn


def _add_extras(prs, slides: list, extras: list[ExtraSlide], subject: str, issues: list[RenderIssue]) -> dict[int, str]:
    ids: dict[int, str] = {}
    for extra in extras:
        binding = extra.spec.bindings[0] if extra.spec.bindings else None
        if binding is None or not 1 <= binding.slide <= len(slides):
            issues.append(RenderIssue(level="error", field=extra.key, message="extra slide has no prototype slide"))
            continue
        clone = clone_slide(prs, slides[binding.slide - 1])
        ids[clone.slide_id] = extra.key
        title_shape = clone.shapes.title
        if title_shape is not None:
            current = title_shape.text_frame.text
            set_rich_text(title_shape, f"{extra.title}: {subject}" if subject and subject in current else extra.title)
        shape = find_shape(clone, binding.shape.id)
        if shape is None:
            issues.append(RenderIssue(level="error", field=extra.key, message=f"shape {binding.shape.id} not found on the prototype slide"))
        else:
            value = extra.value
            if value in (None, "", []):
                value = [[PLACEHOLDER_ROW]] if extra.spec.kind == "table" else placeholder_text(extra.title)
                issues.append(RenderIssue(level="info", field=extra.key, message="no value; placeholder shown"))
            try:
                _apply(prs, clone, shape, extra.spec, binding, value, issues, False)
            except Exception as exc:
                issues.append(RenderIssue(level="error", field=extra.key, message=str(exc)))
        target = slides[extra.before - 1] if 1 <= extra.before <= len(slides) else None
        if target is not None:
            others = [s.slide_id for s in prs.slides if s.slide_id != clone.slide_id]
            move_slide(prs, clone, others.index(target.slide_id))
        else:
            move_slide(prs, clone, len(prs.slides) - 1)
    return ids


def _reorder(prs, slides: list, order: list[int] | None) -> None:
    wanted: list[int] = []
    for number in order or []:
        if 1 <= number <= len(slides) and number not in wanted:
            wanted.append(number)
    if not wanted:
        return
    sequence = wanted + [n for n in range(1, len(slides) + 1) if n not in wanted]
    for position, number in enumerate(sequence):
        move_slide(prs, slides[number - 1], position)


def _slide_map(prs, numbers: dict[int, int]) -> list[int]:
    # Clones are inserted right after their source, so they inherit the last seen origin.
    result: list[int] = []
    last = 0
    for slide in prs.slides:
        last = numbers.get(slide.slide_id, last)
        result.append(last)
    return result


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
