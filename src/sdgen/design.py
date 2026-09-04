from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from sdgen.brief import Brief, dump_brief, load_brief
from sdgen.content import Content, ImageValue, load_markdown
from sdgen.manifest import Manifest
from sdgen.mapping.model import MappingSet
from sdgen.registry import safe_name

DESIGN_FILE = "design.yaml"
BRIEF_FILE = "brief.md"
CONTENT_FILE = "content.md"
MAPPING_FILE = "mappings.yaml"
IMAGES_DIR = "images"
MAPPING_DIR = "mapping"


class Design(BaseModel):
    name: str
    template: str
    brief: Brief = Field(default_factory=Brief)
    content_markdown: str = ""
    images: dict[str, list[str]] = Field(default_factory=dict)
    mapping: MappingSet | None = None
    llm: str = ""
    updated: str = ""

    @property
    def workbook_name(self) -> str:
        return f"{safe_name(self.name)}-mapping.xlsx"


class DesignStore:
    def __init__(self, root: str | Path = "designs") -> None:
        self.root = Path(root)

    def names(self, template: str | None = None) -> list[str]:
        if not self.root.is_dir():
            return []
        names = []
        for folder in sorted(self.root.iterdir()):
            meta = folder / DESIGN_FILE
            if not meta.is_file():
                continue
            if template is not None:
                data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
                if data.get("template") != template:
                    continue
            names.append(folder.name)
        return names

    def delete(self, name: str) -> None:
        folder = self.root / name
        if not (folder / DESIGN_FILE).is_file():
            raise FileNotFoundError(f"design '{name}' not found under {self.root}")
        shutil.rmtree(folder)

    def load(self, name: str) -> Design:
        folder = self.root / name
        meta = folder / DESIGN_FILE
        if not meta.is_file():
            raise FileNotFoundError(f"design '{name}' not found under {self.root}")
        data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
        brief_path = folder / BRIEF_FILE
        content_path = folder / CONTENT_FILE
        mapping_path = folder / MAPPING_FILE
        return Design(
            name=name,
            template=data.get("template", ""),
            brief=load_brief(brief_path.read_text(encoding="utf-8")) if brief_path.is_file() else Brief(),
            content_markdown=content_path.read_text(encoding="utf-8") if content_path.is_file() else "",
            images={k: list(v) for k, v in (data.get("images") or {}).items()},
            mapping=MappingSet.load(mapping_path) if mapping_path.is_file() else None,
            llm=data.get("llm", ""),
            updated=data.get("updated", ""),
        )

    def save(self, design: Design) -> Path:
        folder = self.root / safe_name(design.name)
        folder.mkdir(parents=True, exist_ok=True)
        design.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {"template": design.template, "images": design.images, "llm": design.llm, "updated": design.updated}
        (folder / DESIGN_FILE).write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (folder / BRIEF_FILE).write_text(dump_brief(design.brief), encoding="utf-8")
        (folder / CONTENT_FILE).write_text(design.content_markdown, encoding="utf-8")
        if design.mapping is not None:
            design.mapping.save(folder / MAPPING_FILE)
        return folder

    def image_dir(self, name: str) -> Path:
        folder = self.root / safe_name(name) / IMAGES_DIR
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def mapping_dir(self, name: str) -> Path:
        folder = self.root / safe_name(name) / MAPPING_DIR
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def add_mapping_file(self, design: Design, file_name: str, data: bytes) -> Path:
        target = self.mapping_dir(design.name) / Path(file_name).name
        target.write_bytes(data)
        return target

    def workbook_path(self, design: Design) -> Path:
        return self.mapping_dir(design.name) / design.workbook_name

    def add_image(self, design: Design, field_key: str, file_name: str, data: bytes) -> str:
        folder = self.image_dir(design.name)
        target = folder / Path(file_name).name
        target.write_bytes(data)
        names = design.images.setdefault(field_key, [])
        if target.name not in names:
            names.append(target.name)
        return str(target)

    def content(self, design: Design, manifest: Manifest) -> Content:
        content = load_markdown(design.content_markdown, manifest) if design.content_markdown.strip() else Content()
        if design.brief.subject.strip():
            content.globals["subject"] = design.brief.subject.strip()
        folder = self.image_dir(design.name)
        for field_key, names in design.images.items():
            images = [ImageValue(path=str(folder / n)) for n in names if (folder / n).is_file()]
            if images:
                content.fields[field_key] = images[0] if len(images) == 1 else images
        return content
