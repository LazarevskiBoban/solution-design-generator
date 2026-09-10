"""Reference material attached to a brief: pictures, pasted or uploaded text and links the model reads next to the brief."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field

from sdgen.references import cell_texts

MaterialKind = Literal["image", "text", "link"]
PER_ITEM_CHARS = 6000
TOTAL_CHARS = 24000
TEXT_FILE_MAX = 200_000
PAGE_MAX = 20_000
FETCH_BYTES_MAX = 2_000_000
IMAGE_BYTES_MAX = 4_000_000
IMAGE_TYPES = ("png", "jpg", "jpeg")
TEXT_TYPES = ("md", "txt", "mmd", "drawio", "csv", "xml", "json")


class Material(BaseModel):
    id: str
    kind: MaterialKind
    title: str = ""
    note: str = ""
    file: str = ""  # stored name under the design's material folder; empty for pasted text and links
    url: str = ""
    tags: list[str] = Field(default_factory=list)  # section keys it is about; empty applies everywhere
    text: str = ""  # what the prompts get: the transcript, the pasted text or the page snapshot
    status: str = ""
    added: str = ""

    @property
    def label(self) -> str:
        return self.title or self.file or self.url or self.kind

    @property
    def source(self) -> str:
        return self.url or self.file


def new_material(kind: MaterialKind, title: str = "", note: str = "", tags: Iterable[str] = (), **fields) -> Material:
    fields.setdefault("id", uuid4().hex[:8])
    fields.setdefault("added", now())
    return Material(kind=kind, title=title.strip(), note=note.strip(), tags=[t for t in tags if t], **fields)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def relevant(items: Iterable[Material], tags: set[str] | None = None) -> list[Material]:
    """The items for a call: those tagged for these sections first, then the untagged ones; items tagged only for other sections are left out."""
    items = list(items)
    if tags is None:
        return items
    wanted = set(tags)
    return [m for m in items if wanted & set(m.tags)] + [m for m in items if not m.tags]


def material_text(items: Iterable[Material], tags: set[str] | None = None, per_item: int = PER_ITEM_CHARS, total: int = TOTAL_CHARS) -> str:
    """The reference material block of a prompt; empty when there is nothing to show."""
    chosen = relevant(items, tags)
    parts: list[str] = []
    used = 0
    for index, item in enumerate(chosen):
        body = item.text.strip() or f"(no text yet: {item.status or 'nothing attached'})"
        if len(body) > per_item:
            body = body[:per_item].rstrip() + f"\n[... truncated, {len(body) - per_item} more characters]"
        head = f"## {item.label} ({item.kind}"
        if item.source and item.source != item.label:
            head += f", {item.source}"
        if item.tags:
            head += f"; for: {', '.join(item.tags)}"
        head += ")"
        block = "\n".join([head] + ([f"Note: {item.note}"] if item.note else []) + [body])
        if parts and used + len(block) > total:
            parts.append(f"[... {len(chosen) - index} more item(s) omitted]")
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def material_corpus(items: Iterable[Material]) -> str:
    """Every text in full, for checks that need to know which numbers the author supplied."""
    return "\n".join(m.text for m in items if m.text)


def load_material(path: str | Path) -> list[Material]:
    path = Path(path)
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Material.model_validate(item) for item in data.get("items") or []]


def save_material(items: Iterable[Material], path: str | Path) -> None:
    """Writes the list next to the brief, or removes the file when nothing is left."""
    path = Path(path)
    items = list(items)
    if not items:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": [m.model_dump(mode="json") for m in items]}
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")


def text_from_upload(name: str, data: bytes) -> str:
    """The text of an uploaded file: the block labels of a draw.io drawing, the content of anything else, capped."""
    suffix = Path(name).suffix.lower().lstrip(".")
    if suffix in ("drawio", "xml") and b"<mxfile" in data[:8192]:
        return "\n".join(cell_texts(data))
    text = data.decode("utf-8", errors="replace")
    if len(text) > TEXT_FILE_MAX:
        text = text[:TEXT_FILE_MAX] + f"\n[... truncated, {len(text) - TEXT_FILE_MAX} more characters]"
    return text


def mime_of(name: str) -> str:
    guess, _ = mimetypes.guess_type(name)
    return guess if guess in ("image/png", "image/jpeg") else ""
