from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from sdgen.brief import Brief, dump_brief, load_brief
from sdgen.content import Content, ImageValue, load_markdown
from sdgen.drawio import to_drawio
from sdgen.manifest import Manifest
from sdgen.flow import FlowSpec, to_mermaid
from sdgen.mapping.model import MappingSet
from sdgen.material import Material, load_material, mime_of, relevant, save_material
from sdgen.plan import SectionPlan
from sdgen.registry import safe_name

DESIGN_FILE = "design.yaml"
BRIEF_FILE = "brief.md"
MATERIAL_FILE = "material.yaml"
MATERIAL_DIR = "material"
CONTENT_FILE = "content.md"
MAPPING_FILE = "mappings.yaml"
PLAN_FILE = "plan.yaml"
DRAFT_FILE = "draft.md"
FLOWS_DIR = "flows"
IMAGES_DIR = "images"
MAPPING_DIR = "mapping"


class Design(BaseModel):
    name: str
    template: str
    brief: Brief = Field(default_factory=Brief)
    content_markdown: str = ""
    images: dict[str, list[str]] = Field(default_factory=dict)
    mapping: MappingSet | None = None
    modes: dict[str, str] = Field(default_factory=dict)
    hidden: list[str] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    titles: dict[str, str] = Field(default_factory=dict)
    plan: SectionPlan | None = None
    completed: list[str] = Field(default_factory=list)  # steps of the design page the user marked done
    diagram_format: str = "shapes"  # shapes, drawio or mermaid
    diagram_formats: dict[str, str] = Field(default_factory=dict)  # per-section overrides
    last_draft: str = ""  # the draft as the model returned it, to tell edited sections apart
    llm: str = ""
    updated: str = ""

    @property
    def workbook_name(self) -> str:
        return f"{safe_name(self.name)}-mapping.xlsx"


