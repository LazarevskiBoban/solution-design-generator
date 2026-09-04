from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from sdgen.analyze import slugify
from sdgen.manifest import FieldSpec, GlobalSpec, Manifest

FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

FORMAT_HELP = (
    "One paragraph per line. Start a line with \"- \" for a bullet and indent two spaces per level. "
    "Use **bold** for emphasis. Tables are pipe tables with the given columns (use <br> for a line "
    "break inside a cell). Images are written as ![](path/to/file.png). Leave a section empty to skip it."
)


class ImageValue(BaseModel):
    path: str


FieldValue = str | list[dict[str, Any]] | ImageValue | list[ImageValue]


def images_of(value: Any) -> list[ImageValue]:
    if isinstance(value, ImageValue):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, ImageValue)]
    if isinstance(value, str) and value.strip():
        return [ImageValue(path=value.strip())]
    return []


class Content(BaseModel):
    globals: dict[str, str] = Field(default_factory=dict)
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    unknown: list[str] = Field(default_factory=list)


def load_markdown(text: str, manifest: Manifest | None = None, base_dir: str | Path | None = None) -> Content:
    front, body = _split_front_matter(text)
    content = Content(globals={str(k): _scalar(v) for k, v in front.items()})
    for heading, section in _sections(body):
        spec = _match_field(heading, manifest)
        if spec is None and manifest is not None:
            global_spec = _match_global(heading, manifest)
            if global_spec is not None:
                content.globals[global_spec.key] = " ".join(section.split())
            else:
                content.unknown.append(heading)
            continue
        key = spec.key if spec else slugify(heading)
        kind = spec.kind if spec else _guess_kind(section)
        content.fields[key] = _parse_value(kind, section, base_dir)
    return content


def parse_markdown_sections(text: str) -> tuple[dict, list[tuple[str, str]]]:
    front, body = _split_front_matter(text)
    return front, _sections(body)


def load_markdown_file(path: str | Path, manifest: Manifest | None = None) -> Content:
    path = Path(path)
    return load_markdown(path.read_text(encoding="utf-8"), manifest, base_dir=path.parent)


def dump_markdown(content: Content, manifest: Manifest | None = None) -> str:
    lines: list[str] = []
    if content.globals:
        lines.append("---")
        lines.append(yaml.safe_dump(content.globals, sort_keys=False, allow_unicode=True).rstrip())
        lines.append("---")
        lines.append("")
    ordered = [f.key for f in manifest.fields] if manifest else []
    ordered += [k for k in content.fields if k not in ordered]
    for key in ordered:
        if key not in content.fields:
            continue
        spec = manifest.field(key) if manifest else None
        lines.append(f"## {key}")
        lines.append(_format_value(content.fields[key], spec))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def skeleton_markdown(manifest: Manifest) -> str:
    lines: list[str] = []
    if manifest.globals:
        lines.append("---")
        for g in manifest.globals:
            note = g.label or g.key
            lines.append(f'{g.key}: ""  # {note}. Replaces "{g.replaces}".')
        lines.append("---")
        lines.append("")
    lines.append(f"<!-- {FORMAT_HELP} -->")
    lines.append("")
    for spec in manifest.fields:
        slides = ", ".join(str(b.slide) for b in spec.bindings)
        lines.append(f"## {spec.key}")
        lines.append(f"<!-- {spec.label} ({spec.kind}, slide {slides}). {spec.guidance} -->".replace("  ", " "))
        if spec.kind == "table" and spec.columns:
            lines.append(_pipe_table([], spec.columns, blank_row=True))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def validate_content(content: Content, manifest: Manifest) -> list[str]:
    warnings: list[str] = []
    for g in manifest.globals:
        if not content.globals.get(g.key):
            warnings.append(f"global '{g.key}' has no value; '{g.replaces}' stays in the document")
    for spec in manifest.fields:
        value = content.fields.get(spec.key)
        if value is None or value == "" or value == []:
            warnings.append(f"field '{spec.key}' ({spec.label}) is empty")
            continue
        if spec.kind == "table":
            if not isinstance(value, list):
                warnings.append(f"field '{spec.key}' expects table rows but got text")
            elif spec.columns:
                known = {_norm(c) for c in spec.columns}
                extra = sorted({k for row in value for k in row if _norm(k) not in known})
                if extra:
                    warnings.append(f"field '{spec.key}' has columns not in the template: {', '.join(extra)}")
        elif spec.kind == "image":
            for image in images_of(value):
                if not Path(image.path).is_file():
                    warnings.append(f"field '{spec.key}' image not found: {image.path}")
        elif images_of(value) and not isinstance(value, str):
            warnings.append(f"field '{spec.key}' expects text but got an image")
    for heading in content.unknown:
        warnings.append(f"section '{heading}' does not match any field and is ignored")
    return warnings


