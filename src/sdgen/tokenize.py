from __future__ import annotations

from sdgen.fill.table import fill_table
from sdgen.fill.text import replace_literal_everywhere, set_rich_text
from sdgen.inventory import find_shape
from sdgen.manifest import Manifest


def placeholder(key: str) -> str:
    return "{{" + key + "}}"


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
            if binding.mode == "token" or spec.kind == "image":
                continue
            if not 1 <= binding.slide <= len(slides):
                continue
            shape = find_shape(slides[binding.slide - 1], binding.shape.id)
            if shape is None:
                continue
            if spec.kind == "table":
                if getattr(shape, "has_table", False):
                    fill_table(shape, [[marker]], header_rows=binding.header_rows, keep_last_row_if=binding.keep_last_row_if)
            elif shape.has_text_frame:
                set_rich_text(shape, marker, keep_prefix=binding.keep_prefix)
    return manifest.model_copy(update={"globals": globals_})