class DesignStore:
    def __init__(self, root: str | Path = "designs") -> None:
        self.root = Path(root)

    def names(self, template: str | None = None) -> list[str]:
        return [name for name, owner in self.templates().items() if template is None or owner == template]

    def templates(self) -> dict[str, str]:
        """The template every stored design was built on, by design name."""
        if not self.root.is_dir():
            return {}
        found = {}
        for folder in sorted(self.root.iterdir()):
            meta = folder / DESIGN_FILE
            if meta.is_file():
                data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
                found[folder.name] = str(data.get("template") or "")
        return found

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
        plan_path = folder / PLAN_FILE
        brief = load_brief(brief_path.read_text(encoding="utf-8")) if brief_path.is_file() else Brief()
        brief.material = load_material(folder / MATERIAL_FILE)
        return Design(
            name=name,
            template=data.get("template", ""),
            brief=brief,
            content_markdown=content_path.read_text(encoding="utf-8") if content_path.is_file() else "",
            images={k: list(v) for k, v in (data.get("images") or {}).items()},
            mapping=MappingSet.load(mapping_path) if mapping_path.is_file() else None,
            plan=SectionPlan.load(plan_path) if plan_path.is_file() else None,
            titles={str(k): str(v) for k, v in (data.get("titles") or {}).items()},
            diagram_format=str(data.get("diagram_format") or "shapes"),
            diagram_formats={str(k): str(v) for k, v in (data.get("diagram_formats") or {}).items()},
            last_draft=(folder / DRAFT_FILE).read_text(encoding="utf-8") if (folder / DRAFT_FILE).is_file() else "",
            modes={str(k): str(v) for k, v in (data.get("modes") or {}).items()},
            hidden=[str(k) for k in (data.get("hidden") or [])],
            order=[str(k) for k in (data.get("order") or [])],
            completed=[str(k) for k in (data.get("completed") or [])],
            llm=data.get("llm", ""),
            updated=data.get("updated", ""),
        )

    def save(self, design: Design) -> Path:
        folder = self.root / safe_name(design.name)
        folder.mkdir(parents=True, exist_ok=True)
        design.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {
            "template": design.template,
            "images": design.images,
            "modes": design.modes,
            "hidden": design.hidden,
            "order": design.order,
            "titles": design.titles,
            "completed": design.completed,
            "diagram_format": design.diagram_format,
            "diagram_formats": design.diagram_formats,
            "llm": design.llm,
            "updated": design.updated,
        }
        (folder / DESIGN_FILE).write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (folder / BRIEF_FILE).write_text(dump_brief(design.brief), encoding="utf-8")
        save_material(design.brief.material, folder / MATERIAL_FILE)
        (folder / CONTENT_FILE).write_text(design.content_markdown, encoding="utf-8")
        if design.mapping is not None:
            design.mapping.save(folder / MAPPING_FILE)
        if design.plan is not None:
            design.plan.save(folder / PLAN_FILE)
        if design.last_draft:
            (folder / DRAFT_FILE).write_text(design.last_draft, encoding="utf-8")
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

    def flow_dir(self, name: str) -> Path:
        folder = self.root / safe_name(name) / FLOWS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_flow(self, design: Design, section_key: str, spec: FlowSpec) -> Path:
        folder = self.flow_dir(design.name)
        target = folder / f"{section_key}.yaml"
        spec.save(target)
        (folder / f"{section_key}.mmd").write_text(to_mermaid(spec), encoding="utf-8")
        (folder / f"{section_key}.drawio").write_text(to_drawio(spec), encoding="utf-8")
        return target

    def flows(self, design: Design) -> dict[str, FlowSpec]:
        folder = self.root / safe_name(design.name) / FLOWS_DIR
        if not folder.is_dir():
            return {}
        return {path.stem: FlowSpec.load(path) for path in sorted(folder.glob("*.yaml"))}

    def delete_flow(self, design: Design, section_key: str) -> None:
        folder = self.root / safe_name(design.name) / FLOWS_DIR
        for suffix in (".yaml", ".mmd", ".drawio"):
            path = folder / f"{section_key}{suffix}"
            if path.is_file():
                path.unlink()

    def add_image(self, design: Design, field_key: str, file_name: str, data: bytes) -> str:
        folder = self.image_dir(design.name)
        target = folder / Path(file_name).name
        target.write_bytes(data)
        names = design.images.setdefault(field_key, [])
        if target.name not in names:
            names.append(target.name)
        return str(target)

    def material_dir(self, name: str) -> Path:
        folder = self.root / safe_name(name) / MATERIAL_DIR
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def material_path(self, design: Design, item: Material) -> Path | None:
        return self.material_dir(design.name) / item.file if item.file else None

    def add_material(self, design: Design, item: Material, data: bytes | None = None, file_name: str = "") -> Material:
        """Appends an item to the brief, storing its file under the item id when it has one."""
        if data is not None:
            item.file = f"{item.id}-{Path(file_name or item.file or item.title or 'file').name}"
            (self.material_dir(design.name) / item.file).write_bytes(data)
        design.brief.material.append(item)
        return item

    def remove_material(self, design: Design, item_id: str) -> None:
        kept = []
        for item in design.brief.material:
            if item.id != item_id:
                kept.append(item)
                continue
            path = self.material_path(design, item)
            if path is not None and path.is_file():
                path.unlink()
        design.brief.material = kept

    def material_images(self, design: Design, tags: set[str] | None = None, limit: int = 4) -> list[tuple[bytes, str]]:
        """The pictures a diagram planner may look at: tagged for these sections first, then untagged, newest first."""
        items = [m for m in relevant(design.brief.material, tags) if m.kind == "image" and m.file]
        newest = lambda group: sorted(group, key=lambda m: m.added, reverse=True)  # noqa: E731
        found: list[tuple[bytes, str]] = []
        for item in newest([m for m in items if m.tags]) + newest([m for m in items if not m.tags]):
            path = self.material_path(design, item)
            mime = mime_of(item.file)
            if path is not None and path.is_file() and mime:
                found.append((path.read_bytes(), mime))
            if len(found) >= limit:
                break
        return found

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
