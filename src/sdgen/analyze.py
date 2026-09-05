from __future__ import annotations

import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from sdgen.inventory import DeckInfo, ShapeInfo, SlideInfo
from sdgen.manifest import Binding, FieldKind, FieldSpec, GlobalSpec, Manifest, ShapeRef, SlideRules
from sdgen.textmetrics import FontSpec, capacity_chars

LONG_TEXT = 80
DIAGRAM_LONG_TEXT = 120
MULTI_PARAGRAPH_MIN = 40
LABEL_MAX = 60
PREFIX_MAX = 40
IMAGE_MIN_WIDTH = 2.0
SUBJECT_MIN_LEN = 8
SUBJECT_MIN_SLIDES = 3
DIAGRAM_MIN_CONNECTORS = 5
DIAGRAM_MIN_SHAPES = 25
DEFAULT_FONT_PT = 10.0
PLACEHOLDER_FONT_PT = 14.0

TOKEN_RE = re.compile(r"\{\{\s*[^{}]+?\s*\}\}|<[^<>\n]{3,80}>")
PREFIX_RE = re.compile(rf"^([A-Za-z][^:\n]{{1,{PREFIX_MAX}}}):\s+\S")
REPLACE_NOTE_RE = re.compile(r"replace with slide", re.IGNORECASE)
SUBJECT_SPLIT_RE = re.compile(r"[:|()–—-]")
GENERIC_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z :]*\d+$")

SKIP_PLACEHOLDERS = {"date", "footer", "slide_number", "title", "center_title"}
BODY_PLACEHOLDERS = {"body", "object", "subtitle"}

SlideKind = Literal["content", "diagram", "static"]


class Candidate(BaseModel):
    slide: int
    shape_id: int
    shape_name: str
    kind: FieldKind
    key: str
    label: str
    preview: str = ""
    confidence: float = 0.5
    include: bool = True
    reason: str = ""
    mode: Literal["replace", "token"] = "replace"
    token: str | None = None
    keep_prefix: str | None = None
    max_chars: int | None = None
    columns: list[str] = Field(default_factory=list)
    keep_last_row_if: str | None = None


class SlideClass(BaseModel):
    index: int
    kind: SlideKind
    reason: str = ""


class Analysis(BaseModel):
    globals: list[GlobalSpec] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    slides: list[SlideClass] = Field(default_factory=list)
    exclude: list[int] = Field(default_factory=list)

    def to_manifest(self, name: str, source: str = "template.pptx") -> Manifest:
        fields: dict[str, FieldSpec] = {}
        for cand in self.candidates:
            if not cand.include:
                continue
            key = cand.key
            existing = fields.get(key)
            if existing is not None and not _mergeable(existing, cand):
                base = f"{cand.key}_s{cand.slide}"
                key, suffix = base, 2
                while key in fields and not _mergeable(fields[key], cand):
                    key = f"{base}_{suffix}"
                    suffix += 1
                existing = fields.get(key)
            binding = Binding(
                slide=cand.slide,
                shape=ShapeRef(id=cand.shape_id, name=cand.shape_name),
                mode=cand.mode,
                token=cand.token,
                keep_prefix=cand.keep_prefix,
                max_chars=cand.max_chars,
                keep_last_row_if=cand.keep_last_row_if,
            )
            if existing:
                existing.bindings.append(binding)
            else:
                fields[key] = FieldSpec(
                    key=key,
                    label=cand.label,
                    kind=cand.kind,
                    guidance=_guidance(cand),
                    columns=cand.columns,
                    bindings=[binding],
                )
        return Manifest(
            name=name,
            source=source,
            globals=list(self.globals),
            fields=list(fields.values()),
            slides=SlideRules(exclude=sorted(self.exclude)),
        )


