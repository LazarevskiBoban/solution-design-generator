from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from sdgen.analyze import Analysis, slugify
from sdgen.inventory import DeckInfo, ShapeInfo
from sdgen.manifest import FieldSpec, Manifest

SectionKind = Literal["cover", "static", "divider", "text", "table", "composite", "diagram", "mapping", "references"]
WRITABLE_KINDS = {"text", "table", "composite", "mapping", "references", "diagram"}
EXAMPLE_LIMIT = 1500
DIVIDER_MAX_SHAPES = 5
COVER_MAX_FIELDS = 2
HEADING_MAX_TOP = 1.2
HEADING_MAX_CHARS = 60
MAPPING_RE = re.compile(r"mapping", re.IGNORECASE)
REFERENCE_RE = re.compile(r"reference", re.IGNORECASE)
STATIC_TITLE_RE = re.compile(r"\b(contents|agenda|table of contents|guiding principles)\b", re.IGNORECASE)
MARKER_RE = re.compile(r"\{\{\s*[^{}]+?\s*\}\}")
TITLE_MAX = 60


class Section(BaseModel):
    key: str
    title: str
    kind: SectionKind
    slide: int
    fields: list[str] = Field(default_factory=list)
    ask: str = ""
    example: str = ""
    optional: bool = False
    images: int = 0
    generated: bool = False  # filled from a drawing, never written by the model

    @property
    def writable(self) -> bool:
        return self.kind in WRITABLE_KINDS and bool(self.fields)


class Blueprint(BaseModel):
    name: str
    subject_key: str = "subject"
    sections: list[Section] = Field(default_factory=list)

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    def for_slide(self, slide: int) -> Section | None:
        return next((s for s in self.sections if s.slide == slide), None)

    def save(self, path: str | Path) -> None:
        data = self.model_dump(mode="json", exclude_defaults=True)
        Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Blueprint:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def derive_blueprint(deck: DeckInfo, analysis: Analysis, manifest: Manifest, name: str) -> Blueprint:
    subject = next((g.replaces for g in manifest.globals if g.key == "subject"), None)
    if subject is None and manifest.globals:
        subject = manifest.globals[0].replaces
    excluded = set(manifest.slides.exclude) | set(analysis.exclude)
    classes = {s.index: s.kind for s in analysis.slides}
    shapes = {(slide.index, shape.id): shape for slide in deck.slides for shape in slide.walk()}
    used_keys: set[str] = set()
    sections: list[Section] = []

    for slide in deck.slides:
        if slide.index in excluded:
            continue
        fields = [f for f in manifest.fields if any(b.slide == slide.index for b in f.bindings)]
        bound = {b.shape.id for f in fields for b in f.bindings if b.slide == slide.index}
        title = clean_title(slide.title or _heading_fallback(slide, bound), subject) or f"Slide {slide.index}"
        kind = _kind(slide.index, title, fields, classes.get(slide.index, "content"), slide.shape_count)
        key = _unique(slugify(title), used_keys)
        sections.append(
            Section(
                key=key,
                title=title,
                kind=kind,
                slide=slide.index,
                fields=[f.key for f in fields],
                ask=_ask(kind, fields),
                example=_example(slide.index, fields, shapes),
                images=1 if kind == "diagram" else 0,
            )
        )
    return Blueprint(name=name, sections=sections)


def mark_static_fields(manifest: Manifest, blueprint: Blueprint) -> Manifest:
    """Flags fields that live only on static or divider slides, so their template content is left untouched."""
    slides = {s.slide for s in blueprint.sections if s.kind in ("static", "divider")}
    fields = []
    for spec in manifest.fields:
        replace = [b for b in spec.bindings if b.mode == "replace"]
        static = spec.kind != "image" and bool(replace) and all(b.slide in slides for b in replace) and all(b.mode == "replace" for b in spec.bindings)
        fields.append(spec.model_copy(update={"static": static}) if static != spec.static else spec)
    return manifest.model_copy(update={"fields": fields})


def clean_title(title: str | None, subject: str | None) -> str:
    if not title:
        return ""
    text = " ".join(title.split())
    if subject:
        text = text.replace(" ".join(subject.split()), " ")
    text = MARKER_RE.sub(" ", text)
    text = " ".join(text.split())
    text = re.sub(r":\s+(?=[(|])", " ", text)
    return text.strip(" :|-–—")