def parse_pipe_table(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if not lines:
        return []
    header = _cells(lines[0])
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = _cells(line)
        if cells and all(SEPARATOR_CELL_RE.match(c) for c in cells if c) and any(cells):
            continue
        rows.append({header[i]: cells[i] if i < len(cells) else "" for i in range(len(header))})
    return rows


def _split_front_matter(text: str) -> tuple[dict, str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    return (data if isinstance(data, dict) else {}), text[match.end():]


def _sections(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    in_fence = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else HEADING_RE.match(line)
        if match:
            sections.append((match.group(1), []))
        elif sections:
            sections[-1][1].append(line)
    return [(heading, "\n".join(lines).strip("\n")) for heading, lines in sections]


def _match_field(heading: str, manifest: Manifest | None) -> FieldSpec | None:
    if manifest is None:
        return None
    wanted = slugify(heading)
    for spec in manifest.fields:
        if spec.key == heading.strip() or slugify(spec.key) == wanted or slugify(spec.label) == wanted:
            return spec
    return None


def _match_global(heading: str, manifest: Manifest) -> GlobalSpec | None:
    wanted = slugify(heading)
    for spec in manifest.globals:
        if spec.key == heading.strip() or slugify(spec.key) == wanted or (spec.label and slugify(spec.label) == wanted):
            return spec
    return None


def _guess_kind(section: str) -> str:
    if parse_pipe_table(section):
        return "table"
    if IMAGE_RE.search(section):
        return "image"
    return "text"


def _parse_value(kind: str, section: str, base_dir: str | Path | None) -> FieldValue:
    section = _strip_comments(section)
    if kind == "table":
        return parse_pipe_table(section)
    if kind == "image":
        paths = [m.strip() for m in IMAGE_RE.findall(section)] or ([section.strip()] if section.strip() else [])
        images = []
        for raw in paths:
            if base_dir is not None and not Path(raw).is_absolute():
                raw = str(Path(base_dir) / raw)
            images.append(ImageValue(path=raw))
        if not images:
            return ""
        return images[0] if len(images) == 1 else images
    return section


def strip_comments(section: str) -> str:
    return re.sub(r"<!--.*?-->", "", section, flags=re.DOTALL).strip("\n")


_strip_comments = strip_comments


def _format_value(value: FieldValue, spec: FieldSpec | None) -> str:
    images = images_of(value) if not isinstance(value, str) else []
    if images:
        return "\n".join(f"![]({image.path})" for image in images)
    if isinstance(value, list):
        columns = list(spec.columns) if spec and spec.columns else _union_keys(value)
        return _pipe_table(value, columns)
    return str(value)


def _pipe_table(rows: list[dict[str, Any]], columns: list[str], blank_row: bool = False) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lookup_rows = [{_norm(k): v for k, v in row.items()} for row in rows]
    for row in lookup_rows:
        cells = [_cell_text(row.get(_norm(c), "")) for c in columns]
        lines.append("| " + " | ".join(cells) + " |")
    if blank_row:
        lines.append("| " + " | ".join("" for _ in columns) + " |")
    return "\n".join(lines)


def _cell_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [BREAK_RE.sub("\n", c.strip().replace("\\|", "|")) for c in CELL_SPLIT_RE.split(line)]


def _union_keys(rows: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    return keys


def _scalar(value: Any) -> str:
    return "" if value is None else str(value)


def _norm(text: str) -> str:
    return " ".join(str(text).split()).lower()