def analyze_deck(deck: DeckInfo) -> Analysis:
    analysis = Analysis(globals=_detect_subject(deck))
    for slide in deck.slides:
        diagram, reason = _looks_like_diagram(slide)
        candidates = _slide_candidates(slide, diagram)
        if diagram:
            kind: SlideKind = "diagram"
        elif any(c.include for c in candidates):
            kind, reason = "content", f"{sum(c.include for c in candidates)} fillable sections"
        else:
            kind, reason = "static", "no fillable text found"
        analysis.slides.append(SlideClass(index=slide.index, kind=kind, reason=reason))
        analysis.candidates.extend(candidates)
        if any(REPLACE_NOTE_RE.search(s.text) for s in slide.walk()):
            analysis.exclude.append(slide.index)
    return analysis


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:limit].rstrip("_")
    if not slug or not slug[0].isalpha():
        slug = f"field_{slug}".rstrip("_")
    return slug


def format_analysis(analysis: Analysis, show_all: bool = False) -> str:
    lines: list[str] = []
    for g in analysis.globals:
        lines.append(f"global  {g.key:<32} replaces {g.replaces!r}")
    kinds = Counter(s.kind for s in analysis.slides)
    lines.append(
        f"slides  content={kinds['content']} diagram={kinds['diagram']} static={kinds['static']}"
        + (f"  exclude={analysis.exclude}" if analysis.exclude else "")
    )
    lines.append("")
    lines.append(f"{'slide':>5} {'use':<3} {'kind':<7} {'key':<36} preview / reason")
    for c in analysis.candidates:
        if not (c.include or show_all):
            continue
        mark = "x" if c.include else "-"
        detail = c.preview if c.preview else c.reason
        lines.append(f"{c.slide:>5} {mark:<3} {c.kind:<7} {c.key:<36} {detail}")
    return "\n".join(lines)


def _looks_like_diagram(slide: SlideInfo) -> tuple[bool, str]:
    shapes, connectors = slide.shape_count, slide.connector_count
    if connectors >= DIAGRAM_MIN_CONNECTORS or (shapes > DIAGRAM_MIN_SHAPES and connectors > 0):
        return True, f"{shapes} shapes, {connectors} connectors"
    return False, ""


def _slide_candidates(slide: SlideInfo, diagram: bool) -> list[Candidate]:
    labels = [s for s in slide.shapes if _is_label(s)]
    title_label = _title_segment(slide)
    found: list[Candidate] = []
    used_labels: set[int] = set()
    for shape in slide.shapes:
        if shape.kind == "table":
            found.append(_table_candidate(slide, shape, labels, title_label, used_labels))
        elif shape.kind == "picture" and not diagram:
            if (shape.width or 0) >= IMAGE_MIN_WIDTH:
                found.append(_image_candidate(slide, shape, title_label))
        elif shape.kind == "text":
            found.extend(_text_candidates(slide, shape, labels, title_label, diagram, used_labels))
    return [c for c in found if c.shape_id not in used_labels]


