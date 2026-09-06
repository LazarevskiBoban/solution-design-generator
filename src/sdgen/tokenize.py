from __future__ import annotations

from sdgen.content import Content
from sdgen.fill.table import fill_table
from sdgen.fill.text import replace_literal_everywhere, set_rich_text
from sdgen.inventory import find_shape
from sdgen.manifest import Binding, FieldSpec, Manifest


def placeholder(key: str) -> str:
    return "{{" + key + "}}"


def capture_content(prs, manifest: Manifest) -> Content:
    """Reads the current value of every text and table field, before the deck is tokenized."""
    slides = list(prs.slides)
    content = Content()
    for spec in manifest.fields:
        if spec.kind == "image":
            continue
        for binding in spec.bindings:
            if binding.mode == "token" or not 1 <= binding.slide <= len(slides):
                continue
            shape = find_shape(slides[binding.slide - 1], binding.shape.id)
            if shape is None:
                continue
            value = _table_rows(shape, spec, binding) if spec.kind == "table" else _text_value(shape, spec, binding)
            if value:
                content.fields[spec.key] = value
                break
    return content


def _text_value(shape, spec: FieldSpec, binding: Binding) -> str:
    if not getattr(shape, "has_text_frame", False):
        return ""
    prefix = (binding.keep_prefix or "").strip()
    lines = []
    for index, paragraph in enumerate(shape.text_frame.paragraphs):
        text = paragraph.text.strip()
        if index == 0 and prefix and text.startswith(prefix):
            text = text[len(prefix):].lstrip()
        if not text:
            continue
        lines.append("  " * paragraph.level + "- " + text if spec.kind == "bullets" else text)
    return "\n".join(lines)


def _table_rows(shape, spec: FieldSpec, binding: Binding) -> list[dict[str, str]]:
    if not getattr(shape, "has_table", False):
        return []
    rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
    header = binding.header_rows if binding.header_rows is not None else 1
    columns = list(spec.columns) or (rows[0] if rows else [])
    data = rows[header:]
    footer = (binding.keep_last_row_if or "").strip().lower()
    if footer and data and footer in data[-1][0].lower():
        data = data[:-1]
    return [dict(zip(columns, row)) for row in data if any(row)]


def tokenize_deck(prs, manifest: Manifest) -> Manifest:
    globals_ = []
    for spec in manifest.globals:
        marker = placeholder(spec.key)
        if spec.replaces != marker:
            replace_literal_everywhere(prs, spec.replaces, marker)
        globals_.append(spec.model_copy(update={"replaces": marker}))

    slides = list(prs.slides)
    for spec in manifest.fields:
        marker = placeholder(spec.key)
        for binding in spec.bindings:
            if binding.mode == "token" or spec.kind == "image" or spec.static:
                continue
            if not 1 <= binding.slide <= len(slides):
                continue
            shape = find_shape(slides[binding.slide - 1], binding.shape.id)
            if shape is None:
                continue
            if spec.kind == "table":
                if getattr(shape, "has_table", False):
                    fill_table(shape, [[marker]], header_rows=binding.header_rows, keep_last_row_if=binding.keep_last_row_if, settle=False)
            elif shape.has_text_frame:
                set_rich_text(shape, marker, keep_prefix=binding.keep_prefix)
    return manifest.model_copy(update={"globals": globals_})