def composite_fields(blueprint: Blueprint, manifest: Manifest) -> list[str]:
    """Fields whose box may hold only a summary: on a composite section or on a slide with several written fields."""
    keys: list[str] = []
    for section in blueprint.sections:
        if section.kind in ("cover", "static", "divider"):
            continue
        specs = [f for f in (manifest.field(k) for k in section.fields) if f is not None and f.kind != "image" and not f.static and any(b.mode == "replace" for b in f.bindings)]
        if section.kind == "composite" or len(specs) >= 2:
            keys.extend(f.key for f in specs if f.key not in keys)
    return keys


def cap_title(title: str | None, limit: int = TITLE_MAX) -> str:
    """A slide title of at most `limit` characters, cut at a word boundary."""
    text = " ".join((title or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    if text[limit] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" :,;-–—")


def _heading_fallback(slide, bound: set[int]) -> str | None:
    short = [
        s
        for s in slide.shapes
        if s.kind == "text"
        and s.id not in bound
        and s.has_geometry
        and 0 < len(s.text.strip()) <= HEADING_MAX_CHARS
        and s.placeholder_type not in ("date", "footer", "slide_number")
    ]
    candidates = [s for s in short if s.top <= HEADING_MAX_TOP]
    if not candidates and not bound:
        candidates = short
    if not candidates:
        return None
    best = max(candidates, key=lambda s: ((s.paragraphs[0].font_size or 0) if s.paragraphs else 0, -s.top))
    return best.text.strip()


def format_outline(blueprint: Blueprint) -> str:
    lines = [f"Template '{blueprint.name}': {len(blueprint.sections)} sections"]
    for s in blueprint.sections:
        flag = " (optional)" if s.optional else ""
        ask = f"  ask: {s.ask}" if s.ask else ""
        lines.append(f"{s.slide:>3}  {s.kind:<10} {s.title}{flag}{ask}")
    return "\n".join(lines)


def _kind(index: int, title: str, fields: list[FieldSpec], slide_class: str, shape_count: int) -> SectionKind:
    written = [f for f in fields if f.kind != "image"]  # a picture slot does not change what a slide is
    if index == 1 and len(written) <= COVER_MAX_FIELDS and all(f.kind in ("text", "bullets") for f in written):
        return "cover"
    if STATIC_TITLE_RE.search(title):
        return "static"
    if slide_class == "diagram":
        return "diagram"
    if not fields:
        return "divider" if shape_count <= DIVIDER_MAX_SHAPES else "static"
    if MAPPING_RE.search(title):
        return "mapping"
    if REFERENCE_RE.search(title):
        return "references"
    if not written:
        return "diagram"
    if len(written) == 1:
        return "table" if written[0].kind == "table" else "text"
    return "composite"


def _ask(kind: SectionKind, fields: list[FieldSpec]) -> str:
    parts = []
    if kind == "diagram":
        parts.append("One or more diagram images (PNG or JPG)")
    if kind == "cover":
        parts.append("Subject name")
    for f in fields:
        if f.kind == "table":
            parts.append(f"{f.label} (table: {', '.join(f.columns)})" if f.columns else f"{f.label} (table)")
        elif f.kind == "image":
            parts.append(f"{f.label} (image)")
        else:
            parts.append(f"{f.label} ({f.kind})")
    return "; ".join(parts)


def _example(slide: int, fields: list[FieldSpec], shapes: dict[tuple[int, int], ShapeInfo]) -> str:
    chunks: list[str] = []
    for f in fields:
        for b in f.bindings:
            if b.slide != slide:
                continue
            shape = shapes.get((slide, b.shape.id))
            if shape is None:
                continue
            if shape.table:
                rows = [" | ".join(" ".join(c.split()) for c in row) for row in shape.table.cells if any(c.strip() for c in row)]
                text = "\n".join(rows)
            else:
                text = shape.text.strip()
            if text:
                chunks.append(f"{f.label}: {text}")
    example = "\n".join(chunks)
    return example[:EXAMPLE_LIMIT]


def _unique(key: str, used: set[str]) -> str:
    candidate, n = key, 2
    while candidate in used:
        candidate = f"{key}_{n}"
        n += 1
    used.add(candidate)
    return candidate