def _text_candidates(
    slide: SlideInfo,
    shape: ShapeInfo,
    labels: list[ShapeInfo],
    title_label: str | None,
    diagram: bool,
    used_labels: set[int],
) -> list[Candidate]:
    if shape.placeholder_type in SKIP_PLACEHOLDERS:
        return []
    text = shape.text.strip()
    if not text:
        return []
    tokens = list(dict.fromkeys(TOKEN_RE.findall(text)))
    if tokens:
        return [_token_candidate(slide, shape, tok) for tok in tokens]

    is_body = shape.placeholder_type in BODY_PLACEHOLDERS
    if diagram:
        long_enough = len(text) >= DIAGRAM_LONG_TEXT
        if not long_enough:
            return []
    else:
        long_enough = len(text) >= LONG_TEXT or (
            len(shape.paragraphs) >= 2 and len(text) >= MULTI_PARAGRAPH_MIN
        )

    prefix = None
    first = shape.paragraphs[0].text if shape.paragraphs else ""
    match = PREFIX_RE.match(first)
    if match and len(first) - match.end(1) - 1 >= MULTI_PARAGRAPH_MIN:
        prefix = first[: match.end(1) + 1] + " "

    if prefix:
        label = match.group(1).strip()
        reason = "label kept as prefix"
    elif diagram:
        label, reason = f"{title_label or 'Diagram'} note", "long text on a diagram slide"
    elif _custom_name(shape):
        label, reason = shape.name.strip(), "named after the shape"
    elif is_body and len(shape.paragraphs) > 1 and len(shape.paragraphs[0].text) <= LABEL_MAX:
        label = shape.paragraphs[0].text.strip()
        reason = "body placeholder, first line used as name"
    else:
        near = _nearest_label(shape, labels)
        if near is not None:
            used_labels.add(near.id)
            label, reason = near.text.strip(), "named after nearby label"
        elif is_body:
            label, reason = title_label or "Body", "body placeholder"
        else:
            label, reason = title_label or " ".join(text.split()[:4]), "named after slide title"

    kind: FieldKind = "bullets" if any(p.has_bullet for p in shape.paragraphs) else "text"
    if is_body and len(shape.paragraphs) > 1 and kind == "text":
        kind = "bullets"
    # A box under its own label is content whatever its length; short text there is project text too.
    include = is_body or long_enough or reason == "named after nearby label"
    return [
        Candidate(
            slide=slide.index,
            shape_id=shape.id,
            shape_name=shape.name,
            kind=kind,
            key=slugify(label),
            label=label,
            preview=shape.preview,
            confidence=0.9 if is_body else (0.8 if long_enough else 0.3),
            include=include,
            reason=reason if include else "short text, left unticked",
            keep_prefix=prefix,
            max_chars=_max_chars(shape, len(prefix or "")),
        )
    ]


def _token_candidate(slide: SlideInfo, shape: ShapeInfo, token: str) -> Candidate:
    inner = token.strip("{}<> ").strip()
    return Candidate(
        slide=slide.index,
        shape_id=shape.id,
        shape_name=shape.name,
        kind="text",
        key=slugify(inner),
        label=inner,
        preview=shape.preview,
        confidence=0.95,
        include=True,
        reason=f"placeholder {token}",
        mode="token",
        token=token,
    )


def _table_candidate(
    slide: SlideInfo,
    shape: ShapeInfo,
    labels: list[ShapeInfo],
    title_label: str | None,
    used_labels: set[int],
) -> Candidate:
    table = shape.table
    header = table.cells[0] if table and table.cells else []
    columns = [" ".join(c.split()) or f"column_{i + 1}" for i, c in enumerate(header)]
    near = _nearest_label(shape, labels)
    if near is not None:
        used_labels.add(near.id)
        label = near.text.strip()
    else:
        label = title_label or shape.name
    keep_last = None
    if table and table.rows >= 3:
        last = [" ".join(c.split()) for c in table.cells[-1]]
        if last[0] and not any(last[1:]):
            keep_last = last[0]
    return Candidate(
        slide=slide.index,
        shape_id=shape.id,
        shape_name=shape.name,
        kind="table",
        key=slugify(label),
        label=label,
        preview=" | ".join(columns),
        confidence=0.9,
        include=True,
        reason=f"table {table.rows}x{table.cols}" if table else "table",
        columns=columns,
        keep_last_row_if=keep_last,
    )


def _image_candidate(slide: SlideInfo, shape: ShapeInfo, title_label: str | None) -> Candidate:
    label = f"{title_label or 'Slide ' + str(slide.index)} image"
    return Candidate(
        slide=slide.index,
        shape_id=shape.id,
        shape_name=shape.name,
        kind="image",
        key=slugify(label),
        label=label,
        confidence=0.4,
        include=False,
        reason=f"picture {shape.width:.1f} in wide, left unticked",
    )


def _custom_name(shape: ShapeInfo) -> bool:
    name = shape.name.strip()
    return 0 < len(name) <= PREFIX_MAX and not GENERIC_NAME_RE.match(name)


