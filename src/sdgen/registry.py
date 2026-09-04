from __future__ import annotations

import re
import shutil
from pathlib import Path

from pptx import Presentation
from pydantic import BaseModel

from sdgen.blueprint import Blueprint
from sdgen.content import Content, dump_markdown, load_markdown_file
from sdgen.diagrams import add_image_slots
from sdgen.manifest import Manifest
from sdgen.tokenize import capture_content, tokenize_deck

MANIFEST_FILE = "manifest.yaml"
BLUEPRINT_FILE = "blueprint.yaml"
TEMPLATE_FILE = "template.pptx"
ORIGINAL_FILE = "original.md"
NAME_RE = re.compile(r"[^a-z0-9_-]+")


def safe_name(name: str) -> str:
    cleaned = NAME_RE.sub("-", name.strip().lower()).strip("-")
    if not cleaned:
        raise ValueError("template name must contain letters or digits")
    return cleaned


class TemplateEntry(BaseModel):
    name: str
    directory: Path
    manifest: Manifest
    blueprint: Blueprint | None = None
    original: Content | None = None

    @property
    def template_path(self) -> Path:
        return self.directory / self.manifest.source


class Registry:
    def __init__(self, root: str | Path = "templates") -> None:
        self.root = Path(root)

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / MANIFEST_FILE).is_file())

    def remove(self, name: str) -> None:
        directory = self.root / name
        if not (directory / MANIFEST_FILE).is_file():
            raise FileNotFoundError(f"template '{name}' not found under {self.root}")
        shutil.rmtree(directory)

    def load(self, name: str) -> TemplateEntry:
        directory = self.root / name
        manifest_path = directory / MANIFEST_FILE
        if not manifest_path.is_file():
            raise FileNotFoundError(f"template '{name}' not found under {self.root}")
        blueprint_path = directory / BLUEPRINT_FILE
        blueprint = Blueprint.load(blueprint_path) if blueprint_path.is_file() else None
        manifest = Manifest.load(manifest_path)
        original_path = directory / ORIGINAL_FILE
        original = load_markdown_file(original_path, manifest) if original_path.is_file() else None
        return TemplateEntry(name=name, directory=directory, manifest=manifest, blueprint=blueprint, original=original)

    def add(
        self,
        name: str,
        deck_path: str | Path,
        manifest: Manifest,
        tokenize: bool = True,
        blueprint: Blueprint | None = None,
    ) -> TemplateEntry:
        name = safe_name(name)
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        manifest = manifest.model_copy(update={"name": name, "source": TEMPLATE_FILE})
        original = None
        if tokenize:
            prs = Presentation(str(deck_path))
            original = capture_content(prs, manifest)
            manifest = tokenize_deck(prs, manifest)
            if blueprint is not None:
                manifest, blueprint = add_image_slots(prs, manifest, blueprint)
            prs.save(str(directory / TEMPLATE_FILE))
            (directory / ORIGINAL_FILE).write_text(dump_markdown(original, manifest), encoding="utf-8")
        else:
            shutil.copyfile(deck_path, directory / TEMPLATE_FILE)
        manifest.save(directory / MANIFEST_FILE)
        if blueprint is not None:
            blueprint = blueprint.model_copy(update={"name": name})
            blueprint.save(directory / BLUEPRINT_FILE)
        return TemplateEntry(name=name, directory=directory, manifest=manifest, blueprint=blueprint, original=original)

    def save_blueprint(self, name: str, blueprint: Blueprint) -> None:
        directory = self.root / name
        if not directory.is_dir():
            raise FileNotFoundError(f"template '{name}' not found under {self.root}")
        blueprint.save(directory / BLUEPRINT_FILE)

    def save(self, name: str, manifest: Manifest) -> None:
        directory = self.root / name
        if not directory.is_dir():
            raise FileNotFoundError(f"template '{name}' not found under {self.root}")
        manifest.save(directory / MANIFEST_FILE)
