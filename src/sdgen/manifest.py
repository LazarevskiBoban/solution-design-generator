from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

FieldKind = Literal["text", "bullets", "table", "image"]
BindingMode = Literal["replace", "token"]
ImageFit = Literal["contain", "cover"]


class ShapeRef(BaseModel):
    id: int
    name: str | None = None


class Binding(BaseModel):
    slide: int
    shape: ShapeRef
    mode: BindingMode = "replace"
    token: str | None = None
    keep_prefix: str | None = None
    max_chars: int | None = None
    header_rows: int = 1
    keep_last_row_if: str | None = None
    fit: ImageFit = "contain"


class FieldSpec(BaseModel):
    key: str
    label: str
    kind: FieldKind = "text"
    guidance: str = ""
    columns: list[str] = Field(default_factory=list)
    bindings: list[Binding] = Field(default_factory=list)


class GlobalSpec(BaseModel):
    key: str
    label: str = ""
    replaces: str
    guidance: str = ""


class SlideRules(BaseModel):
    exclude: list[int] = Field(default_factory=list)
    prototypes: dict[str, int] = Field(default_factory=dict)


class Manifest(BaseModel):
    name: str
    source: str = "template.pptx"
    globals: list[GlobalSpec] = Field(default_factory=list)
    fields: list[FieldSpec] = Field(default_factory=list)
    slides: SlideRules = Field(default_factory=SlideRules)

    @model_validator(mode="after")
    def _unique_keys(self) -> Manifest:
        keys = [g.key for g in self.globals] + [f.key for f in self.fields]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ValueError(f"duplicate keys in manifest: {', '.join(duplicates)}")
        return self

    @property
    def keys(self) -> list[str]:
        return [g.key for g in self.globals] + [f.key for f in self.fields]

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)

    def save(self, path: str | Path) -> None:
        data = self.model_dump(mode="json", exclude_defaults=True)
        Path(path).write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