def _is_label(shape: ShapeInfo) -> bool:
    text = shape.text.strip()
    return (
        shape.kind == "text"
        and shape.placeholder_type not in SKIP_PLACEHOLDERS
        and len(shape.paragraphs) == 1
        and 0 < len(text) <= LABEL_MAX
        and not TOKEN_RE.search(text)
    )


def _nearest_label(shape: ShapeInfo, labels: list[ShapeInfo]) -> ShapeInfo | None:
    if not shape.has_geometry:
        return None
    best: tuple[float, ShapeInfo] | None = None
    for label in labels:
        if label.id == shape.id or not label.has_geometry:
            continue
        overlaps = label.left < shape.left + shape.width and shape.left < label.left + label.width
        above = label.top <= shape.top + 0.05
        if not (overlaps and above):
            continue
        distance = shape.top - label.top
        if best is None or distance < best[0]:
            best = (distance, label)
    return best[1] if best else None


def _title_segment(slide: SlideInfo) -> str | None:
    if not slide.title:
        return None
    segment = slide.title.split(":")[0].strip()
    return segment or None


def _max_chars(shape: ShapeInfo, prefix_len: int = 0) -> int | None:
    if not shape.has_geometry:
        return None
    size = next((p.font_size for p in shape.paragraphs if p.font_size), None)
    size = size or (PLACEHOLDER_FONT_PT if shape.placeholder_type else DEFAULT_FONT_PT)
    name = next((p.font_name for p in shape.paragraphs if p.font_name), None) or "Arial"
    bold = bool(next((p.bold for p in shape.paragraphs if p.bold is not None), False))
    spacing = next((p.line_spacing for p in shape.paragraphs if p.line_spacing), None) or 1.0
    after = next((p.space_after for p in shape.paragraphs if p.space_after), None) or 0.0
    # Measured against PowerPoint's default insets of 0.1 in left and right, 0.05 in top and bottom.
    return capacity_chars(shape.width * 72 - 14.4, shape.height * 72 - 7.2, FontSpec(name, size, bold), spacing * 100, after, prefix_len)


def _detect_subject(deck: DeckInfo) -> list[GlobalSpec]:
    counts: Counter[str] = Counter()
    for slide in deck.slides:
        if not slide.title:
            continue
        segments = {s.strip() for s in SUBJECT_SPLIT_RE.split(slide.title)}
        counts.update(s for s in segments if len(s) >= SUBJECT_MIN_LEN)
    if not counts:
        return []
    subject, hits = max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))
    if hits < SUBJECT_MIN_SLIDES:
        return []
    return [
        GlobalSpec(
            key="subject",
            label="Subject name (repeated in slide titles)",
            replaces=subject,
            guidance=f"Replaces '{subject}' wherever it appears in the deck ({hits} slide titles).",
        )
    ]


def _mergeable(existing: FieldSpec, cand: Candidate) -> bool:
    if existing.kind != cand.kind:
        return False
    if cand.mode == "token":
        return any(b.token == cand.token for b in existing.bindings)
    same_slide = any(b.slide == cand.slide for b in existing.bindings)
    same_shape = existing.label.lower() == cand.label.lower() and existing.columns == cand.columns
    return same_shape and not same_slide


def _guidance(cand: Candidate) -> str:
    if cand.mode == "token":
        return f"Replaces the placeholder {cand.token} on slide {cand.slide}."
    if cand.kind == "table":
        return "One row per entry. Columns: " + ", ".join(cand.columns) + "."
    if cand.kind == "image":
        return "PNG or JPG; fitted into the existing picture box."
    if cand.kind == "bullets":
        return "One bullet per line; indent with two spaces for sub-bullets."
    budget = f" About {cand.max_chars} characters fit." if cand.max_chars else ""
    return f"Free text; paragraphs separated by blank lines.{budget}"
